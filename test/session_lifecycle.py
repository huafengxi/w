#!/usr/bin/env python3
"""Isolated lifecycle, declaration, event-gap and stream-budget regressions.

Run: ~/miniconda3/bin/python3 w/test/session_lifecycle.py
All subprocesses and workspace files live in a self-created temporary directory.
"""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from collections import deque
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ext.sessiond import proc as P, bridge as B
from ext.sessiond.rpc import api, stream
from core import wsgi
from core.stream import ClosingIterator
from stores.store import RootStore, build_root_store, StoreException
from stores.dir_store import DirStore


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='session-lifecycle-')
        self.root = Path(self.temp.name).resolve()
        self.patchers = [patch.object(P, 'WS', str(self.root)),
                         patch.object(P, 'WS_REAL', str(self.root)),
                         patch.object(P, 'find_hosts', return_value=[]),
                         patch.object(B, '_ensure_idle_reaper'),
                         patch.object(B, '_BRIDGES', {})]
        for patcher in self.patchers:
            patcher.start()
        self.processes = []
        self.events = []
        self.sup = P.Supervisor('/session.jsonl', self.events.append, cwd=str(self.root))
        self.helper = self.root / 'host.py'
        self.helper.write_text('''import json,sys
for line in sys.stdin:
    obj=json.loads(line)
    if obj.get('type') == 'exit': break
    print(json.dumps({'type':'response','id':obj.get('id'),'command':obj['type'],
                      'success':True,'data':{'entries':[],'commands':[]}}), flush=True)
''')
        real_popen = subprocess.Popen
        def popen(argv, **kwargs):
            proc = real_popen([sys.executable, '-u', str(self.helper)], **kwargs)
            self.processes.append(proc)
            return proc
        self.popen = patch.object(P.subprocess, 'Popen', side_effect=popen)
        self.popen.start()

    def tearDown(self):
        self.sup.shutdown('test cleanup')
        for proc in self.processes:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
        self.popen.stop()
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def start(self):
        self.sup.ensure_started()
        self.assertTrue(self.sup.wait_ready(2))

    def test_exit_does_not_restart(self):
        self.start()
        self.sup.send({'type': 'exit'})
        deadline = time.monotonic() + 2
        while self.sup.state != 'dormant' and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(self.sup.state, 'dormant')
        self.assertEqual(len(self.processes), 1)
        self.start()
        self.assertEqual(len(self.processes), 2)

    def test_shutdown_during_start_is_permanent(self):
        entered, release = threading.Event(), threading.Event()
        def clear_stale():
            entered.set()
            self.assertTrue(release.wait(2))
        with patch.object(self.sup, '_clear_stale_proc', side_effect=clear_stale):
            self.sup.ensure_started()
            self.assertTrue(entered.wait(2))
            result = []
            closer = threading.Thread(target=lambda: result.append(self.sup.shutdown()))
            closer.start()
            deadline = time.monotonic() + 2
            while not self.sup._closed and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(self.sup._closed)
            release.set()
            closer.join(2)
            self.assertFalse(closer.is_alive())
        self.assertTrue(result[0]['ok'])
        self.sup.ensure_started()
        self.assertFalse(self.sup.wait_ready(.05))
        self.assertEqual(self.sup.state, 'closed')
        self.assertEqual(len(self.processes), 0)

    def test_reload_clear_and_concurrent_delete(self):
        self.start()
        session = Path(self.sup.session_file)
        session.write_text('history')
        self.assertTrue(self.sup.reload()['ok'])
        self.assertEqual(session.read_text(), 'history')
        self.assertTrue(self.sup.clear()['ok'])
        self.assertEqual(session.read_text(), '')
        self.assertEqual(self.sup.gen, 3)
        self.assertTrue(all(p.poll() is not None for p in self.processes[:-1]))
        self.assertTrue(self.sup.shutdown()['ok'])
        self.assertFalse(self.sup.reload()['ok'])
        self.assertFalse(self.sup.clear()['ok'])
        self.assertFalse(self.sup.send({'type': 'prompt'}))

    def test_queued_start_does_not_duplicate_reload_generation(self):
        self.start()
        # A request queued during the dormant interval of an explicit reload.
        with self.sup._op_lock:
            self.sup._stop_locked()
            self.sup.ensure_started()
            self.assertTrue(self.sup._start_locked()["ok"])
        self.sup._thread.join(2)
        self.assertFalse(self.sup._thread.is_alive())
        self.assertEqual(len(self.processes), 2)
        self.assertEqual(self.sup.gen, 2)

    def test_query_is_settled_when_generation_ends(self):
        b = self.channel()
        waiter = {"event": threading.Event(), "resp": None}
        b.pending['q'] = waiter
        b._ingest({"type": "sessiond.session_dormant"})
        self.assertTrue(waiter['event'].is_set())
        self.assertFalse(waiter['resp']['success'])
        self.assertFalse(b.pending)

    def test_clear_failure_does_not_spawn(self):
        self.start()
        with patch('builtins.open', side_effect=PermissionError('cannot truncate')):
            result = self.sup.clear()
        self.assertFalse(result['ok'])
        self.assertEqual(len(self.processes), 1)
        self.assertIsNone(self.sup.proc)

    def test_start_failure_is_not_retried(self):
        with patch.object(P.subprocess, 'Popen', side_effect=OSError('cannot spawn')) as spawn:
            self.sup.ensure_started()
            self.assertFalse(self.sup.wait_ready(2))
            self.assertEqual(self.sup.state, 'failed')
            self.assertEqual(spawn.call_count, 1)

    def test_declaration_without_prior_agent_request(self):
        store = RootStore([('/', DirStore(str(self.root)))])
        declaration = self.root / 'chat.agent'
        declaration.write_text(json.dumps({'host': 'test', 'cwd': str(self.root)}))
        channel = B.get_bridge('/chat.agent', store=store)
        self.assertTrue(channel.sup.wait_ready(2))
        self.assertEqual(channel.sup.cwd, str(self.root))
        self.assertEqual(B.get_bridge('/chat.agent', store=store), channel)
        with self.assertRaises(ValueError):
            B.get_bridge('/chat.jsonl', store=store)
        declaration.unlink()
        with self.assertRaises(ValueError):
            B.get_bridge('/chat.agent', store=store)
        dropped = B.drop_session('/chat.jsonl')
        self.assertTrue(dropped['killed'])
        self.assertTrue(channel.terminated)
        self.assertEqual(list(channel.iter_events(channel.seq)), [])

    def test_invalid_requests_do_not_open_channel(self):
        with patch.object(B, 'get_bridge', side_effect=AssertionError('must not open')):
            for kwargs in ({'op': 'unknown'}, {'op': 'cmd', 'cmd': '{'},
                           {'op': 'cmd', 'cmd': '{}'},
                           {'op': 'cmd', 'cmd': '{"type":4}'},
                           {'op': 'cmd', 'cmd': '{"type":"switch_session"}'}):
                meta, body = api.interp(None, session='/chat.agent', **kwargs)
                self.assertFalse(json.loads(body)['ok'])
                self.assertIn(meta['http_status'][:3], ('400', '403'))

    def channel(self):
        b = B.Bridge('/session.jsonl', cwd=str(self.root))
        b.ring = deque(maxlen=8)
        return b

    def test_first_baseline_waits_for_generation_announcement(self):
        b = self.channel()
        try:
            result = b.get_entries(timeout=2)
            self.assertTrue(result['ok'])
            self.assertTrue(result['resp']['success'])
            self.assertTrue(result['watermark'])
        finally:
            self.assertTrue(b.sup.shutdown()['ok'])

    def test_expired_dialog_broadcast_precedes_query_response(self):
        for command in ('get_entries', 'get_commands', 'prompt'):
            b = self.channel()
            b.pending_dialogs['d'] = {'id': 'd'}
            b._dialog_deadline['d'] = time.monotonic() - 1
            b.pending['q'] = {'event': threading.Event(), 'resp': None}
            b._ingest({'type': 'response', 'id': 'q', 'command': command,
                       'success': True, 'data': {}})
            self.assertEqual(b.ring[0][2]['type'], 'sessiond.dialog_resolved')
            self.assertFalse(b.pending_dialogs)

    def test_slow_stream_receives_gap_and_oldest_event(self):
        b = self.channel()
        b.ring = deque(maxlen=2)
        b._append({'n': 1})
        events = b.iter_events(0)
        self.assertEqual(next(events)[1], {'n': 1})
        for n in (2, 3, 4): b._append({'n': n})
        self.assertEqual(next(events)[1]['type'], 'webgw.gap')
        self.assertEqual(next(events)[1], {'n': 3})
        self.assertEqual(next(events)[1], {'n': 4})
        events.close()
        seq, gap = b.resolve_since('unknown')
        self.assertTrue(gap)
        self.assertEqual(seq, b.ring[0][0] - 1)

    def test_active_turn_prevents_idle_reap(self):
        b = self.channel()
        b._ingest({'type': 'agent_start'})
        b.last_activity = time.monotonic() - 100
        b.sup.proc = object()
        b.sup.state = 'running'
        with patch.object(B, '_BRIDGES', {'test': b}), patch.object(B, 'IDLE_TIMEOUT', 1), \
                patch.object(b.sup, '_idle_reap') as reap:
            B._idle_scan()
            reap.assert_not_called()
        b.sup.proc = None
        b._ingest({'type': 'agent_settled'})
        self.assertFalse(b.busy)

    def test_stream_closed_before_iteration_releases_subscriber(self):
        b = self.channel()
        with patch.object(B, 'get_bridge', return_value=b):
            _, iterator = stream.interp(None, session='/chat.agent')
        self.assertEqual(b.subscribers, 1)
        iterator.close()
        iterator.close()
        self.assertEqual(b.subscribers, 0)

    def test_shared_stream_limit_including_proxy_handlers(self):
        slots = threading.BoundedSemaphore(1)
        closed = []
        def handler(*args):
            return {'type': 'text/event-stream'}, ClosingIterator(iter([b'data: x\n\n']),
                                                                  lambda: closed.append(True))
        app = wsgi.make_wsgi_app([handler])
        def request():
            statuses = []
            content = app({'PATH_INFO': '/', 'wsgi.input': io.BytesIO()},
                          lambda status, headers: statuses.append(status))
            return statuses[0], content
        with patch.object(wsgi, '_SSE_SLOTS', slots):
            status, first = request()
            self.assertEqual(status, '200 OK')
            status, second = request()
            self.assertEqual(status, '503 Service Unavailable')
            self.assertEqual(len(closed), 1)
            first.close()
            status, third = request()
            self.assertEqual(status, '200 OK')
            third.close()
            self.assertEqual(len(closed), 3)

    def test_root_mount_failure_is_fatal_optional_failure_is_not(self):
        fstab = self.root / 'fstab'
        fstab.write_text('/optional Missing x\n/ Dir %s\n' % self.root)
        self.assertEqual(len(build_root_store(str(fstab), required=('/',)).fstab), 1)
        fstab.write_text('/ Missing x\n')
        with self.assertRaises(StoreException): build_root_store(str(fstab), required=('/',))
        fstab.write_text('/optional Dir %s\n' % self.root)
        with self.assertRaises(StoreException): build_root_store(str(fstab), required=('/',))


if __name__ == '__main__':
    unittest.main()
