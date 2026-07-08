#!/usr/bin/env python
'''
dav.py PORT [LOG_FILE]

Standalone WebDAV server exposing the RootStore from w/fstab.
Auth + SSL come from wsgi.run_wsgi (BasicAuth, ~/.auth/*).

  log=info ./dav.py 8081
  nossl=1 log=debug ./dav.py 8081
'''
import logging
import os
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from email.utils import formatdate

# repo root = dir containing the `core` package (.../w); this file lives at
# .../w/ext/webdav/dav.py, so three dirnames up. Put it on sys.path so
# `import core.*` and `import ext.*` resolve when run as a standalone script.
_repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
for p in (_repo_root, os.path.join(_repo_root, 'pylib')):
    if p not in sys.path:
        sys.path.insert(0, p)

from core.store import _path_is_dir, StoreException

ALLOWED = 'OPTIONS, GET, HEAD, PROPFIND, PROPPATCH, PUT, DELETE, MKCOL, COPY, MOVE, LOCK, UNLOCK'

ET.register_namespace('D', 'DAV:')

def _http_date(t):
    if t is None: return formatdate(usegmt=True)
    if isinstance(t, (int, float)): return formatdate(t, usegmt=True)
    if hasattr(t, 'timestamp'):
        try: return formatdate(t.timestamp(), usegmt=True)
        except Exception: return formatdate(usegmt=True)
    return str(t)

def _href(path, is_dir=False):
    h = urllib.parse.quote(path)
    if is_dir and not h.endswith('/'): h += '/'
    return h

def _decode_path(env):
    p = env.get('PATH_INFO') or '/'
    try:
        if isinstance(p, str):
            p = p.encode('latin-1').decode('utf-8', errors='replace')
    except Exception:
        pass
    return p

def _send(sr, status, headers=(), content_length=None):
    h = list(headers) + [('DAV', '1, 2')]
    if content_length is not None:
        h.append(('Content-Length', str(content_length)))
    sr(status, h)

def _resp(sr, status, headers=(), body=b''):
    if isinstance(body, str): body = body.encode('utf-8')
    _send(sr, status, headers, content_length=len(body))
    return [body] if body else []

def _read_body(env):
    n = int(env.get('CONTENT_LENGTH') or 0)
    if not n: return b''
    return env['wsgi.input'].read(n)

def _is_dir(meta):
    return bool(meta) and meta.get('type') == 'dir'

def _list_children(rootstore, path):
    if not path.endswith('/'): path = path + '/'
    data = rootstore.read(path)
    if isinstance(data, bytes): data = data.decode('utf-8', 'replace')
    if not data: return []
    out = []
    for line in data.split('\n'):
        if not line or line in ('../', './'): continue
        out.append(path + line)
    return out

def _propfind_xml(entries):
    ms = ET.Element('{DAV:}multistatus')
    for href_path, meta in entries:
        is_dir = _is_dir(meta)
        resp = ET.SubElement(ms, '{DAV:}response')
        ET.SubElement(resp, '{DAV:}href').text = _href(href_path, is_dir)
        propstat = ET.SubElement(resp, '{DAV:}propstat')
        prop = ET.SubElement(propstat, '{DAV:}prop')
        rt = ET.SubElement(prop, '{DAV:}resourcetype')
        if is_dir:
            ET.SubElement(rt, '{DAV:}collection')
        else:
            ET.SubElement(prop, '{DAV:}getcontentlength').text = str(meta.get('size') or 0)
            ET.SubElement(prop, '{DAV:}getcontenttype').text = meta.get('type') or 'application/octet-stream'
        ET.SubElement(prop, '{DAV:}getlastmodified').text = _http_date(meta.get('modified'))
        ET.SubElement(prop, '{DAV:}displayname').text = os.path.basename(href_path.rstrip('/')) or '/'
        ET.SubElement(propstat, '{DAV:}status').text = 'HTTP/1.1 200 OK'
    return b'<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(ms, encoding='utf-8')

def _dest_path(env):
    dest = env.get('HTTP_DESTINATION', '')
    if not dest: return None
    u = urllib.parse.urlparse(dest)
    return urllib.parse.unquote(u.path)

