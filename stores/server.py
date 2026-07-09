#!/usr/bin/env python3
"""stores/server.py -- minimal self-contained VFS server.

Mounts stores/fstab and serves a tiny HTTP API on top of RootStore:

  GET  /path            -> read (full or Range)
  GET  /path?v=head     -> head metadata (json)
  GET  /path?v=dir      -> dir listing
  POST /path?v=write    -> write request body to store

No ext loading, no auth, no ssl -- a simple stdlib wsgiref loop.
Run:  python3 w/stores/server.py [port] [fstab]
"""
import os
import sys
import json
import logging
from wsgiref.simple_server import make_server
from urllib.parse import parse_qs


def parse_qs_to_dict(qs):
    return {k: v[-1] for k, v in parse_qs(qs).items()}


def build_app(root):
    def handle(env, response):
        path = env.get('PATH_INFO', '/') or '/'
        try:
            path = path.encode('latin-1').decode('utf-8')
        except Exception:
            pass
        query = parse_qs_to_dict(env.get('QUERY_STRING', ''))
        v = query.get('v', 'read')
        try:
            if v == 'write':
                n = int(env.get('CONTENT_LENGTH', 0) or 0)
                root.write(path, env['wsgi.input'].read(n))
                response('200 OK', [('Content-Type', 'text/plain')])
                return [b'OK']
            if v == 'head':
                meta = root.head(path)
                if meta is None:
                    response('404 Not Found', [('Content-Type', 'text/plain')])
                    return [b'not found']
                body = json.dumps({k: str(val) for k, val in meta.items()}).encode()
                response('200 OK', [('Content-Type', 'application/json')])
                return [body]
            if v == 'dir':
                p = path if path.endswith('/') else path + '/'
                data = root.read(p)
                if isinstance(data, str):
                    data = data.encode()
                elif data is None:
                    data = b''
                response('200 OK', [('Content-Type', 'text/plain')])
                return [data]
            meta = root.head(path)
            if meta is None:
                response('404 Not Found', [('Content-Type', 'text/plain')])
                return [b'not found']
            range_req = env.get('HTTP_RANGE', '')
            total, start, end, data = root.lazy_read(path, range_req)
            mime = meta.get('type') or 'application/octet-stream'
            if mime.startswith('text'):
                mime = mime + '; charset=utf-8'
            status = '200 OK'
            headers = [('Content-Type', mime), ('Accept-Ranges', 'bytes'),
                       ('Content-Length', str(end - start))]
            if range_req:
                status = '206 Partial Content'
                headers.append(('Content-Range', 'bytes %d-%d/%d' % (start, end - 1, total)))
            response(status, headers)
            if isinstance(data, bytes):
                return [data]
            if isinstance(data, str):
                return [data.encode()]
            return data
        except Exception as e:
            logging.exception('req failed: %s %s', path, query)
            response('500 Internal Server Error', [('Content-Type', 'text/plain')])
            return [str(e).encode()]
    return handle


def main():
    logging.basicConfig(level=getattr(logging, os.getenv('log', 'info').upper()),
                        format='%(levelname)s %(message)s')
    here = os.path.dirname(os.path.realpath(__file__))
    webroot = os.path.dirname(os.path.dirname(here))
    os.chdir(webroot)
    if 'w' not in sys.path:
        sys.path.insert(0, 'w')
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    fstab = sys.argv[2] if len(sys.argv) > 2 else 'w/stores/fstab'
    from stores.store import build_root_store
    root = build_root_store(fstab)
    httpd = make_server('', port, build_app(root))
    logging.info('stores/server.py listening on :%d fstab=%s', port, fstab)
    httpd.serve_forever()


if __name__ == '__main__':
    main()
