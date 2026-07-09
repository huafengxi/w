import logging
import importlib

def load_extensions(names):
    """Pre-import each ext.<name> so broken extensions surface at startup.

    With registry gone, extensions are composed by convention (vmap.frag,
    mime.frag, bin/); no register.py is required.
    """
    for name in names:
        name = name.strip()
        if not name:
            continue
        try:
            importlib.import_module('ext.%s' % name)
            logging.info('ext loaded: %s', name)
        except Exception as e:
            logging.warning('ext load skipped: %s: %r', name, e)
