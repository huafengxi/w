"""One-time ordinary factory configuration shared by the HTTP service and CLI."""
import os
from pathlib import Path
import threading

from core.config import path as config_path

_LOCK = threading.RLock()
_REGISTRY = None


def configure(*, required=True):
    global _REGISTRY
    # Library-only tests can explicitly install factories without a service
    # environment. Real server startup always calls with required=True.
    if not required and not os.environ.get('W_SESSION_HOST_ENTRY'):
        return None
    options = dict(runtime=config_path('W_SESSION_RUNTIME', required=True),
                   host_entry=config_path('W_SESSION_HOST_ENTRY', required=True),
                   agentd=config_path('W_AGENTD_DIR', required=True),
                   startup_timeout=25)
    if not Path(options['host_entry']).is_file():
        raise ValueError('configured independent P entry does not exist')
    with _LOCK:
        from ext.sessiond.ordinary_host import private_dir
        if required:
            private_dir(options['runtime'])  # service startup, not an autorun list request
        if _REGISTRY is not None:
            if _REGISTRY.options != options:
                raise RuntimeError('ordinary host configuration changed inside one process')
            return _REGISTRY
        from ext.sessiond.ordinary_host import configure_process_tools
        configure_process_tools(options['host_entry'])
        from ext.sessiond.ordinary_registry import configure as install_factories
        _REGISTRY = install_factories(options)
        return _REGISTRY
