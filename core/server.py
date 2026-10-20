#!/usr/bin/env python
# -*- coding: utf-8-unix -*-
'''
# Configuration: see README.md (W_WORKSPACE, W_FSTAB, W_AUTH_FILE).
log=info ./core/server.py [--foreground] 8080 [log_file]
'''

import sys, os, os.path
import logging

# repo root = dir containing the `core` package (.../w); put it on sys.path so
# `import core.*` (and, during migration, bare ext modules) resolve regardless
# of how this script is invoked.
_repo_root = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from core.config import workspace, path as config_path
_web_path_ = workspace()
def web_path(name):
    return os.path.realpath(os.path.join(_web_path_, name))
os.chdir(_web_path_)

def help():
    print(__doc__)

def set_path():
    bin_path = os.path.join(_repo_root, 'bin')
    bin_dirs = [bin_path]
    ext_dir = os.path.join(_repo_root, 'ext')
    if os.path.isdir(ext_dir):
        for name in sorted(os.listdir(ext_dir)):
            d = os.path.join(ext_dir, name, 'bin')
            if os.path.isdir(d):
                bin_dirs.append(d)
    for d in reversed(bin_dirs):
        if d not in os.getenv('PATH', '').split(':'):
            os.environ['PATH'] = '%s:%s'%(d, os.getenv('PATH'))

def set_logging(log_file=''):
    if log_file:
        f = open(log_file, 'wb+')
        os.dup2(f.fileno(), 1)
        os.dup2(f.fileno(), 2)

from core.wsgi import run_wsgi, make_wsgi_app
from core.handler import Handler
from ext.proxy.proxy import proxy_handler
from stores.store import build_root_store

def main():
    args = sys.argv[1:]
    foreground = '--foreground' in args
    args = [arg for arg in args if arg != '--foreground']
    if not args:
        help()
        sys.exit(1)
    log_level = os.getenv('log') or 'info'
    logging.basicConfig(level=getattr(logging, log_level.upper(), None), format="%(asctime)s %(levelname)s %(message)s")
    listen_addr = args[0]
    log_file = args[1] if len(args) > 1 else ''
    set_path()
    logging.info('web_start: listen=%s dir=%s log=%s(%s) PATH=%s PYTHONPATH=%s', listen_addr, _web_path_, log_file or 'stdout', log_level, os.getenv('PATH'), os.getenv('PYTHONPATH'))

    set_logging(log_file)
    from ext.sessiond.bootstrap import configure as configure_ordinary
    registry = configure_ordinary()  # fail before listening; no silent PIPE fallback
    logging.info('ordinary host configured: entry=%s runtime=%s',
                 registry.options['host_entry'], registry.options['runtime'])
    root = build_root_store(config_path('W_FSTAB', required=True), required=('/',))
    from ext.proxy.proxy import load_routes
    load_routes()  # validate supplied proxy configuration before listening
    handler = Handler(root).handle_req
    # 代理挂管线第一个业务位（主路由之前）：位于全局 BasicAuth 之内，
    # 未命中规则返回 None 落回既有管线（见 design.md「reverse proxy」）。
    app = make_wsgi_app([proxy_handler, handler])
    logging.info("listen at: %s", listen_addr)
    host_port = listen_addr.split(':')
    if len(host_port) == 2:
        host, port = host_port
    else:
        host, port = '', listen_addr
    run_wsgi(app, host, int(port), daemon=bool(log_file) and not foreground)

if __name__ == '__main__':
    main()
