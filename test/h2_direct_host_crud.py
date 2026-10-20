"""Test-only scoped service injection; production uses bootstrap.configure."""
from contextlib import contextmanager
import sys
from unittest.mock import patch
import h2_direct_host as H
from ext.sessiond import registry as C, bootstrap
from ext.sessiond.rpc import api


@contextmanager
def installed(options):
    service = C.build(options)
    with patch.object(bootstrap, '_REGISTRY', service):
        yield service


def api_call(store, *, expected_instance=None, **kwargs):
    i = H._EXPECTED.set(expected_instance)
    try:
        return api.interp(store, **kwargs)
    finally:
        H._EXPECTED.reset(i)


C.installed = installed
C.api_call = api_call
sys.modules[__name__] = C
