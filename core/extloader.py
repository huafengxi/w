import logging
import importlib

def load_extensions(names):
    """Import each `ext.<name>.register` and call its `register(REGISTRY)`.

    A missing or broken extension is logged and skipped so core never bricks.
    """
    import core.registry as registry
    reg = registry.REGISTRY
    for name in names:
        name = name.strip()
        if not name:
            continue
        try:
            mod = importlib.import_module('ext.%s.register' % name)
            mod.register(reg)
            logging.info('ext loaded: %s', name)
        except Exception as e:
            logging.warning('ext load skipped: %s: %r', name, e)
