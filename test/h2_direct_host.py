"""Test-only adapters for the canonical production ordinary-host module."""
from contextlib import contextmanager
import sys
from unittest.mock import patch
from ext.sessiond import ordinary_host as H
from ext.sessiond.rpc import api


@contextmanager
def installed(options):
    def factory(session_path, on_event, cwd=None, profile=None):
        return H.DirectSupervisor(session_path, on_event, cwd, profile, options=options)
    with patch.object(H.P, 'Supervisor', factory), patch.object(H.B, 'Bridge', H.DirectBridge):
        yield


def api_call(store, *, expected_instance=None, **kwargs):
    if kwargs.get('op') == 'delete_session':
        return api._j(dict(ok=False, error='delete is outside direct-host candidate scope'), '501 Not Implemented')
    token = H._EXPECTED.set(expected_instance)
    try:
        return api.interp(store, **kwargs)
    finally:
        H._EXPECTED.reset(token)


# Tests patch the real runtime globals, not a disconnected re-exported copy.
H.installed = installed
H.api_call = api_call
sys.modules[__name__] = H
