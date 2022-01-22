import os
import mimetypes

def get_mime_type(path):
    text_mime_type = 'text/plain'
    if path.endswith('.org'): type = 'text/org'
    elif path.endswith('.md'): type = 'text/md'
    elif path.endswith('.svg'): type = 'image/svg+xml'
    elif path.endswith('.enc'): type = 'enc'
    elif path.endswith('.tab'): type = 'tab'
    elif path.endswith('.wsh'): type = 'wsh'
    elif path.endswith('.db'): type = 'db'
    elif path.endswith('.ish'): type = 'ish'
    elif path: type = mimetypes.guess_type(path)[0] or text_mime_type
    else: type = text_mime_type
    return type

import chardet
def get_encoding(content):
    return chardet.detect(content)['encoding']

def safe_read(path, n=-1):
    try:
        pack.read(path, n)
    except Exception as e:
        #print e
        return ''

def is_path_dir(path): return os.path.isdir(path) or path.endswith('/')
class DirStore:
    def __init__(self, base_dir):
        self.base_dir = os.path.realpath(os.path.expanduser(base_dir))

    def get_real_path(self, path):
        return os.path.join(self.base_dir, path) or '/'

    def delete(self, path):
        real_path = self.get_real_path(path)
        os.unlink(real_path)
    def head(self, path):
        header_vars = dict()
        real_path = self.get_real_path(path)
        if is_path_dir(real_path):
            mime_type = 'dir'
            header_vars = dict(rpath=real_path)
        else:
            content = pack.read(real_path, 1024)
            # if content == None: raise StoreException("'%s' not found, real_path=%s!"%(path, real_path))
            if content != None:
                mime_type = get_mime_type(real_path)
                first_line = content.split('\n'.encode(), 1)[0]
                header_vars = dict([(k.decode(), v.decode()) for k,v in re.findall(rb'-\*-\s*(\w+)\s*=\s*(.*?)\s*-\*-', first_line)])
                header_vars.update(rpath=real_path)
                if mime_type.startswith('text'):
                    sample = pack.read(real_path, 1<<14)
                    header_vars.update(encoding=get_encoding(sample))
            else:
                mime_type = None
                header_vars = dict(rpath=real_path)
        meta = dict(type=mime_type)
        meta.update(header_vars)
        return meta

    def read_dir(self, path):
        items = pack.list(path)
        return '\n'.join(['../'] + [name + ['', '/'][is_path_dir('%s/%s'%(path, name))] for name in items])

    def lazy_read(self, path, range_req=''):
        real_path = self.get_real_path(path)
        if is_path_dir(real_path):
            d = self.read_dir(real_path)
            return len(d), 0, len(d), d
        else:
            return pack.lazy_read(real_path, range_req)

    def read(self, path):
        real_path = self.get_real_path(path)
        if is_path_dir(real_path):
            return self.read_dir(real_path)
        else:
            return pack.read(real_path)

    def write(self, path, content):
        real_path = self.get_real_path(path)
        try:
            with open(real_path, 'w') as f:
                f.write(content)
        except IOError as e:
            logging.error(e)
            raise e

    def __repr__(self):
        return 'DirStore(%s)'%(repr(self.base_dir))
