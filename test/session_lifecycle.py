#!/usr/bin/env python3
"""Real independent-host lifecycle plus declaration, event-gap and stream budgets.

All processes/files are synthetic. The Web client never owns pi PIPEs.
"""
import io
import json
import os
from pathlib import Path
import subprocess
import signal
import sys
import threading
import time
import unittest
from collections import deque
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sandbox
from ext.sessiond import sessions as S, bridge as B, lifecycle as L, registry as R, bootstrap
from ext.sessiond.client import Client
from ext.sessiond.rpc import api, stream
from core import wsgi
from core.stream import ClosingIterator
from stores.store import RootStore, build_root_store, StoreException
from stores.dir_store import DirStore


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = sandbox.TempDir(prefix='sl-')
        self.root = Path(self.temp.name).resolve()
        self.helper = self.root / 'fakepi'
        self.helper.write_text('#!' + sys.executable + '\n' + '''import json,sys
for line in sys.stdin:
    obj=json.loads(line)
    if obj.get('type') == 'exit': break
    print(json.dumps({'type':'response','id':obj.get('id'),'command':obj['type'],
                      'success':True,'data':{'entries':[],'commands':[]}}), flush=True)
''')
        self.helper.chmod(0o700)
        host = Path(os.environ['W_AGENTD_DIR']).resolve().parent / 'pi-host/pi_host.py'
        self.service = R.build(dict(runtime=str(self.root / 'r'), host_entry=str(host),
                                    agentd=os.environ['W_AGENTD_DIR'], stop_timeout=.5),
                               registered=False)
        self.clients = []
        def factory(session_path, on_event, cwd=None, profile=None, declaration_path=None):
            client = Client(session_path, on_event, cwd, profile, options=self.service.options,
                            declaration_path=declaration_path)
            self.clients.append(client)
            return client
        self.service.client_factory = factory
        self.patchers = [patch.object(S, 'WS', str(self.root)),
                         patch.object(S, 'WS_REAL', str(self.root)),
                         patch.object(B, '_BRIDGES', {}),
                         patch.object(bootstrap, '_REGISTRY', self.service),
                         patch.dict(os.environ, W_SESSION_PI_BIN=str(self.helper))]
        for patcher in self.patchers:
            patcher.start()
        self.events = []
        (self.root / 'session.agent').write_text(json.dumps(dict(host='test', cwd=str(self.root))))
        self.sup = factory('/session.jsonl', self.events.append, cwd=str(self.root))

    def tearDown(self):
        # Detach is not cleanup: explicitly stop exact harness-owned instances.
        for client in self.clients:
            try:
                current = client.life.load()
                if current and client.life.status()['state'] == 'running':
                    client.life.operate('stop', current['instance'])
            except FileNotFoundError:
                # A test deliberately removed its declaration. Actual P facts
                # still authorize only these exact harness-owned identities.
                current = client.life.read_host()
                if current:
                    if L.same(L.identity(current['pid']), current) and L.live(L.identity(current['pid'])):
                        L.signal_identity(current, signal.SIGKILL)
            client.shutdown()
            for handle in client.life.handles.values():
                handle.wait(timeout=3)
        self.wait_for(lambda: not any(t.name == 'sessiond-child-wait' and t.is_alive()
                                     for t in threading.enumerate()))
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def start(self):
        self.sup.ensure_started()
        self.assertTrue(self.sup.wait_ready(2), self.sup.status_doc())

    def wait_for(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(predicate())

    def test_exit_does_not_restart(self):
        self.start()
        first = self.sup.gen
        self.assertTrue(self.sup.send({'type': 'exit'}))
        self.wait_for(lambda: self.sup.life.status()['state'] == 'dormant')
        self.assertEqual(len(self.sup.life.handles), 1)
        self.start()
        self.assertNotEqual(self.sup.gen, first)
        self.assertEqual(len(self.sup.life.handles), 2)

    def test_shutdown_is_permanent_detach_not_process_exit(self):
        self.start()
        self.assertTrue(self.sup.shutdown()['ok'])
        self.assertEqual(self.sup.life.status()['state'], 'running')
        self.sup.ensure_started()
        self.assertFalse(self.sup.wait_ready(.05))
        self.assertFalse(self.sup.send({'type': 'prompt'}))
        self.assertFalse(self.sup.change('reload', self.sup.gen)['ok'])
        self.assertEqual(len(self.sup.life.handles), 1)

    def test_reload_clear_and_closed_client(self):
        self.start()
        session = Path(self.sup.session_file)
        session.write_text('history')
        first = self.sup.gen
        self.assertTrue(self.sup.change('reload', first)['ok'])
        second = self.sup.gen
        self.assertNotEqual(first, second)
        self.assertEqual(session.read_text(), 'history')
        self.assertTrue(self.sup.change('clear', second)['ok'])
        self.assertEqual(session.read_text(), '')
        self.assertNotEqual(self.sup.gen, second)
        self.assertEqual(len(self.sup.life.handles), 3)
        self.assertTrue(all(p.poll() is not None for p in list(self.sup.life.handles.values())[:-1]))
        self.sup.shutdown()
        self.assertFalse(self.sup.reload()['ok'])
        self.assertFalse(self.sup.clear()['ok'])

    def test_queued_access_does_not_duplicate_reload_instance(self):
        self.start()
        first = self.sup.gen
        with self.sup._op_lock:
            queued = threading.Thread(target=self.sup.ensure_started)
            queued.start()
            self.assertTrue(self.sup.change('reload', first)['ok'])
        queued.join(3)
        self.assertFalse(queued.is_alive())
        self.assertEqual(len(self.sup.life.handles), 2)

    def test_query_is_settled_when_generation_ends(self):
        b = self.channel()
        waiter = {'event': threading.Event(), 'resp': None}
        b.pending['q'] = waiter
        b._ingest({'type': 'sessiond.session_dormant'})
        self.assertTrue(waiter['event'].is_set())
        self.assertFalse(waiter['resp']['success'])
        self.assertFalse(b.pending)

    def test_clear_failure_does_not_spawn(self):
        self.start()
        with patch.object(self.sup.life, 'truncate', side_effect=PermissionError('cannot truncate')):
            result = self.sup.change('clear', self.sup.gen)
        self.assertFalse(result['ok'])
        self.assertEqual(len(self.sup.life.handles), 1)
        self.assertEqual(self.sup.life.load()['phase'], 'stopped')

    def test_popen_failure_leaves_no_start_marker(self):
        before = self.sup.life.declaration.read_bytes()
        with patch.object(L.subprocess, 'Popen', side_effect=OSError('cannot spawn')) as spawn:
            self.sup.ensure_started()
            self.assertFalse(self.sup.wait_ready(2))
            self.assertEqual(self.sup.state, 'unknown')
            self.sup.ensure_started()
            self.assertEqual(spawn.call_count, 2)
        self.assertEqual(self.sup.life.declaration.read_bytes(), before)
        self.assertFalse(self.sup.life.current.exists())

    def test_declaration_without_prior_agent_request(self):
        store = RootStore([('/', DirStore(str(self.root)))])
        declaration = self.root / 'chat.agent'
        declaration.write_text(json.dumps({'host': 'test', 'cwd': str(self.root)}))
        channel = B.get_bridge('/chat.agent', store=store)
        self.assertTrue(channel.sup.wait_ready(2))
        self.assertEqual(channel.sup.cwd, str(self.root))
        self.assertIs(B.get_bridge('/chat.agent', store=store), channel)
        with self.assertRaises(ValueError):
            B.get_bridge('/chat.jsonl', store=store)
        declaration.unlink()
        with self.assertRaises(ValueError):
            B.get_bridge('/chat.agent', store=store)
        detached = B.detach_session('/chat.jsonl')
        self.assertTrue(detached['found'])
        self.assertFalse(detached['killed'])
        self.assertEqual(channel.sup.life.status()['state'], 'unknown')
        actual = channel.sup.life.read_host()
        self.assertTrue(L.same(L.identity(actual['pid']), actual))

    def test_external_session_dir_and_declaration_alias_share_host(self):
        store = RootStore([('/', DirStore(str(self.root)))])
        history = self.root / 'history'; history.mkdir()
        ui = self.root / 'ui'; ui.mkdir()
        declaration = ui / 'chat.agent'
        declaration.write_text(json.dumps(dict(host='test', cwd=str(self.root), sessionDir=str(history))))
        channel = B.get_bridge('/ui/chat.agent', store=store)
        self.assertTrue(channel.sup.wait_ready(2), channel.sup.status_doc())
        self.assertEqual(channel.sup.life.declaration, declaration)
        first = channel.sup.gen
        (self.root / 'alias.agent').symlink_to(declaration)
        (history / 'alias.jsonl').symlink_to(history / 'chat.jsonl')
        with patch.object(B, '_BRIDGES', {}):
            reconnected = B.get_bridge('/alias.agent', store=store)
            self.assertTrue(reconnected.sup.wait_ready(2), reconnected.sup.status_doc())
            self.assertEqual(reconnected.sup.gen, first)
            self.assertEqual(reconnected.sup.life.declaration, declaration)
        self.assertFalse((history / 'chat.agent').exists())

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
        b = B.OrdinaryBridge('/session.jsonl', cwd=str(self.root),
                             client_factory=self.service.client_factory)
        b.ring = deque(maxlen=8)
        return b

    def test_first_baseline_waits_for_instance_announcement(self):
        b = self.channel()
        result = b.get_entries(timeout=2)
        self.assertTrue(result['ok'], result)
        self.assertTrue(result['resp']['success'])
        self.assertTrue(result['watermark'])

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

    def test_stream_closed_before_iteration_releases_subscriber(self):
        b = self.channel()
        with patch.object(B, 'get_bridge', return_value=b):
            _, iterator = stream.interp(None, session='/chat.agent')
        self.assertEqual(b.subscribers, 1)
        iterator.close()
        iterator.close()
        self.assertEqual(b.subscribers, 0)

    def test_disconnect_keeps_process_and_reconnect_reuses_instance(self):
        store = RootStore([('/', DirStore(str(self.root)))])
        (self.root / 'chat.agent').write_text(json.dumps({'host': 'test', 'cwd': str(self.root)}))
        b = B.get_bridge('/chat.agent', store=store)
        generation = b.sup.gen
        _, events = stream.interp(store, session='/chat.agent')
        next(events)
        events.close()
        self.assertEqual(b.subscribers, 0)
        self.assertEqual(b.sup.life.status()['state'], 'running')
        connected = B.get_bridge('/chat.agent', store=store)
        baseline = connected.get_entries(timeout=2)
        self.assertTrue(baseline['resp']['success'])
        self.assertIs(connected.sup, b.sup)
        self.assertEqual(connected.sup.gen, generation)
        self.assertEqual(len(b.sup.life.handles), 1)

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
