#!/usr/bin/env python3
"""w/test/test_regress_multipart_dir_read.py -- regression cases for two defects.

A) multipart POST parsing (core/handler.py parse_post -> cgi.parse_multipart):
   cgi.parse_multipart does `pdict['boundary'].decode('ascii')`, i.e. it wants
   bytes, while cgi.parse_header (stdlib on <3.13, legacy-cgi on >=3.13) yields
   str. Without normalization every multipart/form-data POST died with
   AttributeError inside prepare_args -> HTTP 500 with an empty body, before any
   handler ran.

B) directory read through the Cmd store (stores/cmd_store.py):
   stores/dir_store.safe_read swallows open()'s OSError (== IOError) into None --
   IsADirectoryError for a directory, PermissionError for an unreadable file --
   and CmdStore used to be the only mountable store without read_dir, so
   RootStore.read routed directory paths into CmdStore.read. That None reached
   consumers doing `store.read(src).split('\\n')` (core/rpc/core_read.py, v=dir)
   -> AttributeError -> HTTP 500. The fix stays on the store boundary:
   read_dir() answers directory paths with a str listing (same shape as
   DirStore.read_dir) and read() collapses a None into an empty value of the
   same type as its other branches (bytes).

Self-contained: every path comes from __file__ / tempfile, the HTTP leg runs on
an ephemeral loopback port via wsgiref (no auth, no ssl, no fixed port), and the
only subprocess spawned is the shell builtin `true` used as a Cmd store command.

Run:  python3 w/test/test_regress_multipart_dir_read.py
Exits 0 on success, 1 on failure.
"""
import io
import logging
import os
import shutil
import stat
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from wsgiref.simple_server import make_server

HERE = os.path.dirname(os.path.realpath(__file__))
W = os.path.dirname(HERE)
WEBROOT = os.path.dirname(W)
os.chdir(WEBROOT)
if W not in sys.path:
    sys.path.insert(0, W)

logging.basicConfig(level=getattr(logging, os.getenv('log', 'WARNING').upper(), logging.WARNING),
                    format='%(levelname)s %(message)s')

import cgi
import core.handler as handler
from core.wsgi import make_wsgi_app
from stores.cmd_store import CmdStore
from stores.store import build_root_store

RESULTS = []


def check(label, cond, *extra):
    print(('OK   ' if cond else 'FAIL ') + label, *extra)
    RESULTS.append(bool(cond))


def run_case(fn, *args):
    """Run one case; an unexpected exception is recorded as a FAIL instead of
    aborting the whole run (so a broken build reports every broken leg)."""
    try:
        fn(*args)
    except Exception as e:
        check('%s raised' % fn.__name__, False, '%s: %s' % (type(e).__name__, e))


# ---------------------------------------------------------------- case A

BOUNDARY = '----RegressBoundary0123456789'


def multipart_body(fields, files=()):
    out = io.BytesIO()
    dash = b'--' + BOUNDARY.encode('ascii')
    for name, value in fields:
        out.write(dash + b'\r\n')
        out.write(('Content-Disposition: form-data; name="%s"\r\n\r\n' % name).encode('utf-8'))
        out.write(value.encode('utf-8') + b'\r\n')
    for name, filename, value in files:
        out.write(dash + b'\r\n')
        out.write(('Content-Disposition: form-data; name="%s"; filename="%s"\r\n'
                   % (name, filename)).encode('utf-8'))
        out.write(b'Content-Type: application/octet-stream\r\n\r\n')
        out.write(value + b'\r\n')
    out.write(dash + b'--\r\n')
    return out.getvalue()


def case_a_unit():
    ctype = 'multipart/form-data; boundary=' + BOUNDARY
    body = multipart_body([('text', 'hello world'), ('n', '42')],
                          [('upload', 'a.bin', b'\x00\x01raw')])
    # Precondition of the defect: parse_header hands back a str boundary.
    _, pdict = cgi.parse_header(ctype)
    check('A0 parse_header yields a str boundary (defect precondition)',
          isinstance(pdict.get('boundary'), str), pdict)

    parsed = handler.parse_post(ctype, io.BytesIO(body), len(body))
    check('A1 parse_post returns the plain fields',
          parsed.get('text') == ['hello world'] and parsed.get('n') == ['42'], parsed)
    check('A2 parse_post keeps the file part as bytes',
          parsed.get('upload') == [b'\x00\x01raw'], parsed.get('upload'))

    flat = handler.parse_post_to_dict(ctype, io.BytesIO(body), len(body))
    check('A3 parse_post_to_dict flattens to last-value-per-key',
          flat.get('text') == 'hello world' and flat.get('n') == '42', flat)

    # prepare_args is the real consumer: a multipart POST must reach args intact.
    env = {'CONTENT_TYPE': ctype, 'CONTENT_LENGTH': str(len(body)),
           'HTTP_USER_AGENT': 'regress-test'}
    args = handler.prepare_args(env, {'src': '/x'}, io.BytesIO(body))
    check('A4 prepare_args carries multipart fields into args',
          args.get('text') == 'hello world' and args.get('src') == '/x', sorted(args))

    # The other branch must stay untouched.
    qs = b'a=1&b=2'
    check('A5 urlencoded branch unchanged',
          handler.parse_post('application/x-www-form-urlencoded', io.BytesIO(qs), len(qs))
          == {'a': ['1'], 'b': ['2']})


