# -*- type=script -*-
def interp(store, **args):
    cwd = os.path.realpath('.')
    view = store.read('/vmap')
    log_level = logging.getLevelName(logging.getLogger().getEffectiveLevel())
    return dict(type='text/plain'), 'pwd: %s\ncmd: %s\nlog_level: %s\nvmap: %s\n'%(cwd, sys.argv, log_level, view)
