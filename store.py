import traceback
import logging
import re
import os
class StoreException(Exception):
    pass

class RootStore:
    def __init__(self, fstab):
        self.fstab = fstab
    def get_store(self, path):
        path = os.path.join('/', path)
        for prefix, store in self.fstab:
            if prefix != '/':
                prefix = prefix + '/'
            if path.startswith(prefix): return store, path[len(prefix):]
        raise StoreException("no store for %s"%(path))
    def get_rpath(self, path):
        return self.head(path).get('rpath')

    def find(self, path, limit=100):
        flist = [path]
        for i in range(limit):
            if i >= len(flist):
                flist.sort()
                return flist
            path = flist[i]
            if path.endswith('/'):
                flist.extend([path + p for p in self.read(path).split('\n') if p != '../' and p != './'])
        return flist
    def head(self, path):
        try:
            store, new_path = self.get_store(path)
            meta = store.head(new_path)
            logging.debug('root.head: %s -> %s.%s meta=%s', path, store, new_path, meta)
        except StoreException as e:
            logging.debug('root.head: %s %s', e, path)
            meta = None
        return meta
    def delete(self, path):
        try:
            store, new_path = self.get_store(path)
            return store.delete(new_path)
        except StoreException as e:
            return None
    def lazy_read(self, path, range_req=''):
        store, new_path = self.get_store(path)
        if callable(getattr(store, 'lazy_read', None)):
            x = store.lazy_read(new_path, range_req)
        else:
            d = store.read(new_path)
            x = len(d), 0, len(d), d
        logging.debug("root.lazy_read: %s -> %s %.100s", path, store, new_path)
        return x

    def read(self, path):
        store, new_path = self.get_store(path)
        x = store.read(new_path)
        logging.debug("root.read: %s -> %s %.100s", path, store, new_path)
        return x

    def write(self, path, content):
        store, new_path = self.get_store(path)
        return store.write(new_path, content)

read_chunk_sz = 1<<21
def lazy_read_part_file(path, fsize, start, end):
    with open(path, 'rb') as f:
        f.seek(start)
        for i in range(start, end, read_chunk_sz):
            buf = f.read(min(end - i, read_chunk_sz))
            if buf:
                yield buf

class Pack:
    def __init__(self, pack=None):
        self.pack = pack
    def set_pack(self, pack):
        self.pack = pack
    def list(self, path):
        items = []
        if os.path.isdir(path):
            items.extend(os.listdir(path))
        if self.pack:
            items.extend([p[len(path):] for p in self.pack.list(path)])
        return items
    def lazy_read(self, path, range_req=''):
        def limit_read_size(start, end):
            return start, min(start + read_chunk_sz, end)
        def parse_range(range_seq, fsize):
            _ = re.findall('\d+', range_req)
            if len(_) == 2:
                return int(_[0]), int(_[1]) + 1
            elif len(_) == 1:
                return limit_read_size(int(_[0]), fsize)
            else:
                return limit_read_size(0, fsize)
        if os.path.isfile(path):
            fsize = os.stat(path).st_size
            start, end = parse_range(range_req, fsize)
            logging.info("RangeRead: '%s' start=%d end=%d path=%s", range_req, start, end, path)
            return fsize, start, end, lazy_read_part_file(path, fsize, start, end)
        elif range_req:
            raise Exception('not support range request: %s'%(path))
        else:
            data = self.read(path)
            if data == None:
                return 0, 0, 0, ''
            else:
                return len(data), 0, len(data), data
    def read(self, path, limit=-1):
        if os.path.isfile(path):
            with open(path, 'r') as f:
                return f.read(limit)
        if self.pack:
            return self.pack.read(path)

pack = Pack()
def file_find_all(pat, path):
    return re.findall(pat, pack.read(path))

def build_root_store(fstab, pack=None):
    logging.info('build_root_store %s', fstab)
    def parse_cmd_args(args):
        return [i for i in args if not re.match('^\w+=', i)], dict(i.split('=', 1) for i in args if re.match('^\w+=', i))
    def do_mount(mpoint, type, arg):
        logging.info('mount %s %s %s', mpoint, type, arg)
        args, kw = parse_cmd_args(arg.split())
        return mount(type, *args, **kw)
    mstore = [(mpoint, do_mount(mpoint, type, arg)) for mpoint, type, arg in file_find_all('(?m)^([^# \t]+)\s+(\w+)(.*)\n', fstab)]
    logging.info(mstore)
    return RootStore(mstore)

def get_store_cls(type):
    def load(x):
        logging.info('load: %s', x)
        exec compile(pack.read(x), filename='<pack>/%s'%(x,), mode='exec') in globals()
    cls_name = '%sStore'%(type)
    if not globals().get(cls_name, None):
        load('w/%s_store.py'%(type.lower()))
    return globals().get(cls_name)

def mount(type, *args, **kw):
    return get_store_cls(type)(*args, **kw)
