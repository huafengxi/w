import logging
import os
import sys
import time
import urllib.parse

def fork_as_daemon(daemon):
    if not daemon: return
    if os.fork() > 0:
        time.sleep(0.1)
        sys.exit(0)

def make_wsgi_app(handlers):
    def echo_handler(env, path, query, post):
        if query.get('__echo__', None) == 'true':
            logging.debug("echo req: %s, %s", path, query)
            return dict(type='text/plain'), '%s %s\n'%(path, query)
    def err_handler(env, path, query, post):
        logging.debug("HANDLE_404: %s", path)
        return dict(type='text/html', http_status='404 Not Found'), open("w/view/404.html").read()
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
        query_args = {k: v[-1] for k, v in urllib.parse.parse_qs(query).items()}
        meta, content = try_these([echo_handler] + handlers + [err_handler], env, path, query_args, post)
        logging.info("RESP: %s meta=%s", path, meta)
        if not meta.get('type'):
            meta['type'] = ''
        mime = meta['type']
        if mime.startswith('text') and 'charset=' not in mime: mime = '%s; charset=%s'%(mime, meta.get('encoding', 'utf-8'))
        headers = [('Content-Type', mime)]
        headers.append(("X-Content-Type-Options", "nosniff"))
        headers.append(('Accept-Ranges', 'bytes'))
        headers += build_cache_control_header(meta)
        content_len, range_resp_header = meta.get('content_len'), meta.get('range_resp_header')
        if content_len: headers.append(('Content-Length', '%d'%(content_len)))
        if range_resp_header: headers.append(range_resp_header)
        # extra_headers：handler 要求透传的额外响应头（reverse proxy 上游响应头，
        # 0831-0937-sh7l），hop-by-hop 由产生方自行剔除。
        headers += meta.get('extra_headers') or []
        return (meta.get('http_status') or '200 OK', headers), content
    def wsgi_app(env, response):
        env.setdefault('QUERY_STRING', '')
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

def patch_wsgiserver_nonascii_uri():
    # wsgiserver 1.3 的 parse_request_uri() 直接对请求行原始 bytes 调 urlparse()，
    # 未编码的非 ASCII 字节（如裸中文路径）ascii 解码崩溃 → 500 裸抛（票 ticket.1apqq4）。
    # 解析前把非 ASCII 字节逐字节 percent-encode（ASCII 原样保留，不动既有 % 序列），
    # 之后走既有 unquote_to_bytes + WSGI utf-8 解码管线 → 正常 404/静态解析。
    import wsgiserver
    orig = wsgiserver.HTTPRequest.parse_request_uri
    def parse_request_uri(self, uri):
        if isinstance(uri, bytes):
            try:
                uri.decode('ascii')
            except UnicodeDecodeError:
                uri = b''.join(bytes([b]) if b < 128 else ('%%%02X' % b).encode('ascii') for b in uri)
        return orig(self, uri)
    wsgiserver.HTTPRequest.parse_request_uri = parse_request_uri

def run_use_wsgiserver(app, host, port, daemon):
    if not host: host ='0.0.0.0'
    patch_wsgiserver_nonascii_uri()
    from wsgiserver import WSGIServer
    cert, keyfile = os.path.expanduser('~/.auth/fullchain.pem'), os.path.expanduser('~/.auth/privkey.pem')
    if os.getenv('nossl') == '1':
        cert, keyfile = None, None
        logging.warning('nossl mode: run with danger')
    elif not os.path.exists(cert) or not os.path.exists(keyfile):
        logging.warning("ssl key %s/%s not exists: run with danger", cert, keyfile)
        cert, keyfile = None, None
    timeout = get_socket_timeout()
    logging.info("run use wsgiserver: socket timeout: %s", timeout)
    server = WSGIServer(app, host, port, certfile=cert, keyfile=keyfile, timeout=timeout, numthreads=30)
    fork_as_daemon(daemon)
    server.start()

def read_credential():
    path = os.path.expanduser('~/.auth/passwd')
    try:
        with open(path) as f:
            return f.read()
    except IOError:
        return ''
os.environ.update(WSGI_AUTH_CREDENTIALS=read_credential().strip())
from wsgi_basic_auth import BasicAuth
def run_wsgi(app, host, port, daemon=False):
    app = BasicAuth(app)
    run_use_wsgiserver(app, host, port, daemon)
