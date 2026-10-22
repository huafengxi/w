"""Agent routes and control proxy remain agent-owned after the source split."""
import json
import os
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sandbox
from ext.sessiond import sessions as S, agent_client as A
from types import SimpleNamespace


class AgentBoundary(unittest.TestCase):
    def setUp(self):
        self.temp = sandbox.TempDir(prefix='agent-client-')
        self.root = Path(self.temp.name)
        self.home = self.root / 'agents/task/fixture'
        self.home.mkdir(parents=True)
        self.patchers = [patch.object(S, 'WS', str(self.root)),
                         patch.object(S, 'WS_REAL', str(self.root)),
                         patch.object(S, '_self_host', return_value='test'),
                         patch.object(A, 'agent_workspace', return_value=str(self.root)),
                         patch.object(A, 'agent_metadata', side_effect=lambda root,pid: json.loads(self.spec.read_text()) if self.spec.exists() else None),
                         patch.object(A, 'agent_state', side_effect=lambda root,pid: json.loads(self.pid.read_text()) if self.pid.exists() else None)]
        for patcher in self.patchers: patcher.start()
        self.spec = self.home / 'spec.json'
        self.pid = self.home / 'state-view.json'
        self.spec.write_text(json.dumps(dict(host='test', workdir=str(self.root))))
        self.site = '/agents/task/fixture/session/session.jsonl'

    def tearDown(self):
        for patcher in reversed(self.patchers): patcher.stop()
        self.temp.cleanup()

    def test_spec_entry_only_no_unregistered_or_final_revival(self):
        self.assertEqual(A.agentd_entry('/agents/task/fixture/spec.json'), (self.site, None))
        self.assertIsNotNone(A.agentd_entry(self.site)[1])
        self.assertEqual(A.agentd_route(self.site)[0], 'reject')
        self.pid.write_text(json.dumps(dict(final=True)))
        self.assertIn('终态', A.agentd_route(self.site)[1])
        self.pid.write_text(json.dumps(dict(status='paused')))
        self.assertIn('暂停', A.agentd_route(self.site)[1])

    def test_foreign_host_is_rejected_before_socket_connection(self):
        self.spec.write_text(json.dumps(dict(host='elsewhere')))
        with patch.object(A, '_sock_live', side_effect=AssertionError('no foreign connect')):
            self.assertIn('elsewhere', A.agentd_route(self.site)[1])

    def test_live_route_uses_recorded_identity_and_socket(self):
        self.pid.write_text(json.dumps(dict(pid=123, procStart=456, sock='/synthetic.sock')))
        with patch.object(A, 'pid_identity_ok', return_value=True) as identity, \
                patch.object(A, '_sock_live', return_value=True) as connected:
            self.assertEqual(A.agentd_route(self.site), ('live', dict(sock='/synthetic.sock', participant_id='task/fixture')))
            identity.assert_called_once_with(123, 456)
            connected.assert_called_once_with('/synthetic.sock')

    def test_darwin_route_uses_configured_native_start_identity(self):
        from ext.sessiond import lifecycle
        with patch.object(A.sys, 'platform', 'darwin'), patch.object(lifecycle, 'identity', return_value={'start':'123456789'}) as read:
            self.assertTrue(A.pid_identity_ok(123, 123456789))
            self.assertFalse(A.pid_identity_ok(123, 987654321))
            read.assert_called_with(123)
        with patch.object(A.sys, 'platform', 'darwin'), patch.object(lifecycle, 'identity', return_value=None):
            self.assertFalse(A.pid_identity_ok(123, 123456789))

    def test_control_publishes_desired_without_protocol_ack(self):
        channel=SimpleNamespace(sup=SimpleNamespace(participant_id='task/fixture'),session='fixture')
        with patch('execution.desired') as desired:
            doc,status=A.control(channel,'stop','test')
            desired.assert_called_once_with(str(self.root),'task/fixture','cancel')
        self.assertEqual(status,'200 OK');self.assertTrue(doc['ok'])
        self.assertFalse((self.home/'control').exists())


if __name__ == '__main__':
    unittest.main()
