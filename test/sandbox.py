"""Isolated test roots; production modules are only read as dependencies."""
import atexit
import os
from pathlib import Path
import sys
import tempfile

BASE = tempfile.mkdtemp(prefix='w-tests-')
os.environ['TMPDIR'] = BASE
tempfile.tempdir = BASE
WS = os.path.join(BASE, 'workspace')
os.makedirs(WS)
os.symlink(str(Path(__file__).resolve().parents[1]), os.path.join(WS, 'w'))
os.environ['W_WORKSPACE'] = WS
# Caller supplies the read-only agentd dependency (envscrub and safe_delete).
if os.environ.get('W_AGENTD_DIR'):
    sys.path.insert(0, os.environ['W_AGENTD_DIR'])


def remove(path):
    try:
        from safe_delete import safe_rmtree
    except ImportError:
        print('Retained test fixture (safe_delete unavailable):', path)
        return
    if os.path.exists(path):
        ok, reason = safe_rmtree(path, authorized_root=BASE)
        if not ok:
            raise RuntimeError(reason)


class TempDir:
    def __init__(self, **kw):
        self.name = tempfile.mkdtemp(**kw)
    def cleanup(self):
        remove(self.name)
    def __enter__(self):
        return self.name
    def __exit__(self, *args):
        self.cleanup()


# The empty authorization anchor is deliberately retained.
atexit.register(lambda: remove(WS))
