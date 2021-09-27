class DictStore(dict):
    def __init__(self, path):
        dict.__init__(self, file_find_all('(?m)^([^# ]+):\s+(\S*)\n', path))
    def head(self, path):
        if path == '/' or path == '' or self.has_key(path):
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
