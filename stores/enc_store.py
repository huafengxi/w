import os
from stores.store import _path_is_dir
from stores.dir_store import DirStore, get_mime_type

char_map = 'iFXhbcNYDuUgsjrIMJwTpPAqnyvOfSxeEzWBkdtQmlZCoRVKLGHa'
char_map_len = len(char_map)
def fuzz_str(f):
    def translate_char(x):
        i = char_map.find(x)
        return char_map[i^1] if i >= 0 else x
    return ''.join(map(translate_char, f))

def name_conv(name): return fuzz_str(name)
def data_conv(buf, start=0):
    def xor_char(i): return ord(char_map[i % char_map_len]) if i < (1<<21) else 0
    return bytes([c ^ xor_char(i + start) for i,c in enumerate(buf)])

class EncStore:
    def __init__(self, base_dir):
        self.store = DirStore(base_dir)

    def head(self, path):
        header_vars = dict()
        real_path = self.store.get_real_path(path)
        if _path_is_dir(path):
            mime_type = 'dir'
        else:
            mime_type = get_mime_type(real_path)
        return dict(type=mime_type, rpath=real_path)

    def mv(self, path, new_path):
        return self.store.mv(name_conv(path), name_conv(new_path))
    def delete(self, path):
        return self.store.delete(name_conv(path))
    def lazy_read(self, path, range_req=''):
        fsz, start, end, data = self.store.lazy_read(name_conv(path), range_req)
        if _path_is_dir(path):
            return fsz, start, end, name_conv(data)
        else:
            def resp_part(iter, start):
                offset = 0
                for x in iter:
                    yield data_conv(x, start + offset)
                    offset += len(x)
            return fsz, start, end, resp_part(data, start)

    def read_dir(self, path):
        data = self.store.read_dir(name_conv(path))
        return name_conv(data)
    def read(self, path):
        data = self.store.read(name_conv(path))
        return data_conv(data)

    def write(self, path, content):
        if isinstance(content, str):
            content = content.encode('utf-8')
        return self.store.write(name_conv(path), data_conv(content))
