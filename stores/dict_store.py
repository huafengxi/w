from stores.store import StoreException, file_find_all

_VMAP_RE = r'(?m)^([^# ]+):\s+(\S*)\n'

class DictStore(dict):
    def __init__(self, path):
        import core.registry as registry  # core's vmap composer: base + ext frags
        pairs = file_find_all(_VMAP_RE, path)
        for frag in registry.REGISTRY.vmap_fragments:
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
