# reverse proxy handler (0831-0937-sh7l)
#
# 路由规则声明在 ext/proxy/routes.json（与本文件同目录），格式见 README.md /
# design.md「reverse proxy」小节。规则按最长前缀匹配；文件缺失/为空/解析失败
# 均视为代理功能关闭（不影响既有管线），解析失败会记一条 warning。
#
# 规则文件按 mtime 热加载：每个请求检查 stat，变更才重读，无需重启服务。
#
# 转发语义（纯标准库 http.client 实现，不引三方依赖）：
# - 保留原方法与请求体；透传请求头（剔除 hop-by-hop），补 X-Forwarded-For /
#   X-Forwarded-Proto，Host 改写为上游；
# - 上游响应的 status/头/体原样回传（同样剔除 hop-by-hop）；
# - 流式透传（任务 xt2sj3）：上游响应 Content-Type 为 text/event-stream 或无
#   Content-Length 时，按块生成器透传（不设 content_len → wsgi 层走 chunked），
#   SSE/长连接不被憋住；其余响应保持一次性读完中转（原语义零回归）；
# - timeout 为 socket 级每操作超时：对流式连接即空闲超时（每次 recv 独立计时），
#   SSE 类长连接路由应配大于上游 keep-alive 周期的值（如 /mac/ 65s > 15s ping）；
# - 上游不可达/超时 → 502 Bad Gateway；上游返回什么就透传什么。
#
# 插入点：本 handler 挂在管线第一个业务位（echo 调试器之后、主路由之前），
# 位于全局 BasicAuth 之内 —— 未认证请求到不了这里（见 core/wsgi.py:run_wsgi）。

import json
import logging
import os
import http.client
import urllib.parse

# RFC 2616 §13.5.1 hop-by-hop headers（小写比较）
HOP_BY_HOP = frozenset([
    'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
    'te', 'trailers', 'transfer-encoding', 'upgrade',
])

_routes_file = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'routes.json')
# mtime 缓存：{'mtime': float|None, 'routes': list}
_cache = {'mtime': None, 'routes': [], 'loaded_key': None}


def _parse_rules(obj):
    """接受顶层 list，或 {'routes': [...]}；返回法规则列表（忽略不合法条目）。"""
    if isinstance(obj, dict):
        obj = obj.get('routes', [])
    if not isinstance(obj, list):
        raise ValueError('routes must be a list (or {"routes": [...]})')
    rules = []
    for r in obj:
        if not isinstance(r, dict):
            raise ValueError('each route must be an object')
        prefix, upstream = r.get('prefix'), r.get('upstream')
        if not isinstance(prefix, str) or not prefix.startswith('/'):
            raise ValueError('route.prefix must be a string starting with "/": %r' % (r,))
        u = urllib.parse.urlparse(upstream if isinstance(upstream, str) else '')
        if u.scheme not in ('http', 'https') or not u.netloc:
            raise ValueError('route.upstream must be http(s)://host[:port]: %r' % (r,))
        rules.append({
            'prefix': prefix,
            'upstream': u,
            'strip_prefix': bool(r.get('strip_prefix', False)),
            'timeout': float(r.get('timeout', 10)),
        })
    return rules


def load_routes():
    """按 mtime 缓存读取规则文件。缺失/空/解析失败 → 返回 []（代理关闭）。"""
    try:
        st = os.stat(_routes_file)
    except OSError:
        if _cache['routes']:
            logging.warning('proxy: routes file %s gone, proxy disabled', _routes_file)
        _cache.update(mtime=None, routes=[], loaded_key=None)
        return []
    if st.st_mtime == _cache['mtime']:
        return _cache['routes']
    key = (st.st_mtime, st.st_size)
    if key == _cache['loaded_key']:
        _cache['mtime'] = st.st_mtime
        return _cache['routes']
    try:
        with open(_routes_file) as f:
            text = f.read()
        routes = _parse_rules(json.loads(text)) if text.strip() else []
    except Exception as e:
        logging.warning('proxy: routes file %s invalid (%s), proxy disabled', _routes_file, e)
        routes = []
    else:
        logging.info('proxy: (re)loaded %d route(s) from %s', len(routes), _routes_file)
    _cache.update(mtime=st.st_mtime, routes=routes, loaded_key=key)
    return routes


def match_route(routes, path):
    """最长前缀匹配；未命中返回 None。"""
    best = None
    for r in routes:
        p = r['prefix']
        if path == p or path.startswith(p):
            if best is None or len(p) > len(best['prefix']):
                best = r
    return best


def _req_headers(env, upstream_netloc):
    """WSGI env → 上游请求头：透传（剔 hop-by-hop），改写 Host，补 X-Forwarded-*。"""
    headers = {}
    for k, v in env.items():
        if k.startswith('HTTP_'):
            name = k[5:].replace('_', '-').title()
            if name.lower() in HOP_BY_HOP:
                continue
            headers[name] = v
    ctype = env.get('CONTENT_TYPE')
    if ctype:
        headers['Content-Type'] = ctype
    headers['Host'] = upstream_netloc
    proto = env.get('wsgi.url_scheme') or 'http'
    headers['X-Forwarded-Proto'] = proto
    remote = env.get('REMOTE_ADDR', '')
    if remote:
        prev = headers.get('X-Forwarded-For')
        headers['X-Forwarded-For'] = '%s, %s' % (prev, remote) if prev else remote
    return headers


