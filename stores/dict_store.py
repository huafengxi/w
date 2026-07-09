import importlib

from stores.store import StoreException

class DictStore(dict):
    def __init__(self, data):
        dict.__init__(self, data)

    @classmethod
    def from_fstab(cls, module='vmap'):
        """fstab factory: `/vmap Dict vmap` -> DictStore(vmap.build())."""
        return cls(importlib.import_module(module).build())

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
