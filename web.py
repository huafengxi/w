#!/usr/bin/env python2
'''
# echo 'user:passwd' > ~/.auth/passwd
# ssl: ~/.auth/cert.pem ~/.auth/privkey.pem
log=info ./myapp.py 8080 [log_file]
'''

import sys, os, os.path
def locate_web_path(path):
    p = os.path.realpath(path)
    while p != '/' and not os.path.exists(os.path.join(p, 'index.org')):
        p = os.path.dirname(p)
    return p
_web_path_ = locate_web_path(os.path.dirname(sys.argv[0]) if sys.argv[0].endswith('/web.py') else '.')
def web_path(name):
    return os.path.realpath(os.path.join(_web_path_, name))
os.chdir(_web_path_)
sys.path.extend(['pylib'])
import logging

def help():
    print __doc__

import signal
import subprocess
def list_process(pat):
    for pid in subprocess.Popen(['pgrep', '-f', 'web.py'], stdout=subprocess.PIPE).communicate()[0].split('\n'):
        if not pid: continue
        yield int(pid)
def kill_process(pat):
    for p in list_process(pat):
        if p != os.getpid(): os.kill(p, signal.SIGKILL)
def set_path():
    if os.name == 'nt':
        os.putenv('PATH', 'c:/Python27;c:/emacs/bin;c:/cygwin64/bin;%s;%s'%(web_path('bin'), os.getenv('PATH')))
    else:
        bin_path = web_path('w/bin')
        if not os.path.exists(bin_path):
            bin_path = os.path.expanduser('~/m/w/bin')
        if bin_path not in os.getenv('PATH'):
            os.environ['PATH'] = '%s:%s'%(bin_path, os.getenv('PATH'))

def set_logging(log_file=''):
    if log_file:
        f = open(log_file, 'wb+')
        os.dup2(f.fileno(), 1)
        os.dup2(f.fileno(), 2)

from wsgi import run_wsgi, make_wsgi_app
import vfs_handler
from store import pack, build_root_store

def get_pack():
    return globals().get('__pack__', None)

def main():
    len(sys.argv) > 1 or help() or sys.exit(1)
    log_level = os.getenv('log') or 'info'
    logging.basicConfig(level=getattr(logging, log_level.upper(), None), format="%(asctime)s %(levelname)s %(message)s")
    if get_pack():
        sys.argv[0] = '<pack>/web.py'
    listen_addr = sys.argv[1]
    log_file = len(sys.argv) > 2 and sys.argv[2] or ''
    set_path()
    logging.info('web_start: listen=%s dir=%s log=%s(%s) pack=%s PATH=%s PYTHONPATH=%s', listen_addr, _web_path_, log_file or 'stdout', log_level, get_pack(), os.getenv('PATH'), os.getenv('PYTHONPATH'))
    kill_process('web.py {}'.format(listen_addr))
    set_logging(log_file)
    root = build_root_store('w/fstab')
    if 'genpack' in globals():
        logging.info('set pack in /g/self')
        root.write('/g/self', genpack(__pack__))
    handler = vfs_handler.Handler(root).handle_req
    app = make_wsgi_app([handler], pack)
    vfs_handler.set_wsgi_fetcher(app)
    logging.info("listen at: %s", listen_addr)
    host_port = listen_addr.split(':')
    if len(host_port) == 2:
        host, port = host_port
    else:
        host, port = '', listen_addr
    run_wsgi(app, host, int(port), log_file)

if __name__ == '__main__':
    pack.set_pack(get_pack())
    main()