def _resp_headers(resp):
    """上游响应头 → 透传列表（剔 hop-by-hop；Content-Type/Length 由 wsgi 层统一出）。
    X-Content-Type-Options 亦剔除（任务 kqhweh，ogwtb4 评审建议 1）：wsgi 层对全部响应
    统一加 nosniff（core/wsgi.py），上游（同为 w 服务）再带一个会出现重复头。"""
    out = []
    for k, v in resp.getheaders():
        lk = k.lower()
        if lk in HOP_BY_HOP or lk in ('content-type', 'content-length',
                                      'x-content-type-options'):
            continue
        out.append((k, v))
    return out


def forward(env, path, rule):
    u = rule['upstream']
    target = path[len(rule['prefix']):] if rule['strip_prefix'] else path
    if not target.startswith('/'):
        target = '/' + target
    qs = env.get('QUERY_STRING', '')
    if qs:
        target = '%s?%s' % (target, qs)
    method = env.get('REQUEST_METHOD', 'GET')
    length = int(env.get('CONTENT_LENGTH') or 0)
    body = env['wsgi.input'].read(length) if length > 0 else b''
    headers = _req_headers(env, u.netloc)
    headers['Content-Length'] = str(len(body))
    conn_cls = http.client.HTTPSConnection if u.scheme == 'https' else http.client.HTTPConnection
    conn = conn_cls(u.hostname, u.port or (443 if u.scheme == 'https' else 80),
                    timeout=rule['timeout'])
    streaming = False
    try:
        conn.request(method, target, body=body, headers=headers)
        resp = conn.getresponse()
        ctype = resp.getheader('Content-Type') or ''
        # 流式判定（任务 xt2sj3）：SSE 明示，或无 Content-Length 的长响应体。
        # http.client 对 chunked 上游透明解块，读面统一按普通流读。
        is_sse = ctype.split(';')[0].strip().lower() == 'text/event-stream'
        streaming = (
            is_sse
            or (resp.getheader('Content-Length') is None
                and resp.status not in (204, 304) and method != 'HEAD')
        )
        # 日志标签按 ctype 细化（任务 kqhweh，ogwtb4 评审建议 2）：SSE 记 STREAM，
        # 无 Content-Length 的普通响应（如 JSON rpc）记 CHUNKED，不再混称 STREAM。
        stream_tag = 'PROXY STREAM' if is_sse else 'PROXY CHUNKED'
        if streaming:
            # 不设 content_len → wsgi 层出 Transfer-Encoding: chunked，逐块透传。
            # 连接由生成器生命周期接管（外层不 close）。
            meta = {
                'type': ctype or 'application/octet-stream',
                'http_status': '%d %s' % (resp.status, resp.reason or ''),
                'extra_headers': _resp_headers(resp),
            }
            logging.info('%s: %s %s -> %s://%s%s => %d',
                         stream_tag, method, path, u.scheme, u.netloc, target, resp.status)

            def gen():
                nbytes = 0
                # read1：至多一次底层 recv（http.client 的 read(n) 会缓冲到凑满 n，
                # 对 SSE 会憋帧；read1 有数据即返，保持增量；chunked 上游透明解块）。
                rd = getattr(resp, 'read1', resp.read)
                try:
                    while True:
                        chunk = rd(65536)
                        if not chunk:
                            break
                        nbytes += len(chunk)
                        yield chunk
                except Exception as e:
                    # 响应头已出、无法再改状态码：断流 + 日志，前端自带重连兜底。
                    logging.warning('%s CUT: %s %s -> %s://%s after %d bytes: %s',
                                    stream_tag, method, path, u.scheme, u.netloc, nbytes, e)
                finally:
                    try:
                        conn.close()
                    except Exception:
                        pass

            return meta, gen()
        data = resp.read()  # 非流式：一次性读完中转（原语义）
        meta = {
            'type': resp.getheader('Content-Type') or 'application/octet-stream',
            'http_status': '%d %s' % (resp.status, resp.reason or ''),
            'content_len': len(data),
            'extra_headers': _resp_headers(resp),
        }
        logging.info('PROXY: %s %s -> %s://%s%s => %d (%d bytes)',
                     method, path, u.scheme, u.netloc, target, resp.status, len(data))
        return meta, data
    except Exception as e:
        logging.warning('PROXY FAIL: %s %s -> %s://%s: %s', method, path, u.scheme, u.netloc, e)
        msg = '502 Bad Gateway: upstream %s://%s unreachable (%s)' % (u.scheme, u.netloc, e)
        return {'type': 'text/plain', 'http_status': '502 Bad Gateway'}, msg
    finally:
        if not streaming:
            try:
                conn.close()
            except Exception:
                pass


def proxy_handler(env, path, query, post):
    """管线业务第一站：命中代理规则则转发，否则返回 None 落回既有管线。"""
    routes = load_routes()
    if not routes:
        return None
    rule = match_route(routes, path)
    if not rule:
        # 裸前缀兜底（任务 kqhweh，ogwtb4 评审建议 3）：/mac（无尾斜杠）不匹配
        # '/mac/' 前缀，会落回既有管线渲染兜底聊天页（易误导）。显式 302 重定向到
        # 带尾斜杠的前缀，进入正常代理路由。
        for r in routes:
            p = r['prefix']
            if p.endswith('/') and path == p.rstrip('/'):
                logging.info('PROXY REDIRECT: %s -> %s', path, p)
                return ({'type': 'text/html',
                         'http_status': '302 Found',
                         'extra_headers': [('Location', p)]},
                        '<a href="%s">%s</a>' % (p, p))
        return None
    return forward(env, path, rule)
