import traceback
import logging
import urllib.parse
import cgi
import re

from vmap import build as vmap_build
import mime

def parse_post(ctype, post, post_size):
    if ctype.startswith('multipart/form-data'):
        ctype, pdict = cgi.parse_header(ctype)
        return cgi.parse_multipart(post, pdict)
    else:
        return urllib.parse.parse_qs(post.read(post_size).decode('utf-8'))

def parse_post_to_dict(ctype, post, post_size):
    return dict((k, v[-1]) for k, v in list(parse_post(ctype, post, post_size).items()))

def prepare_args(env, query, post):
    if re.search('Googlebot|Baiduspider', env.get('HTTP_USER_AGENT', ""), re.I):
        query.update(is_crawled_by_spider=True)
    if re.search('curl', env.get('HTTP_USER_AGENT', ""), re.I):
        query.update(is_crawled_by_curl=True)
    post_key = query.get('post')
    if post_key:
        args = {post_key: post.read()}
    else:
        args = parse_post_to_dict(env.get('CONTENT_TYPE', 'application/x-www-form-urlencoded'), post, int(env.get('CONTENT_LENGTH', '') or 0))
    args.update(query)
    args.update(_range_req=env.get('HTTP_RANGE', ''))
    return args

def rpc_encode(result, tb):
    if tb: return 'Exception:\n%s' % (str(tb))
    else: return str(result)

def safe_sub(text, **__kw):
    return string.Template(text.decode()).safe_substitute(__kw).encode()

def get_meta(store, path):
    meta = store.head(path) or dict()
    if 'path' in meta:
        url_info = urllib.parse.urlparse(meta.get('path'))
        meta.update({k: v[-1] for k, v in urllib.parse.parse_qs(url_info.query).items()}, path=url_info.path)
    else:
        meta.update(path=path)
    return meta

class VMap(dict):
    def __init__(self):
        dict.__init__(self, vmap_build())
    def translate(self, path, meta, args):
        mime = meta.get('type')
        if not mime: return path
        vpath = self.get(mime)
        if meta.get('is_crawled_by_spider', False):
            vpath = self.get(mime + '/bot', vpath)
        if meta.get('is_crawled_by_curl', False):
            vpath = self.get(mime + '/curl', vpath)
        if vpath:
            url_info =  urllib.parse.urlparse(vpath)
            vpath, view_query_args = url_info.path, {k: v[-1] for k, v in urllib.parse.parse_qs(url_info.query).items()}
            args.update(view_query_args, src=path)
        return vpath or path

def run_script(store, path, args):
    ns = dict(
        os=os, sys=sys, re=re, string=string, json=json, io=io, time=time,
        logging=logging, urllib=urllib, itertools=itertools, copy=copy,
        Popen=Popen, PIPE=PIPE, STDOUT=STDOUT, NULLFD=NULLFD,
        dict_updated=dict_updated, popen=popen, sub=sub,
        response_part_file=response_part_file, build_dict=build_dict,
        run_script=run_script,
    )
    exec(compile(store.read(path), path[1:], mode='exec'), ns)
    output = ns['interp'](store, **args)
    if type(output) != tuple:
        return dict(type='text/plain'), output
    else:
        return output

def response_part_file(store, path, meta=None, range_req=''):
    def build_range_resp_header(total, start, end):
        return 'Content-Range', 'bytes %d-%d/%d'%(start, end-1, total)
    if meta == None: meta = (store.head(path) or dict(type='text/plain'))
    total_bytes, start, end, data = store.lazy_read(path, range_req)
    if range_req:
        meta.update(http_status='206 Partial Content', content_len=end-start, range_resp_header=build_range_resp_header(total_bytes, start, end))
        return meta, data
    else:
        meta.update(content_len=total_bytes)
        return meta, data

