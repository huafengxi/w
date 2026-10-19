"""Test-only scoped injection of the real lifecycle/client service."""
from contextlib import contextmanager
import sys
from unittest.mock import patch
from ext.sessiond import lifecycle as H, registry as R, bootstrap
from ext.sessiond.rpc import api


@contextmanager
def installed(options):
    service = R.build(options, registered=False)
    with patch.object(bootstrap, '_REGISTRY', service):
        yield service


def api_call(store, *, expected_instance=None, **kwargs):
    if kwargs.get('op') == 'delete_session':
        return api._j(dict(ok=False, error='delete is outside direct-host scope'), '501 Not Implemented')
    token = H._EXPECTED.set(expected_instance)
    try:
        return api.interp(store, **kwargs)
    finally:
        H._EXPECTED.reset(token)


# Identity fault injection targets the real lifecycle globals, not re-export copies.
H.installed = installed
H.api_call = api_call
sys.modules[__name__] = H
