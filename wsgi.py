import traceback
import platform
import logging
import urllib
import os
import sys
import time
from utils import parse_qs_to_dict, safe_read_text

def fork_as_daemon(daemon):
    if not daemon: return
    if os.fork() > 0:
        time.sleep(0.1)
        sys.exit(0)

def make_wsgi_app(handlers):
    '''def handler(env, path, query, post): return mime_type, content'''
    def echo_handler(env, path, query, post):
        if query.get('__echo__', None) == 'true':
            logging.debug("echo req: %s, %s", path, query)
            return dict(type='text/plain'), '%s %s\n'%(path, query)
    def err_handler(env, path, query, post):
        logging.debug("HANDLE_404: %s", path)
        return dict(type='text/html'), safe_read_text("w/404.html")
    def try_these(handlers, env, path, query, post):
        for f in handlers:
            ret = f(env, path, query, post)
            if ret: return ret
    def build_cache_control_header(meta):
        mime = meta.get('type', '')
        if meta.get('rpath') and mime.startswith('image/'):
            return [('Cache-control', 'Private,Max-age=86400')]
        else:
            return []

    def handle_request(env, path, query, post):
        logging.debug("REQ: %s %s query=%s", path, env, repr(query))
        #path = os.path.normpath(path)
        query_args = parse_qs_to_dict(query)
        meta, content = try_these([echo_handler] + handlers + [err_handler], env, path, query_args, post)
        logging.info("RESP: %s meta=%s", path, meta)
        if not meta.get('type'):
            meta['type'] = ''
        mime = meta['type']
        if mime.startswith('text'): mime = '%s; charset=%s'%(mime, meta.get('encoding', 'utf-8'))
        headers = [('Content-Type', mime)]
        headers.append(("X-Content-Type-Options", "nosniff"))
        headers.append(('Accept-Ranges', 'bytes'))
        headers += build_cache_control_header(meta)
        content_len, range_resp_header = meta.get('content_len'), meta.get('range_resp_header')
        if content_len: headers.append(('Content-Length', '%d'%(content_len)))
        if range_resp_header: headers.append(range_resp_header)
        return (meta.get('http_status') or '200 OK', headers), content
    def wsgi_app(env, response):
        env.setdefault('QUERY_STRING', '')
        env.update(wsgi_handler=wsgi_app)

        pi = env.get('PATH_INFO', '')
        try:
            env['PATH_INFO'] = (pi.encode('latin-1') if isinstance(pi, str) else pi).decode('utf-8', errors='replace')
        except Exception as e:
            logging.warning('PATH_INFO decode failed: %r %s', pi, e)

        header, content = handle_request(env, env.get('PATH_INFO'), env['QUERY_STRING'], env['wsgi.input'])
        response(*header)
        if not content: content = []
        elif isinstance(content, bytes): content = [content]
        elif isinstance(content, str): content = [content.encode()]
        return content
    return wsgi_app

def get_socket_timeout():
    t = os.getenv("timeout")
    t = int(t if t else '60')
    return None if t < 0 else t

def run_use_wsgiserver(app, host, port, daemon):
    if not host: host ='0.0.0.0'
    from wsgiserver import WSGIServer
    cert, keyfile = os.path.expanduser('~/.auth/fullchain.pem'), os.path.expanduser('~/.auth/privkey.pem')
    if os.getenv('nossl') == '1':
        cert, keyfile = None, None
        logging.warn('nossl mode: run with danger')
    elif not os.path.exists(cert) or not os.path.exists(keyfile):
        logging.warn("ssl key %s/%s not exists: run with danger", cert, keyfile)
        cert, keyfile = None, None
    timeout = get_socket_timeout()
    logging.info("run use wsgiserver: socket timeout: %s", timeout)
    server = WSGIServer(app, host, port, certfile=cert, keyfile=keyfile, timeout=timeout, numthreads=30)
    fork_as_daemon(daemon)
    server.start()
    return True

def read_crendential():
    path = os.path.expanduser('~/.auth/passwd')
    try:
        with open(path) as f:
            return f.read()
    except IOError:
        return ''
os.environ.update(WSGI_AUTH_CREDENTIALS=read_crendential().strip())
from wsgi_basic_auth import BasicAuth
def run_wsgi(app, host, port, daemon=False):
    app = BasicAuth(app)
    return run_use_wsgiserver(app, host, port, daemon)
