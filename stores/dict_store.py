import os

from stores.store import StoreException, file_find_all

_VMAP_RE = r'(?m)^([^# ]+):\s+(\S*)\n'

class DictStore(dict):
    def __init__(self, path):
        # Base vmap + any sibling ext/<name>/vmap.frag by convention.
        pairs = file_find_all(_VMAP_RE, path)
        ext_dir = os.path.join(os.path.dirname(path), 'ext')
        if os.path.isdir(ext_dir):
            for name in sorted(os.listdir(ext_dir)):
                frag = os.path.join(ext_dir, name, 'vmap.frag')
                if os.path.isfile(frag):
                    pairs.extend(file_find_all(_VMAP_RE, frag))
        dict.__init__(self, pairs)
    def head(self, path):
        if path == '/' or path == '' or path in self:
            return dict(type='text/plain')
        else:
            return None
    def delete(self, path):
        raise StoreException('Delete NotSupport path=%s'%(path))
    def read(self, path):
        if path == '/' or path == '':
            return repr(self)
        else:
            return self.get(path, None)
    def write(self, path, content):
        self[path] = content
    def __repr__(self):
        return dict.__repr__(self)