def make_dav_app(rootstore):
    def options(env, sr, path):
        return _resp(sr, '200 OK', [('Allow', ALLOWED), ('MS-Author-Via', 'DAV')])

    def propfind(env, sr, path):
        depth = env.get('HTTP_DEPTH', 'infinity')
        meta = rootstore.head(path) or {'type': None}
        if not meta.get('type'):
            return _resp(sr, '404 Not Found')
        entries = [(path, meta)]
        if depth != '0' and _is_dir(meta):
            for child in _list_children(rootstore, path):
                cmeta = rootstore.head(child) or {'type': None}
                if cmeta.get('type'):
                    entries.append((child, cmeta))
        body = _propfind_xml(entries)
        return _resp(sr, '207 Multi-Status', [('Content-Type', 'application/xml; charset=utf-8')], body)

    def proppatch(env, sr, path):
        body = b'<?xml version="1.0" encoding="utf-8"?>\n<D:multistatus xmlns:D="DAV:"></D:multistatus>'
        return _resp(sr, '207 Multi-Status', [('Content-Type', 'application/xml; charset=utf-8')], body)

    def get(env, sr, path):
        is_head = env.get('REQUEST_METHOD', 'GET').upper() == 'HEAD'
        meta = rootstore.head(path) or {'type': None}
        if not meta.get('type'):
            return _resp(sr, '404 Not Found')
        if _is_dir(meta):
            data = rootstore.read(path) or b''
            if isinstance(data, str): data = data.encode('utf-8')
            return _resp(sr, '200 OK', [('Content-Type', 'text/plain; charset=utf-8')], b'' if is_head else data)
        range_req = env.get('HTTP_RANGE', '')
        total, start, end, data = rootstore.lazy_read(path, range_req)
        content_len = (end - start) if range_req else total
        headers = [('Content-Type', meta.get('type') or 'application/octet-stream'),
                   ('Accept-Ranges', 'bytes')]
        if range_req:
            status = '206 Partial Content'
            headers.append(('Content-Range', 'bytes %d-%d/%d' % (start, end - 1, total)))
        else:
            status = '200 OK'
        _send(sr, status, headers, content_length=content_len)
        if is_head or data is None: return []
        if isinstance(data, bytes): return [data]
        if isinstance(data, str): return [data.encode('utf-8')]
        return (c if isinstance(c, bytes) else c.encode('utf-8') for c in data)

    def put(env, sr, path):
        if _path_is_dir(path):
            return _resp(sr, '409 Conflict')
        rootstore.write(path, _read_body(env))
        return _resp(sr, '201 Created')

    def delete(env, sr, path):
        rootstore.delete(path)
        return _resp(sr, '204 No Content')

    def mkcol(env, sr, path):
        if not callable(getattr(rootstore, 'mkdir', None)):
            return _resp(sr, '403 Forbidden')
        try:
            rootstore.mkdir(path)
            return _resp(sr, '201 Created')
        except Exception as e:
            logging.warning('mkcol %s: %s', path, e)
            return _resp(sr, '403 Forbidden')

    def move(env, sr, path):
        dest = _dest_path(env)
        if not dest: return _resp(sr, '400 Bad Request')
        if rootstore.mv(path, dest) is None:
            try:
                data = rootstore.read(path)
                rootstore.write(dest, data)
                rootstore.delete(path)
            except Exception as e:
                logging.warning('move fallback %s -> %s: %s', path, dest, e)
                return _resp(sr, '502 Bad Gateway')
        return _resp(sr, '201 Created', [('Location', _href(dest))])

    def copy(env, sr, path):
        dest = _dest_path(env)
        if not dest: return _resp(sr, '400 Bad Request')
        try:
            data = rootstore.read(path)
            rootstore.write(dest, data)
        except Exception as e:
            logging.warning('copy %s -> %s: %s', path, dest, e)
            return _resp(sr, '502 Bad Gateway')
        return _resp(sr, '201 Created', [('Location', _href(dest))])

    def lock(env, sr, path):
        token = 'opaquelocktoken:%s' % os.urandom(8).hex()
        body = ('<?xml version="1.0" encoding="utf-8"?>\n'
                '<D:prop xmlns:D="DAV:"><D:lockdiscovery><D:activelock>'
                '<D:locktype><D:write/></D:locktype>'
                '<D:lockscope><D:exclusive/></D:lockscope>'
                '<D:depth>infinity</D:depth>'
                '<D:timeout>Second-3600</D:timeout>'
                '<D:locktoken><D:href>%s</D:href></D:locktoken>'
                '</D:activelock></D:lockdiscovery></D:prop>') % token
        return _resp(sr, '200 OK',
                     [('Lock-Token', '<%s>' % token),
                      ('Content-Type', 'application/xml; charset=utf-8')],
                     body)

    def unlock(env, sr, path):
        return _resp(sr, '204 No Content')

    handlers = {
        'OPTIONS': options, 'PROPFIND': propfind, 'PROPPATCH': proppatch,
        'GET': get, 'HEAD': get,
        'PUT': put, 'DELETE': delete, 'MKCOL': mkcol,
        'MOVE': move, 'COPY': copy, 'LOCK': lock, 'UNLOCK': unlock,
    }

    def app(env, sr):
        method = env.get('REQUEST_METHOD', '').upper()
        path = _decode_path(env)
        logging.debug('DAV %s %s', method, path)
        h = handlers.get(method)
        if not h:
            return _resp(sr, '405 Method Not Allowed', [('Allow', ALLOWED)])
        try:
            return h(env, sr, path)
        except StoreException as e:
            logging.warning('dav %s %s: %s', method, path, e)
            return _resp(sr, '404 Not Found')
        except Exception as e:
            logging.error('dav %s %s: %s', method, path, e, exc_info=True)
            return _resp(sr, '500 Internal Server Error')
    return app

def _locate_web_path(path):
    p = os.path.realpath(path)
    while p != '/' and not os.path.exists(os.path.join(p, 'index.org')):
        p = os.path.dirname(p)
    return p

def main():
    if len(sys.argv) <= 1:
        print(__doc__)
        sys.exit(1)
    log_level = os.getenv('log') or 'info'
    logging.basicConfig(level=getattr(logging, log_level.upper(), None),
                        format='%(asctime)s %(levelname)s %(message)s')
    listen_addr = sys.argv[1]
    log_file = sys.argv[2] if len(sys.argv) > 2 else ''
    web_path = _locate_web_path(_repo_root)
    os.chdir(web_path)
    if log_file:
        f = open(log_file, 'wb+')
        os.dup2(f.fileno(), 1); os.dup2(f.fileno(), 2)
    from core.store import build_root_store
    from core.wsgi import run_wsgi
    from core.extloader import load_extensions
    # only the webdav ext is needed to register the WebDav store + /dav mount.
    load_extensions(os.environ.get('ext', 'webdav').split(','))
    root = build_root_store('w/fstab')
    app = make_dav_app(root)
    host_port = listen_addr.split(':')
    host, port = (host_port[0], host_port[1]) if len(host_port) == 2 else ('', listen_addr)
    logging.info('dav listen at %s dir=%s', listen_addr, web_path)
    run_wsgi(app, host, int(port), daemon=bool(log_file))

if __name__ == '__main__':
    main()
