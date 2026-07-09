import logging
import importlib

def load_extensions(names):
    """Import each `ext.<name>.register` and call its `register(REGISTRY)`.

    Extensions without a register.py are still considered loaded — vmap.frag
    and other conventions may be enough. A broken register is logged and
    skipped so core never bricks.
    """
    import core.registry as registry
    reg = registry.REGISTRY
    for name in names:
        name = name.strip()
        if not name:
            continue
        try:
            mod = importlib.import_module('ext.%s.register' % name)
        except ModuleNotFoundError as e:
            if e.name == 'ext.%s.register' % name:
                logging.info('ext loaded (no register.py): %s', name)
                continue
            logging.warning('ext load skipped: %s: %r', name, e)
            continue
        try:
            mod.register(reg)
            logging.info('ext loaded: %s', name)
        except Exception as e:
            logging.warning('ext load skipped: %s: %r', name, e)
