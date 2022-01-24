import traceback
import logging
import urllib.parse
import cgi
import re

def parse_qs_to_dict(qs):
    return dict((k, v[-1]) for k, v in list(urllib.parse.parse_qs(qs).items()))

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
        args = {post_key: post}
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
        meta.update(parse_qs_to_dict(url_info.query), path=url_info.path)
    else:
        meta.update(path=path)
    return meta

class VMap(dict):
    def __init__(self, store):
        dict.__init__(self, eval(store.read('/vmap/')))
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
            vpath, view_query_args = url_info.path, parse_qs_to_dict(url_info.query)
            args.update(view_query_args, src=path)
        return vpath or path

def run_script(store, path, args):
    exec(compile(store.read(path), path[1:], mode='exec'), globals())
    output = interp(store, **args)
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
        return meta, [safe_sub(store.read(path), QUERY_ARGS=repr(args))]
    else:
        return response_part_file(store, path, meta, range_req)

def build_dict(*__args, **__kw):
    d = dict()
    for i in __args:
        d.update(i)
    d.update(__kw)
    return d

class Handler:
    def __init__(self, store):
        self.store = store
        self.vmap = VMap(store)
    def handle_req(self, env, path, query, post):
        return self.do_req(path, prepare_args(env, query, post))
    def do_req(self, path, args):
        # logging.debug('HANDLE_REQ: path=%s args=%.200s', path, args)
        meta = get_meta(self.store, path)
        vmeta = copy.copy(meta)
        if 'v' in args: meta.update(type=args['v'])
        if 'v' in meta: meta.update(type=meta['v'])
        vpath = self.vmap.translate(meta['path'], meta, args)
        if vpath != path:
            vmeta = get_meta(self.store, vpath)
        logging.info('RESOLVE: meta=%s vmeta=%s args=%.200s', meta, vmeta, args)
        if not vmeta.get('type'):
            return None
        try:
            return do_view(self.store, vpath, vmeta, build_dict(meta, vmeta, args))
        except Exception as e:
            logging.error(traceback.format_exc())
            return dict(type='text/plain', http_status='500 Internal Server Error'), rpc_encode(None, traceback.format_exc())

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
#import tsql
import archive
import local_file_operator as lfop
NULLFD = open('/dev/null')
def dict_updated(d, **kw):
    new_dict = copy.copy(d)
    new_dict.update(**kw)
    return new_dict

def make_wsgi_fetcher(wsgi_handler):
    def dummy_response(*args):pass
    def fetch(host, path, query_string):
        env = {'PATH_INFO': path, 'CONTENT_LENGTH': '0', 'wsgi.input': io.StringIO(), 'QUERY_STRING': query_string}
        return ''.join(wsgi_handler(env, dummy_response))
    return fetch

def set_wsgi_fetcher(app):
    archive.fetch = make_wsgi_fetcher(app)

def cmd_wrapper(head):
    if os.name != 'nt':
        return head
    if head.endswith('.es'):
        print('cmd_wrapper=%s'%(head))
        return 'emacs --script %s'%(popen('where %s'%(head)))
    return head

def popen(cmd, input=None, env=None):
    p = Popen(cmd, cwd='.', shell=True, stdin=PIPE, stdout=PIPE, stderr=PIPE, env=env)
    output, stderr = p.communicate(input)
    if 0 != p.wait():
        raise Exception('popen fail: %s %s'%(cmd, stderr))
    else:
        return output

def sub(tpl, __d={}, **kw):
    return string.Template(tpl).safe_substitute(__d, **kw)

from album import AlbumDB
