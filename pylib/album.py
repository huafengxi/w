#!/usr/bin/env python2
'''
./album.py find_local k v attr1 attr2
./album.py distinct_local k
'''
import os
class LocalStore:
    def __init__(self):
        pass
    def find(self, path):
        for root, dirs, files in os.walk(path):
            for f in files:
                yield '%s/%s'%(root, f)
            for d in dirs:
                yield '%s/%s/'%(root, d)
    def read(self, path):
        try:
            return open(path).read()
        except IOError:
            return ''

class AlbumDB:
    def __init__(self, path, store=None):
        if store == None:
            store = LocalStore()
        self.store, self.path = store, path
        self.dirs = [i for i in store.find(path, 10000) if i.endswith('/.meta/')]
    def read(self, path): return self.store.read(path)
    def build(self, d, attrs):
        def read_meta(d, m): return self.read(os.path.join(d,m))
        one = dict([(a, read_meta(d, a).decode()) for a in attrs])
        one.update(base_dir=os.path.split(d[:-1])[0])
        return one
    def collect(self, li, *attrs):
        if li == None: li = self.dirs
        return [self.build(d, attrs) for d in li]
    def distinct(self, key):
        s = set()
        for i in self.collect(None, key):
            if i.get(key, '') != None:
                s.update(i.get(key, '').split('\n'))
        return s
    def filt(self, k, v):
        def is_match(d):
            c = self.read(os.path.join(d, k))
            return c and (v in c)
        return [d for d in self.dirs if is_match(d)]

def find_local(k, v, *attrs):
    a = AlbumDB('.')
    li = a.filt(k, v)
    for i in a.collect(li, *attrs):
        print(i)

def distinct_local(k):
    return AlbumDB('.').distinct(k)
import sys
if __name__ == '__main__':
    def help(): print(__doc__)
    len(sys.argv) >= 2  or help() or sys.exit(1)
    func = globals().get(sys.argv[1])
    callable(func) or help() or sys.exit(2)
    ret = func(*sys.argv[2:])
    if ret != None:
        print(ret)


