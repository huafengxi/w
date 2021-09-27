class PyEnvStore(dict):
    def __init__(self):
        self.env = globals()
    def head(self, path):
        if path == '/' or path == '' or self.env.has_key(path):
            return dict(type='text/plain')
        else:
            return None
    def delete(self, path):
        raise StoreException('Delete NotSupport path=%s'%(path))
    def read(self, path):
        if path == '/' or path == '':
            return repr(self.env)
        else:
            return self.env.get(path, None)
    def write(self, path, content):
        self.env[path] = content
    def __repr__(self):
        return 'PyEnvStore()'
