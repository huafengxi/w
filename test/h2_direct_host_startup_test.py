#!/usr/bin/env python3
"""Post-launch socket acceptance, not pre-launch contention arbitration."""
import concurrent.futures
import json
import os
from pathlib import Path
import socket
import unittest
from unittest.mock import patch

from h2_direct_host_test import Fixture, record
import h2_direct_host as H


class StartupAcceptance(unittest.TestCase):
    def setUp(self):
        self.f = Fixture(self.id().split('.')[-1])
        self.life = self.f.life

    def tearDown(self):
        self.f.cleanup()

    def test_own_peer_and_real_get_state_then_running(self):
        accept = self.life._accept_start
        observed = []
        def check(d):
            observed.append(self.life.status()['state'])
            self.assertEqual(d['phase'], 'verifying')
            return accept(d)
        with patch.object(self.life, '_accept_start', side_effect=check):
            d = self.life.ensure()
        self.f.remember()
        self.assertEqual(observed, ['unknown'])
        proof = d['startupAcceptance']
        self.assertEqual(proof['peerPid'], d['host']['pid'])
        self.assertEqual((proof['command'], proof['success']), ('get_state', True))
        requests = [json.loads(x) for x in (self.f.ws / 'startup-requests').read_text().splitlines()]
        self.assertEqual(requests, [dict(pid=d['child']['pid'], id=proof['requestId'])])
        self.assertEqual(self.life.status()['state'], 'running')
        self.assertEqual(list(self.life.local.parent.rglob('*.lock')), [])
        record(startupAcceptance=proof, beforeAcceptance=observed, noFileLocks=True)

    def test_foreign_actual_socket_peer_is_not_probed_killed_or_unlinked(self):
        accept = self.life._accept_start
        foreign = socket.socket(socket.AF_UNIX)
        saved = str(self.life.local / 'own-socket')
        inode = []
        def replace_endpoint(d):
            # Own test roots only: move our P's pathname and bind a real listener
            # owned by this harness. Kernel peer must differ from the new P.
            os.rename(self.life.sock, saved)
            foreign.bind(self.life.sock)
            foreign.listen(2)
            inode.append(os.lstat(self.life.sock).st_ino)
            return accept(d)
        try:
            with patch.object(self.life, '_accept_start', side_effect=replace_endpoint):
                with self.assertRaisesRegex(RuntimeError, 'not the launched host'):
                    self.life.ensure()
            d = self.life.load()
            self.f.remember()
            self.assertEqual(d['phase'], 'startup_failed')
            self.assertEqual(d['startupCleanup']['remainingLive'], [])
            self.assertEqual(os.lstat(self.life.sock).st_ino, inode[0])
            self.assertFalse((self.f.ws / 'startup-requests').exists())
            foreign.settimeout(1)
            connection, _ = foreign.accept()
            with connection:
                connection.settimeout(1)
                self.assertEqual(connection.recv(1), b'')  # no get_state sent to wrong P
            record(foreignPeer=dict(pid=os.getpid(), inode=inode[0], kept=True), failed=d)
        finally:
            foreign.close()

    def _failure(self, mode):
        (self.f.ws / 'startup-mode').write_text(mode)
        accept = self.life._accept_start
        def bounded(d):
            self.life.startup_timeout = .2  # facts already obtained before this
            return accept(d)
        with patch.object(self.life, '_accept_start', side_effect=bounded):
            with self.assertRaises(RuntimeError):
                self.life.ensure()
        d = self.life.load()
        self.f.remember()
        self.assertEqual(d['phase'], 'startup_failed')
        self.assertNotIn('startupAcceptance', d)
        self.assertEqual(d['startupCleanup']['remainingLive'], [])
        rows = (self.f.ws / 'startup-requests').read_text().splitlines()
        self.assertEqual(len(rows), 1)  # one probe, no reconnect/replay retry
        self.assertFalse(any(H.live(H.identity(i['pid'])) for i in d['known']))
        with self.assertRaises(RuntimeError):
            self.life.ensure()  # no implicit failed-start retry
        record(startupFailureMode=mode, record=d)

    def test_get_state_rejection_is_failed_start(self):
        self._failure('rejected')

    def test_success_for_wrong_request_id_is_not_acceptance(self):
        self._failure('wrong-id')

    def test_success_for_wrong_command_is_not_acceptance(self):
        self._failure('wrong-command')

    def test_eof_is_not_acceptance(self):
        self._failure('eof')

    def test_timeout_is_not_acceptance(self):
        self._failure('silent')

    def test_atomic_writers_do_not_share_a_temporary_file(self):
        target = self.f.root / 'parallel.json'
        with concurrent.futures.ThreadPoolExecutor(4) as pool:
            list(pool.map(lambda n: H.atomic(target, dict(n=n)), range(40)))
        self.assertIn(json.loads(target.read_text())['n'], range(40))
        self.assertEqual(list(self.f.root.glob('parallel.json.tmp-*')), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