def do_view(store, path, meta, args):
    mime = meta['type']
    range_req = args.get('_range_req')
    if mime == 'script':
        if range_req: raise Exception('not support range request: %s'%(path))
        return run_script(store, path, args)
    elif mime == 'text/html':
        if range_req: raise Exception('not support range request: %s'%(path))
        # ARGS_JSON（任务 xt2sj3）：以 JSON 注入全量 args（含 vmap 写入的 src），
        # 供视图感知代理前缀（浏览器 location.pathname 与 src 求差）。
        # '</' 转义防 JSON 内容内嵌 </script> 逃逸。
        return meta, [safe_sub(store.read(path), QUERY_ARGS=repr(args),
                               ARGS_JSON=json.dumps(args, default=str).replace('</', '<\\/'))]
    else:
        return response_part_file(store, path, meta, range_req)

# 通用 secret 参数名（任务 fnv23x / todo t-m2v9②）：按**参数名**脱敏，命中值不进日志。
# 词边界式匹配（前后不得紧邻字母）——`user_nonce`/`next_token`/`api_key`/`dashscope_api_key`
# 命中，而 `author`/`monkey`/`keyword` 之类不误伤。键名族参考 env/ 凭据命名
# （PI_WEB_PASSWORD、RSH_TOKEN、DASHSCOPE_API_KEY、TOKENFLOW_API_KEY、PIKPAK_PASSWORD、
# WEBDAV_PASSWORD、hf-token、civitai token）与端点侧一次性 nonce。
_SECRET_ARG_RE = re.compile(
    r'(?<![a-z])('
    r'nonce|tokens?|passw(?:or)?d|pwd|secrets?|credentials?|cookies?|'
    r'auth|authorization|signatures?|sig|(?:api|access|private|secret|session)[_-]?key|key'
    r')(?![a-z])', re.I)

def _is_secret_arg(name):
    return bool(_SECRET_ARG_RE.search(str(name).lower()))

def _redact_secret_args(args):
    """按参数名脱敏 secret 值（保留长度/计数便于排障，明文一律不落日志）。
    无命中时原对象直返（不做无谓复制）；有命中时返回浅拷贝——只作用于日志面，
    不触碰透传给执行路径的原始 args。"""
    hits = [k for k in args if _is_secret_arg(k)]
    if not hits:
        return args
    red = dict(args)
    for k in hits:
        v = red[k]
        if isinstance(v, str):
            red[k] = '<redacted %d chars>' % len(v)
        elif isinstance(v, (list, tuple, set)):
            red[k] = '<redacted %d items>' % len(v)
        elif isinstance(v, dict):
            red[k] = '<redacted %d keys>' % len(v)
        elif v is None:
            red[k] = v
        else:
            red[k] = '<redacted>'
    return red

# 内容型键名（任务 4ob5de 项 5）：写类 RPC（ext/fileops/rpc/builtin_write.py 的
# store_content/text/file，ext/shell/rpc/sh.py 的 input 等）把**正文**当参数传，
# `RESOLVE:` 行按 `%.2000s` 明文落 run/logs/web.log → 写含 secret 的文件即泄漏。
# 日志面只留长度摘要，键名与非内容字段（src/target/v/op）原样保留以便排障。
_CONTENT_ARG_NAMES = frozenset([
    'store_content', 'text', 'content', 'body', 'data', 'payload', 'file', 'input'])

def _summarize_content_args(args):
    """内容型键名 → 长度摘要（例 `text=<1234 chars>`）。只作用于日志面。
    `file` 可能是**尚未读取的文件对象**（upload 分支）：一律不 read——读了就把流
    吃掉，破坏执行路径。已被 ① 脱敏成 `<redacted …>` 的值原样保留，不二次摘要。"""
    hits = [k for k in args if str(k).lower() in _CONTENT_ARG_NAMES]
    if not hits:
        return args
    red = dict(args)
    for k in hits:
        v = red[k]
        if isinstance(v, str):
            if v.startswith('<redacted '):
                continue
            red[k] = '<%d chars>' % len(v)
        elif isinstance(v, bytes):
            red[k] = '<%d bytes>' % len(v)
        elif hasattr(v, 'read'):
            red[k] = '<file object>'
        elif isinstance(v, (list, tuple, set)):
            red[k] = '[%d items]' % len(v)
        elif isinstance(v, dict):
            red[k] = '{%d keys}' % len(v)
        elif v is None:
            red[k] = v
        else:
            red[k] = '<%s>' % type(v).__name__
    return red

