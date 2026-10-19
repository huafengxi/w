"""Test-only scoped factory installation; production uses bootstrap.configure."""
from contextlib import contextmanager, ExitStack
import sys
from unittest.mock import patch
import h2_direct_host as H
from ext.sessiond import ordinary_registry as C
from ext.sessiond.rpc import api


@contextmanager
def installed(options):
    registry = C.build(options)
    with ExitStack() as stack:
        for module, name, value in registry.bindings:
            stack.enter_context(patch.object(module, name, value))
        yield registry


def api_call(store, *, expected_instance=None, expected_registration=None, **kwargs):
    i = H._EXPECTED.set(expected_instance)
    r = C._EXPECTED_REGISTRATION.set(expected_registration)
    try:
        return api.interp(store, **kwargs)
    finally:
        C._EXPECTED_REGISTRATION.reset(r)
        H._EXPECTED.reset(i)


C.installed = installed
C.api_call = api_call
sys.modules[__name__] = C
