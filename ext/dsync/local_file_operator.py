import os
def get_mtime(p):
    try:
        return int(os.stat(p).st_mtime * 1000000000)
    except OSError:
        return -1
def update_with_mtime(rp, v, ts):
    try:
        os.makedirs(os.path.dirname(rp))
    except OSError as e:
        pass
    np = rp + '.dsync.tmp'
    with open(np, 'wb') as f:
        f.write(v)
    os.utime(np, (ts/1000000000.0, ts/1000000000.0))
    os.rename(np, rp)
def list_mtime(path, ts):
    if path: path = os.path.normpath(path) + '/'
    for root, subdirs, files in os.walk(path):
        for p in files:
            rp = os.path.join(root, p)
            mtime = get_mtime(rp)
            if mtime > ts:
                yield rp[len(path):], mtime
