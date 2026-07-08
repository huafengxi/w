#!/usr/bin/env python
# -*- coding: utf-8-unix -*-
'''
# echo 'user:passwd' > ~/.auth/passwd
# ssl: ~/.auth/fullchain.pem ~/.auth/privkey.pem
log=info ./core/server.py 8080 [log_file]
'''

import sys, os, os.path
import signal
import subprocess
import logging

# repo root = dir containing the `core` package (.../w); put it on sys.path so
# `import core.*` (and, during migration, bare ext modules) resolve regardless
# of how this script is invoked.
_repo_root = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
for p in (_repo_root, os.path.join(_repo_root, 'pylib')):
    if p not in sys.path:
        sys.path.insert(0, p)

def locate_web_path(path):
    p = os.path.realpath(path)
    while p != '/' and not os.path.exists(os.path.join(p, 'index.org')):
        p = os.path.dirname(p)
    return p
_web_path_ = locate_web_path(_repo_root)
def web_path(name):
    return os.path.realpath(os.path.join(_web_path_, name))
os.chdir(_web_path_)

def help():
    print(__doc__)

def list_process(pat):
    for pid in subprocess.Popen(['pgrep', '-f', pat], stdout=subprocess.PIPE).communicate()[0].split(b'\n'):
        if not pid: continue
        yield int(pid)
def kill_process(pat):
    for p in list_process(pat):
        if p != os.getpid(): os.kill(p, signal.SIGKILL)
def set_path():
    import core.registry as registry
    bin_path = web_path('w/bin')
    if not os.path.exists(bin_path):
        bin_path = os.path.expanduser('~/m/w/bin')
    bin_dirs = [bin_path] + [web_path(d) for d in registry.REGISTRY.bin_dirs]
    for d in reversed(bin_dirs):
        if d not in os.getenv('PATH', '').split(':'):
            os.environ['PATH'] = '%s:%s'%(d, os.getenv('PATH'))

def set_logging(log_file=''):
    if log_file:
        f = open(log_file, 'wb+')
        os.dup2(f.fileno(), 1)
        os.dup2(f.fileno(), 2)

from core.wsgi import run_wsgi, make_wsgi_app
import core.vfs_handler as vfs_handler
from core.store import build_root_store
from core.extloader import load_extensions
import core.registry as registry

# Extensions loaded by default; override with env `ext` (comma-separated,
# `ext=` for a feature-free core). Media is last (owns the startup hook).
DEFAULT_EXTS = 'introspect,dsync,org,markdown,encrypt,shell,sql,webdav,fileops,media'

def main():
    if len(sys.argv) <= 1:
        help()
        sys.exit(1)
    log_level = os.getenv('log') or 'info'
    logging.basicConfig(level=getattr(logging, log_level.upper(), None), format="%(asctime)s %(levelname)s %(message)s")
    listen_addr = sys.argv[1]
    log_file = sys.argv[2] if len(sys.argv) > 2 else ''
    ext_env = os.environ.get('ext')
    load_extensions((DEFAULT_EXTS if ext_env is None else ext_env).split(','))
    set_path()
    logging.info('web_start: listen=%s dir=%s log=%s(%s) PATH=%s PYTHONPATH=%s', listen_addr, _web_path_, log_file or 'stdout', log_level, os.getenv('PATH'), os.getenv('PYTHONPATH'))
    kill_process('server.py +{}'.format(listen_addr))

    hook_ctx = dict(web_path=web_path, _web_path_=_web_path_)
    for hook in registry.REGISTRY.startup_hooks:
        try:
            hook(hook_ctx)
        except Exception as e:
            logging.warning('startup hook failed: %r', e)

    set_logging(log_file)
    root = build_root_store('w/fstab')
    handler = vfs_handler.Handler(root).handle_req
    app = make_wsgi_app([handler])
    vfs_handler.set_wsgi_fetcher(app)
    logging.info("listen at: %s", listen_addr)
    host_port = listen_addr.split(':')
    if len(host_port) == 2:
        host, port = host_port
    else:
        host, port = '', listen_addr
    run_wsgi(app, host, int(port), daemon=bool(log_file))

if __name__ == '__main__':
    main()
