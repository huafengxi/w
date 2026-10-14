import logging
import os
import sys
import time
import threading
from core.stream import ClosingIterator
from core.config import path as config_path

HTTP_THREADS = 30
SSE_LIMIT = 20
_SSE_SLOTS = threading.BoundedSemaphore(SSE_LIMIT)
import urllib.parse

# DEBUG 面的 secret 脱敏（任务 4ob5de 项 4）：`REQ:`/`echo req:` 两行过去直打原始
# query string 与整个 WSGI environ（nonce/token/cookie/Authorization 载体）——现网
# log=info 不触发，但 `log=debug` 起服务（README「start service」的写法）即整体绕过
# handler 的 `_redact_log_args`。这里复用 core/handler.py 同一套键名族（fnv23x 引入的
# 词边界匹配）= 单一事实源，不再各写一份；`_debug_enabled()` 门控让 INFO 面零额外开销。
from core.handler import _redact_secret_args, redact_env_for_log, redact_query_string

def _debug_enabled():
    return logging.getLogger().isEnabledFor(logging.DEBUG)

def fork_as_daemon(daemon):
    if not daemon: return
    if os.fork() > 0:
        time.sleep(0.1)
        sys.exit(0)

def make_wsgi_app(handlers):
    def echo_handler(env, path, query, post):
        if query.get('__echo__', None) == 'true':
            if _debug_enabled():
                logging.debug("echo req: %s, %s", path, _redact_secret_args(query))
            return dict(type='text/plain'), '%s %s\n'%(path, query)
    def err_handler(env, path, query, post):
        logging.debug("HANDLE_404: %s", path)
        return dict(type='text/html', http_status='404 Not Found'), open(os.path.join(os.path.dirname(__file__), '..', 'view', '404.html')).read()
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
        if _debug_enabled():
            logging.debug("REQ: %s env=%s query=%s", path,
                          redact_env_for_log(env), repr(redact_query_string(query)))
        query_args = {k: v[-1] for k, v in urllib.parse.parse_qs(query).items()}
        meta, content = try_these([echo_handler] + handlers + [err_handler], env, path, query_args, post)
        if (meta.get('type') or '').startswith('text/event-stream'):
            if _SSE_SLOTS.acquire(blocking=False):
                content = ClosingIterator(content, _SSE_SLOTS.release)
            else:
                close = getattr(content, 'close', None)
                if close is not None:
                    close()
                meta = dict(type='text/plain', http_status='503 Service Unavailable')
                content = 'Too many event streams; close a chat tab and retry.'
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
            pi = (pi.encode('latin-1') if isinstance(pi, str) else pi).decode('utf-8', errors='replace')
        except Exception as e:
            logging.warning('PATH_INFO decode failed: %r %s', pi, e)
        # local_on 重写：命中本机规范名的代理前缀剥掉改本地直读（见 ext/proxy），
        # 让 itab/链接可以固定带调用方定义的机器前缀。
        # 无前缀路径不强制跳转（用户 2026-09-01 拍板，撤销 se6t32 的 302）；
        # 需要统一地址的带前缀链接由使用方自行控制。
        try:
            from ext.proxy.proxy import rewrite_local
            pi = rewrite_local(pi)
        except Exception as e:
            logging.warning('rewrite_local failed: %r %s', pi, e)
        env['PATH_INFO'] = pi

        header, content = handle_request(env, pi, env['QUERY_STRING'], env['wsgi.input'])
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
    cert, keyfile = config_path('W_TLS_CERT'), config_path('W_TLS_KEY')
    if os.getenv('nossl') == '1':
        cert, keyfile = None, None
        logging.warning('nossl mode: run with danger')
    elif cert or keyfile:
        if not cert or not keyfile or not all(os.path.isfile(p) for p in (cert, keyfile)):
            raise ValueError('W_TLS_CERT and W_TLS_KEY must both name existing files')
    timeout = get_socket_timeout()
    logging.info("run use wsgiserver: socket timeout: %s", timeout)
    server = WSGIServer(app, host, port, certfile=cert, keyfile=keyfile, timeout=timeout, numthreads=HTTP_THREADS)
    fork_as_daemon(daemon)
    server.start()

def read_credential():
    path = config_path('W_AUTH_FILE', required=True)
    with open(path) as f:
        value = f.read().strip()
    if not value or ':' not in value:
        raise ValueError('W_AUTH_FILE must contain nonempty user:password credentials')
    return value
from wsgi_basic_auth import BasicAuth
def run_wsgi(app, host, port, daemon=False):
    os.environ['WSGI_AUTH_CREDENTIALS'] = read_credential()
    app = BasicAuth(app)
    run_use_wsgiserver(app, host, port, daemon)
