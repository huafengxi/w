import os
import re
import logging
import mimetypes
import shutil
from store import _path_is_dir

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
    elif path.endswith('.plist'): type = 'plist'
    elif path.endswith('.flac'): type = 'audio/flac'
    elif path: type = mimetypes.guess_type(path)[0] or text_mime_type
    else: type = text_mime_type
    return type

import chardet
def get_encoding(content):
    return chardet.detect(content)['encoding']

def safe_read(p, limit=-1):
    try:
        with open(p, 'rb') as f:
            return f.read(limit)
    except IOError as e:
        return None

read_chunk_sz = 1<<19
def lazy_read_part_file(path, fsize, start, end):
    with open(path, 'rb') as f:
        f.seek(start)
        for i in range(start, end, read_chunk_sz):
            buf = f.read(min(end - i, read_chunk_sz))
            if buf:
                yield buf

class DirStore:
    def __init__(self, base_dir):
        self.base_dir = os.path.realpath(os.path.expanduser(base_dir))

    def get_real_path(self, path):
        return os.path.join(self.base_dir, path) or '/'

    def mv(self, src, dest):
        src, dest = self.get_real_path(src), self.get_real_path(dest)
        shutil.move(src, dest)
        return 'mv %s %s'%(src, dest)
    def delete(self, path):
        real_path = self.get_real_path(path)
        os.unlink(real_path)
    def mkdir(self, path):
        os.makedirs(self.get_real_path(path), exist_ok=True)
    def head(self, path):
        header_vars = dict()
        real_path = self.get_real_path(path)
        if _path_is_dir(path):
            mime_type = 'dir'
            header_vars = dict(rpath=real_path)
        else:
            content = safe_read(real_path, 1024)
            if content != None:
                mime_type = get_mime_type(real_path)
                first_line = content.split(b'\n', 1)[0]
                header_vars = dict([(k.decode(), v.decode()) for k,v in re.findall(rb'-\*-\s*(\w+)\s*=\s*(.*?)\s*-\*-', first_line)])
                header_vars.update(rpath=real_path)
                if mime_type.startswith('text'):
                    sample = safe_read(real_path, 1<<14)
                    header_vars.update(encoding=get_encoding(sample))
            else:
                mime_type = None
                header_vars = dict(rpath=real_path)
        meta = dict(type=mime_type)
        meta.update(header_vars)
        return meta

    def read_dir(self, path):
        path = self.get_real_path(path)
        items = sorted(os.listdir(path))
        return '\n'.join(['../'] + [name + ['', '/'][os.path.isdir(self.get_real_path(f'{path}/{name}'))] for name in items])

    def lazy_read_file(self, path, range_req):
        def limit_read_size(start, end):
            return start, min(start + read_chunk_sz, end)
        def parse_range(range_seq, fsize):
            _ = re.findall(r'\d+', range_req)
            if len(_) == 2:
                return int(_[0]), int(_[1]) + 1
            elif len(_) == 1:
                return limit_read_size(int(_[0]), fsize)
            else:
                return 0, fsize
        if os.path.isfile(path):
            fsize = os.stat(path).st_size
            start, end = parse_range(range_req, fsize)
            logging.debug("RangeRead: '%s' start=%d end=%d path=%s", range_req, start, end, path)
            return fsize, start, end, lazy_read_part_file(path, fsize, start, end)
        elif range_req:
            raise Exception('not support range request: %s'%(path))
        else:
            data = self.read(path)
            if data == None:
                return 0, 0, 0, ''
            else:
                return len(data), 0, len(data), data

    def lazy_read(self, path, range_req=''):
        real_path = self.get_real_path(path)
        if _path_is_dir(real_path):
            d = self.read_dir(real_path)
            return len(d), 0, len(d), d
        else:
            return self.lazy_read_file(real_path, range_req)

    def read(self, path):
        real_path = self.get_real_path(path)
        return safe_read(real_path)

    def write(self, path, content):
        real_path = self.get_real_path(path)
        try:
            with open(real_path, 'wb') as f:
                if isinstance(content, str):
                    content = content.encode('utf-8')
                f.write(content)
        except IOError as e:
            logging.error(e)
            raise e

    def __repr__(self):
        return 'DirStore(%s)'%(repr(self.base_dir))
