"""Isolated bridge round-trip and HTTP operation routing regressions."""
import json
from pathlib import Path
import sys
import threading
import unittest
from collections import deque
from types import SimpleNamespace

import sandbox
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ext.sessiond import bridge
from ext.sessiond.rpc import api


class ChatBackend(unittest.TestCase):
    def make_bridge(self, respond=True, success=True):
        b = bridge.Bridge.__new__(bridge.Bridge)
        b.lock = threading.Lock()
        b.cond = threading.Condition(b.lock)
        b.pending = {}
        b.pending_dialogs = {}
        b._dialog_deadline = {}
        b._req_seq = 0
        b.ns = 'fixture'
        b.seq = 0
        b.ring = deque(maxlen=20)
        sent = []
        def send(cmd):
            sent.append(cmd)
            if respond:
                b._ingest({'type': 'response', 'id': cmd['id'], 'command': cmd['type'],
                           'success': success, 'data': {'steering': ['actual'], 'followUp': []},
                           'error': 'fixture rejection' if not success else None})
            return True
        b.sup = SimpleNamespace(ensure_started=lambda: None, wait_ready=lambda: True,
                                send=send, state='running')
        return b, sent

    def test_real_receipt_not_write_ack(self):
        b, sent = self.make_bridge()
        r = b.request({'type': 'clear_queue'})
        self.assertTrue(r['ok'])
        self.assertEqual(r['data'], {'steering': ['actual'], 'followUp': []})
        self.assertEqual(sent[0]['type'], 'clear_queue')
        self.assertFalse(b.pending)

    def test_first_request_registers_after_startup_generation_event(self):
        b, sent = self.make_bridge()
        starting = [True]
        def start():
            if starting[0]:
                starting[0] = False
                b._ingest({'type': 'sessiond.session_restarted', 'gen': 1})
        b.sup.ensure_started = start
        r = b.request({'type': 'clear_queue'})
        self.assertTrue(r['ok'], r)
        self.assertEqual(r['data']['steering'], ['actual'])
        self.assertFalse(b.pending)

    def test_rejection_and_timeout(self):
        b, _ = self.make_bridge(success=False)
        self.assertFalse(b.request({'type': 'clear_queue'})['ok'])
        b, _ = self.make_bridge(respond=False)
        r = b.request({'type': 'steer', 'message': 'recover me'}, timeout=.001)
        self.assertFalse(r['ok'])
        self.assertIn('unknown', r['error'])
        self.assertFalse(b.pending)

    def test_old_generation_without_chat_command_never_receives_prompt(self):
        b, sent = self.make_bridge()
        r = b.chat_action('state')
        self.assertFalse(r['ok'])
        self.assertIn('extension unavailable', r['error'])
        self.assertEqual([c['type'] for c in sent], ['get_commands'])

    def test_new_http_ops_reach_bridge(self):
        calls = []
        b = SimpleNamespace(
            request=lambda c: calls.append(c) or {'ok': True, 'data': {'steering': ['actual'], 'followUp': []}},
            chat_action=lambda a, p=None: calls.append((a, p)) or {'ok': True, 'state': 'ready'},
            pending_dialog_list=lambda: [])
        original = api._bridge_or_err
        api._bridge_or_err = lambda *_: (b, None)
        try:
            for op, kw in [('queue_rpc', {'action': 'clear_queue'}), ('queue_rpc', {'action': 'steer', 'message': 'text'}),
                           ('chat_state', {}), ('regenerate', {'entryId': 'stable', 'message': 'new', 'images': '[]'})]:
                response = api.interp(None, op=op, session='/fixture.agent', **kw)
                self.assertTrue(json.loads(response[1])['ok'], response)
            self.assertEqual(calls[0], {'type': 'clear_queue'})
            self.assertEqual(calls[1], {'type': 'steer', 'message': 'text'})
            self.assertEqual(calls[-1], ('regenerate', {'entryId': 'stable', 'message': 'new', 'images': []}))
            self.assertFalse(json.loads(api.interp(None, op='queue_rpc', session='/fixture.agent', action='abort')[1])['ok'])
        finally:
            api._bridge_or_err = original


if __name__ == '__main__':
    unittest.main()
