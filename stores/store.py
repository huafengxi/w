import logging
import re
import os

class StoreException(Exception):
    pass

def _path_is_dir(x): return (x in ['', '.', '..']) or x.endswith('/')
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
    def mv(self, path, new_path):
        try:
            store, src = self.get_store(path)
            store2, dest = self.get_store(new_path)
            if store != store2:
                raise StoreException("mv src and dest not on same store: %s %s" % (path, new_path))
            return store.mv(src, dest)
        except StoreException as e:
            logging.debug('root.mv: %s', e)
            return None
    def delete(self, path):
        try:
            store, new_path = self.get_store(path)
            return store.delete(new_path)
        except StoreException as e:
            logging.debug('root.delete: %s %s', e, path)
            return None
    def mkdir(self, path):
        store, new_path = self.get_store(path)
        if not callable(getattr(store, 'mkdir', None)):
            raise StoreException('mkdir not supported on %s' % store)
        return store.mkdir(new_path)
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
        if _path_is_dir(path):
            if callable(getattr(store, 'read_dir', None)):
                x = store.read_dir(new_path)
            else:
                x = store.read(new_path)
        else:
            x = store.read(new_path)
        logging.debug("root.read: %s -> %s %.100s", path, store, new_path)
        return x

    def write(self, path, content):
        store, new_path = self.get_store(path)
        return store.write(new_path, content)

def file_find_all(pat, path):
    with open(path) as f:
        return re.findall(pat, f.read())

def build_root_store(fstab):
    logging.info('build_root_store %s', fstab)
    def parse_cmd_args(args):
        return [i for i in args if not re.match(r'^\w+=', i)], dict(i.split('=', 1) for i in args if re.match(r'^\w+=', i))
    def do_mount(mpoint, type, arg):
        logging.info('mount %s %s %s', mpoint, type, arg)
        args, kw = parse_cmd_args(arg.split())
        return mount(type, *args, **kw)
    mstore = []
    for mpoint, type, arg in file_find_all(r'(?m)^([^# \t]+)\s+(\w+)(.*)\n', fstab):
        try:
            mstore.append((mpoint, do_mount(mpoint, type, arg)))
        except Exception as e:
            logging.warning('mount skipped: %s %s %s: %r', mpoint, type, arg, e)
    logging.info(mstore)
    return RootStore(mstore)

def get_store_cls(type):
    import core.registry as registry
    return registry.REGISTRY.get_store_cls(type)

def mount(type, *args, **kw):
    return get_store_cls(type)(*args, **kw)
