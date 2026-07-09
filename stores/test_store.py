#!/usr/bin/env python3
"""stores/test_store.py -- self-contained smoke test for the stores package.

Builds a temp fstab mounting /enc (Enc store) and /dir (Dir store) on temp
dirs, starts stores/server.py in-process on an ephemeral port, then exercises
write / full-read / range-read / head / mime / dir on both mounts. The Enc
mount is the interesting one: it tests name+data encryption with on-the-fly
range decryption.

Run:  python3 w/stores/test_store.py
Exits 0 on success, 1 on failure.
"""
import os
import sys
import json
import random
import shutil
import tempfile
import threading
import urllib.request
from urllib.parse import quote
from wsgiref.simple_server import make_server

HERE = os.path.dirname(os.path.realpath(__file__))
W = os.path.dirname(HERE)
WEBROOT = os.path.dirname(W)
os.chdir(WEBROOT)
if W not in sys.path:
    sys.path.insert(0, W)

from stores.store import build_root_store
from stores.server import build_app

TMP = tempfile.mkdtemp(prefix='stores_test_')
ENC_DIR = os.path.join(TMP, 'enc')
DIR_DIR = os.path.join(TMP, 'dir')
os.makedirs(ENC_DIR)
os.makedirs(DIR_DIR)
fstab_path = os.path.join(TMP, 'fstab')
with open(fstab_path, 'w') as f:
    f.write('/enc Enc %s\n/dir Dir %s\n' % (ENC_DIR, DIR_DIR))

root = build_root_store(fstab_path)
httpd = make_server('127.0.0.1', 0, build_app(root))
BASE = 'http://127.0.0.1:%d' % httpd.server_port
threading.Thread(target=httpd.serve_forever, daemon=True).start()


def check(label, cond, *extra):
    print(('OK   ' if cond else 'FAIL ') + label, *extra)
    if not cond:
        sys.exit(1)


def req(path, query='', data=None, headers=None):
    url = BASE + quote(path) + (('?' + query) if query else '')
    return urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers or {}))


def run_mount(prefix, label):
    blob = os.urandom(2 * 1024 * 1024)
    p = prefix + '/test_store_blob.bin'
    txt = prefix + '/テスト.txt'
    try:
        req(p, 'v=write', data=blob).read()
        check('[%s] write 2MB' % label, True)

        got = req(p).read()
        check('[%s] full read' % label, got == blob)

        off = random.randint(0, len(blob) - 1)
        ln = random.randint(1, len(blob) - off)
        r = req(p, headers={'Range': 'bytes=%d-%d' % (off, off + ln - 1)})
        got = r.read()
        check('[%s] range %d+%d (status %s)' % (label, off, ln, r.status), got == blob[off:off + ln])

        meta = json.loads(req(p, 'v=head').read())
        check('[%s] head type' % label, meta.get('type') in ('application/octet-stream', 'text/plain'), meta)

        req(txt, 'v=write', data=b'hello').read()
        r = req(txt)
        ct = r.headers.get('Content-Type', '').split(';')[0].strip()
        r.read()
        check('[%s] mime .txt -> text/plain' % label, ct == 'text/plain', ct)

        listing = req(prefix + '/', 'v=dir').read().decode()
        check('[%s] dir listing has blob' % label, 'test_store_blob.bin' in listing, listing)
    finally:
        for f in (p, txt):
            try:
                root.delete(f)
            except Exception:
                pass


try:
    run_mount('/enc', 'Enc')
    run_mount('/dir', 'Dir')
finally:
    httpd.shutdown()
    shutil.rmtree(TMP, ignore_errors=True)

print('--- stores/test_store.py done ---')