def redact_query_string(qs):
    """原始 query string 的日志面脱敏（任务 4ob5de 项 4，供 core/wsgi.py 的 DEBUG
    行复用同一套键名族）：按 `&` 拆条，键名命中 _SECRET_ARG_RE 的值只留长度摘要，
    其余原样保留（排障可用）。percent-encoded 的键名先解码再判定（`api%5Fkey=`
    之类不绕过）；不改动、不消费任何执行路径数据。"""
    if not qs:
        return qs
    out = []
    for part in str(qs).split('&'):
        k, sep, v = part.partition('=')
        if sep and _is_secret_arg(urllib.parse.unquote(k)):
            v = '<redacted %d chars>' % len(urllib.parse.unquote(v))
        out.append(k + sep + v)
    return '&'.join(out)

# WSGI environ 里承载原始 query 的键（任务 4ob5de 项 4）：DEBUG 面直打整个 environ
# 时，光靠键名族拦不住它们——QUERY_STRING 本体就是 nonce/token 的载体。
_ENV_QUERY_KEYS = ('QUERY_STRING', 'RAW_QUERY_STRING', 'REQUEST_URI')

def redact_env_for_log(env):
    """WSGI environ 的日志面脱敏，两层：
    ① 键名族（_SECRET_ARG_RE）：HTTP_COOKIE / HTTP_AUTHORIZATION / … 的值只留长度；
    ② 原始 query 载体（QUERY_STRING / REQUEST_URI 等）再过一遍 redact_query_string。
    只作用于日志面，不触碰执行路径用的 env。"""
    red = _redact_secret_args(env)
    hits = [k for k in _ENV_QUERY_KEYS if isinstance(red.get(k), str) and red[k]]
    if not hits:
        return red
    red = dict(red)
    for k in hits:
        v = red[k]
        if '?' in v:      # REQUEST_URI 形态 path?query：只脱 query 段
            head, sep, qs = v.partition('?')
            red[k] = head + sep + redact_query_string(qs)
        else:
            red[k] = redact_query_string(v)
    return red

def _redact_log_args(args):
    """日志脱敏，三层：
    ① 通用 secret 参数名（任务 fnv23x）：任何请求的 args 里名字命中 _SECRET_ARG_RE
       的值（nonce/token/password/api_key/…）一律替换为 `<redacted N chars>`，
       使 `RESOLVE:` 行不再明文落 run/logs/web.log。
    ② op=cmd 深度脱敏（任务 0829-2134-n8i2）：只记 type + 各字段长度/计数摘要，
       prompt 文本（message 等字符串字段）与 images 一律不全文入日志。
       例：{"type": "prompt", "message": "<12 chars>", "images": "[1 x image/png]"}
       steer/follow_up 及其它 cmd 类型同口径。
    ③ 内容型键名长度摘要（任务 4ob5de 项 5）：`store_content`/`text`/`file`/`input`
       等承载**正文**的键只记 `<N chars>`，写类 RPC 的正文不再随 `RESOLVE:` 落日志。
    三层都只作用于日志面，不触碰透传给执行路径的原始 args。"""
    args = _summarize_content_args(_redact_secret_args(args))
    if args.get('op') != 'cmd':
        return args
    cmd = args.get('cmd')
    if not isinstance(cmd, str) or not cmd:
        return args
    try:
        obj = json.loads(cmd)
    except Exception:
        return dict(args, cmd=cmd[:80] + '…<truncated>')
    if not isinstance(obj, dict):
        return dict(args, cmd=cmd[:80] + '…<truncated>')
    red = {}
    for k, v in obj.items():
        if k == 'type':
            red[k] = v
        elif k == 'images' and isinstance(v, list):
            mimes = ','.join((i.get('mimeType') or '?') if isinstance(i, dict) else '?'
                             for i in v)
            red[k] = '[%d x %s]' % (len(v), mimes) if v else '[]'
        elif isinstance(v, str):
            red[k] = '<%d chars>' % len(v)
        elif isinstance(v, list):
            red[k] = '[%d items]' % len(v)
        elif isinstance(v, dict):
            red[k] = '{%d keys}' % len(v)
        else:
            red[k] = v
    return dict(args, cmd=json.dumps(red, ensure_ascii=False))

