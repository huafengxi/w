#!/usr/bin/env python3
# In-process smoke harness: exercises the request pipeline without sockets/fork.
# Usage: cd <webroot> && fstab=<path> python3 w/test/smoke.py
import os, sys, io, logging, urllib.parse
logging.basicConfig(level=getattr(logging, os.getenv('log', 'WARNING').upper(), logging.WARNING),
                    format="%(levelname)s %(message)s")
WEBROOT = os.getenv('webroot', '/home/yuanqi.xhf/m')
os.chdir(WEBROOT)
if 'w' not in sys.path:
    sys.path.insert(0, 'w')
from stores.store import build_root_store
import core.handler as handler

root = build_root_store(os.getenv('fstab', 'w/stores/fstab'))
h = handler.Handler(root)

def req(path, qs=''):
    env = {'PATH_INFO': path, 'QUERY_STRING': qs, 'CONTENT_LENGTH': '0',
           'CONTENT_TYPE': 'application/x-www-form-urlencoded'}
    try:
        meta, content = h.handle_req(env, path, {k: v[-1] for k, v in urllib.parse.parse_qs(qs).items()}, io.BytesIO()) or (None, None)
        if hasattr(content, '__iter__') and not isinstance(content, (bytes, str)):
            content = b''.join(x if isinstance(x, bytes) else str(x).encode() for x in content)
        c = (content if isinstance(content, (bytes, str)) else repr(content))[:70]
        print('OK   %-24s %-10s type=%-12s len~%s :: %.50r' % (
            path, qs, (meta or {}).get('type'), (meta or {}).get('content_len'), c))
    except Exception as e:
        print('ERR  %-24s %-10s %r' % (path, qs, e))

if __name__ == '__main__':
    req('/w/readme.org')
    req('/w/readme.org', 'v=read')
    req('/w/readme.org', 'v=head')
    req('/w/', '')
    req('/w/', 'v=dir')
    req('/w/test/a.md')
    req('/vmap/')
    print('--- smoke done ---')
