"""Isolated SSE multiplex reservations, independent cursors, gaps and wakeups."""
import json
from pathlib import Path
import sys
import threading
import unittest
from collections import deque
from unittest.mock import patch

import sandbox
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ext.sessiond import bridge
from ext.sessiond.rpc import multiplex


class Multiplex(unittest.TestCase):
    def setUp(self):
        self.channels = {}
        for name in ['/one.agent', '/two.agent']:
            b = bridge.Bridge.__new__(bridge.Bridge)
            b.lock = threading.Lock()
            b.cond = threading.Condition(b.lock)
            b.ns = name[1:4]
            b.seq = 0
            b.ring = deque(maxlen=3)
            b.subscribers = 0
            b.stream_wakes = set()
            b.terminated = False
            b._append(dict(type='initial'))
            self.channels[name] = b
        self.patcher = patch.object(bridge, 'get_bridge', side_effect=lambda name, **kw: self.channels[name])
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def open(self, **cursors):
        return multiplex.interp(None, subscriptions=json.dumps([
            dict(session=name, since=since) for name, since in cursors.items()]))

    def decode(self, stream):
        return json.loads(next(stream).decode().removeprefix('data: '))

    def test_sessions_have_independent_ids_and_release_without_start(self):
        meta, stream = self.open(**{'/one.agent': '', '/two.agent': ''})
        self.assertEqual(meta['type'], 'text/event-stream')
        self.assertTrue(all(b.subscribers == 1 for b in self.channels.values()))
        stream.close()
        stream.close()
        self.assertTrue(all(b.subscribers == 0 and not b.stream_wakes for b in self.channels.values()))
        _, stream = self.open(**{'/one.agent': '', '/two.agent': ''})
        self.addCleanup(stream.close)
        next(stream)
        first, second = self.decode(stream), self.decode(stream)
        self.assertEqual(first['session'], '/one.agent')
        self.assertEqual(second['session'], '/two.agent')
        self.assertNotEqual(first['id'], second['id'])

    def test_bridge_notification_wakes_idle_mux_without_polling(self):
        b = self.channels['/one.agent']
        _, stream = self.open(**{'/one.agent': b.ring[-1][1]})
        self.addCleanup(stream.close)
        next(stream)
        result = []
        reader = threading.Thread(target=lambda: result.append(self.decode(stream)))
        reader.start()
        b._append(dict(type='agent_start'))
        reader.join(1)
        self.assertFalse(reader.is_alive(), 'lost event wakeup')
        self.assertEqual(result[0]['event']['type'], 'agent_start')

    def test_gap_is_per_session_and_closed_session_does_not_stop_others(self):
        one, two = self.channels.values()
        for i in range(5):
            one._append(dict(type='new', n=i))
        one.terminated = True
        _, stream = self.open(**{'/one.agent': 'one:1', '/two.agent': ''})
        self.addCleanup(stream.close)
        next(stream)
        gap = self.decode(stream)
        self.assertEqual(gap['session'], '/one.agent')
        self.assertEqual(gap['event']['type'], 'webgw.gap')
        for _ in range(3):
            self.assertEqual(self.decode(stream)['event']['type'], 'new')
        self.assertEqual(self.decode(stream)['event']['type'], 'sessiond.stream_closed')
        self.assertEqual(self.decode(stream)['session'], '/two.agent')

    def test_late_limit_failure_rolls_back_prior_reservations(self):
        self.channels['/two.agent'].subscribers = bridge.MAX_STREAMS_PER_BRIDGE
        meta, body = self.open(**{'/one.agent': '', '/two.agent': ''})
        self.assertIn('429', meta['http_status'])
        self.assertEqual(self.channels['/one.agent'].subscribers, 0)
        self.assertFalse(self.channels['/one.agent'].stream_wakes)

    def test_bad_shapes_never_open_a_bridge(self):
        for raw in ['{}', '[]', '[{"session":"/one.agent","since":1}]',
                    json.dumps([dict(session='/one.agent', since='')] * 2)]:
            meta, _body = multiplex.interp(None, subscriptions=raw)
            self.assertIn('400', meta['http_status'])
        bridge.get_bridge.assert_not_called()


if __name__ == '__main__':
    unittest.main()
