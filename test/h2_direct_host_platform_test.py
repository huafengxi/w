"""Platform stop contract: Darwin check-then-kill is deliberately best effort."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(tempfile.mkdtemp(prefix='wop-'))
os.environ.setdefault('W_WORKSPACE', str(ROOT))
os.environ.setdefault('W_AGENTD_DIR', os.environ['H2_A_SOURCE'])
sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parent)]
import h2_direct_host as H
H.configure_process_tools(Path(os.environ['H2_P_SOURCE']) / 'pi_host.py')


class PlatformStop(unittest.TestCase):
    def setUp(self):
        self.expected = H.identity(os.getpid())

    def test_matching_darwin_uses_pid_kill(self):
        with patch.object(H.sys, 'platform', 'darwin'), patch.object(H, 'identity', return_value=self.expected), \
                patch.object(H.os, 'kill') as kill, patch.object(H, 'pidfd_open') as pidfd:
            H.signal_identity(self.expected, signal.SIGTERM)
            kill.assert_called_once_with(self.expected['pid'], signal.SIGTERM)
            pidfd.assert_not_called()

    def test_mismatched_start_never_kills(self):
        actual = dict(self.expected, start='0')
        with patch.object(H.sys, 'platform', 'darwin'), patch.object(H, 'identity', return_value=actual), \
                patch.object(H.os, 'kill') as kill:
            with self.assertRaisesRegex(RuntimeError, 'identity mismatch'):
                H.signal_identity(self.expected, signal.SIGTERM)
            kill.assert_not_called()

    def test_denied_read_never_kills(self):
        with patch.object(H.sys, 'platform', 'darwin'), patch.object(H, 'identity', side_effect=PermissionError('denied')), \
                patch.object(H.os, 'kill') as kill:
            with self.assertRaises(PermissionError):
                H.signal_identity(self.expected, signal.SIGTERM)
            kill.assert_not_called()

    def test_already_exited_never_kills(self):
        with patch.object(H.sys, 'platform', 'darwin'), patch.object(H, 'identity', return_value=None), \
                patch.object(H.os, 'kill') as kill:
            H.signal_identity(self.expected, signal.SIGTERM)
            kill.assert_not_called()

    def test_exited_between_check_and_kill_is_harmless(self):
        with patch.object(H.sys, 'platform', 'darwin'), patch.object(H, 'identity', return_value=self.expected), \
                patch.object(H.os, 'kill', side_effect=ProcessLookupError):
            H.signal_identity(self.expected, signal.SIGTERM)

    def test_wrong_boot_never_kills(self):
        with patch.object(H.os, 'kill') as kill:
            with self.assertRaisesRegex(RuntimeError, 'boot identity mismatch'):
                H.signal_identity(dict(self.expected, boot='wrong'), signal.SIGTERM)
            kill.assert_not_called()

    def test_real_own_child_can_stop(self):
        p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], start_new_session=True)
        try:
            expected = H.identity(p.pid)
            H.signal_identity(expected, signal.SIGTERM)
            self.assertEqual(p.wait(timeout=5), -signal.SIGTERM)
        finally:
            if p.poll() is None:
                p.kill()
                p.wait(timeout=5)


if __name__ == '__main__':
    unittest.main(verbosity=2)