def build_dict(*__args, **__kw):
    d = dict()
    for i in __args:
        d.update(i)
    d.update(__kw)
    return d

class Handler:
    def __init__(self, store):
        self.store = store
        self.vmap = VMap()
    def handle_req(self, env, path, query, post):
        return self.do_req(path, prepare_args(env, query, post))
    def do_req(self, path, args):
        # logging.debug('HANDLE_REQ: path=%s args=%.200s', path, args)
        meta = get_meta(self.store, path)
        # 文件缺失时无 type：若后缀有 frag 显式 mime 映射且该 mime 在 vmap 有视图，
        # 则兜底渲染到对应视图（如 .jsonl → 聊天窗，0830-1924-ft6s）；未映射仍 404。
        if not meta.get('type'):
            em = mime.guess_explicit(meta['path'])
            if em and self.vmap.get(em):
                meta['type'] = em
        vmeta = copy.copy(meta)
        if 'v' in args: meta.update(type=args['v'])
        if 'v' in meta: meta.update(type=meta['v'])
        vpath = self.vmap.translate(meta['path'], meta, args)
        if vpath != path:
            vmeta = get_meta(self.store, vpath)
        logging.info('RESOLVE: meta=%s vmeta=%s args=%.2000s', meta, vmeta, _redact_log_args(args))
        if not vmeta.get('type'):
            return None
        try:
            resp_meta, resp_body = do_view(self.store, vpath, vmeta, build_dict(meta, vmeta, args))
        except Exception as e:
            logging.error(traceback.format_exc())
            return dict(type='text/plain', http_status='500 Internal Server Error'), rpc_encode(None, traceback.format_exc())
        # 聊天窗页 no-cache（任务 daigh0）：浏览器旧 tab 会驻留旧 JS 直到手动刷新，
        # no-cache 让每次访问都回源校验。仅命中 sessiond 聊天视图（?v=chat、
        # .jsonl/.agent/spec.json 各入口同款映射），不动其它资源缓存策略。
        if vpath == '/sessiond/view/index.html':
            resp_meta = dict(resp_meta, extra_headers=(resp_meta.get('extra_headers') or [])
                             + [('Cache-Control', 'no-cache')])
        return resp_meta, resp_body

# setup script deps
import sys
import os
import string
import json
import copy
import io
from subprocess import Popen, PIPE, STDOUT
import itertools
import urllib.request, urllib.parse, urllib.error
import time
NULLFD = open('/dev/null')
def dict_updated(d, **kw):
    new_dict = copy.copy(d)
    new_dict.update(**kw)
    return new_dict

def popen(cmd, input=None, env=None):
    p = Popen(cmd, cwd='.', shell=True, stdin=PIPE, stdout=PIPE, stderr=PIPE, env=env)
    output, stderr = p.communicate(input)
    if 0 != p.wait():
        raise Exception('popen fail: %s %s'%(cmd, stderr))
    else:
        return output

def sub(tpl, __d={}, **kw):
    return string.Template(tpl).safe_substitute(__d, **kw)