def case_a_http(base, ctype, body):
    """A multipart POST over a real socket, through the production pipeline
    (core.wsgi.make_wsgi_app + core.handler), answered by the read-only
    `v=echo` branch of core/rpc/core_read.py."""
    req = urllib.request.Request(base + '/w/core/rpc/core_read.py?src=/w/README.md',
                                 data=body, method='POST',
                                 headers={'Content-Type': ctype,
                                          'Content-Length': str(len(body))})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            status, text = r.status, r.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        status, text = e.code, e.read().decode('utf-8', 'replace')
    check('A6 multipart POST over HTTP is 200', status == 200, 'status=%s' % status)
    check("A7 the echoed args carry both multipart fields",
          "'text': 'hello world'" in text and "'n': '42'" in text, text[:200])


# ---------------------------------------------------------------- case B

def case_b_unit(tmp):
    sub = os.path.join(tmp, 'adir')
    os.makedirs(os.path.join(sub, 'nested'))
    with open(os.path.join(sub, 'alpha.txt'), 'w') as f:
        f.write('a')

    store = CmdStore('true')
    got = store.read(sub)
    check('B1 CmdStore.read on a directory returns bytes, not None',
          got is not None and isinstance(got, bytes), repr(got))

    listing = store.read_dir(sub + '/')
    check('B2 read_dir lists the directory as str (DirStore shape)',
          isinstance(listing, str) and 'alpha.txt' in listing and 'nested/' in listing
          and listing.split('\n')[0] == '../', repr(listing))
    check('B3 the consumer shape `.split("\\n")` works on read_dir output',
          set(listing.split('\n')) >= {'../', 'alpha.txt', 'nested/'}, listing.split('\n'))

    missing = os.path.join(tmp, 'nope')
    other = store.read(missing)
    check('B4 the Popen branch is bytes too (type parity)', isinstance(other, bytes), repr(other))

    # read() None-collapse for an existing but unreadable file (safe_read -> None).
    unreadable = os.path.join(tmp, 'locked.txt')
    with open(unreadable, 'w') as f:
        f.write('secret')
    os.chmod(unreadable, 0)
    if os.geteuid() == 0:
        print('SKIP B5 unreadable-file fallback: running as root, mode 000 is still readable')
    else:
        got2 = store.read(unreadable)
        check('B5 read() on an unreadable file returns b"" instead of None',
              got2 == b'' and isinstance(got2, bytes), repr(got2))
    os.chmod(unreadable, stat.S_IRUSR | stat.S_IWUSR)

    # RootStore routes a dir path on a Cmd mount to read_dir (the missing method
    # was the root cause), and the real consumer renders it.
    fstab = os.path.join(tmp, 'fstab')
    with open(fstab, 'w') as f:
        f.write('/w Dir %s\n/c Cmd true\n' % W)
    root = build_root_store(fstab)
    # '/c' + <abs path> resolves to <abs path> in the rootless Cmd path model.
    route = '/c' + sub + '/'
    via_root = root.read(route)
    check('B6 RootStore.read on a Cmd-mounted dir path is a str listing',
          isinstance(via_root, str) and 'alpha.txt' in via_root, repr(via_root)[:120])
    meta, body = handler.run_script(root, '/w/core/rpc/core_read.py', dict(v='dir', src=route))
    text = body if isinstance(body, str) else b''.join(
        x if isinstance(x, bytes) else str(x).encode() for x in body).decode('utf-8', 'replace')
    check('B7 core_read.py v=dir renders the entries',
          meta.get('type') == 'text/html' and 'alpha.txt' in text and 'nested/' in text,
          meta, text[-200:])
    return root, route


def case_b(tmp, out):
    out['root'], out['route'] = case_b_unit(tmp)


def case_b_http(base, route):
    """The dir view over a real socket: must be 200 and must carry entries."""
    req = urllib.request.Request(base + route + '?v=dir', method='GET')
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            status, text = r.status, r.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        status, text = e.code, e.read().decode('utf-8', 'replace')
    check('B8 GET <cmd mount>?v=dir over HTTP is 200', status == 200, 'status=%s' % status)
    check('B9 the dir view body carries the directory entries',
          'alpha.txt' in text and 'nested/' in text and '<li>' in text, text[-200:])


def main():
    tmp = tempfile.mkdtemp(prefix='w_regress_')
    httpd = None
    try:
        run_case(case_a_unit)
        out = {}
        run_case(case_b, tmp, out)
        root, route = out.get('root'), out.get('route')

        if root is not None:
            # Ephemeral loopback port; production pipeline, no auth / no ssl.
            httpd = make_server('127.0.0.1', 0,
                                make_wsgi_app([handler.Handler(root).handle_req]))
            base = 'http://127.0.0.1:%d' % httpd.server_port
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            ctype = 'multipart/form-data; boundary=' + BOUNDARY
            body = multipart_body([('text', 'hello world'), ('n', '42')])
            run_case(case_a_http, base, ctype, body)
            run_case(case_b_http, base, route)
        else:
            check('HTTP legs (root store built)', False, 'skipped: no root store')
    finally:
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        # Only ever delete the mkdtemp dir this process created: require it to sit
        # strictly inside the temp root (never equal to it) and to be a directory.
        real = os.path.realpath(tmp)
        temp_root = os.path.realpath(tempfile.gettempdir())
        if real.startswith(temp_root + os.sep) and real != temp_root and os.path.isdir(real):
            shutil.rmtree(real)
        else:
            print('SKIP cleanup: %r is not a self-created temp dir' % real)
    failed = RESULTS.count(False)
    print('--- %d checks, %d failed ---' % (len(RESULTS), failed))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
