import re
import io
import tarfile
import time
import logging

def need_ignore(path):
    return re.match('/.git|/nohup.out|/myapp.log|/emacs.d/local|/emacs.d/server|.+[.]elc', path)

def join_path(p1, p2):
    return re.sub('[/]+', '/', '%s/%s'%(p1, p2))

class TarHandler:
    def __init__(self):
        self.fileobj = io.StringIO()
        self.fileobj.name = 'm'
        self.tar = tarfile.TarFile(mode='w', fileobj=self.fileobj)
        self.time = time.time()
    def get_content(self):
        self.fileobj.seek(0)
        return self.fileobj.read()
    def add(self, path, content):
        path = join_path('m', path)
        if path.endswith('/'):
            self.add_dir(path)
        else:
            self.add_file(path, content)
    def add_dir(self, path):
        pass
    def add_file(self, path, content):
        fileobj = io.StringIO(content)
        tarinfo = tarfile.TarInfo(name=path)
        tarinfo.size = len(content)
        tarinfo.mtime = self.time
        self.tar.addfile(tarinfo, fileobj)

def gen_sitemap(store, base='/'):
    unfetched, fetched = [base], {}
    while unfetched:
        path = unfetched.pop()
        if fetched.get(path):
            continue
        if need_ignore(path):
            continue
        if path.endswith('/'):
            content = store.read(path)
            if isinstance(content, bytes):
                content = content.decode()
            unfetched.extend(join_path(path, line) for line in content.split('\n') if not line.startswith('..') and not line.startswith('.git'))
        fetched[path] = True
        yield path

def archive(store, base='/'):
    for path in gen_sitemap(store, base):
        logging.info('archive: %s', path)
        if path.endswith('/'):
            yield path, None
        else:
            yield path, store.read(path)

def archive_to_tar(store, base='/'):
    handler = TarHandler()
    for path, content in archive(store, base):
        handler.add(path, content)
    return handler.get_content()
