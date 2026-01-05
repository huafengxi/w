import os
char_map = 'iFXhbcNYDuUgsjrIMJwTpPAqnyvOfSxeEzWBkdtQmlZCoRVKLGHa'
char_map_len = len(char_map)
def fuzz_str(f):
    def translate_char(x):
        i = char_map.find(x)
        return char_map[i^1] if i >= 0 else x
    return ''.join(map(translate_char, f))

def name_conv(name): return fuzz_str(name)
def data_conv(buf, start):
    def xor_char(i): return ord(char_map[i % char_map_len]) if i < (1<<21) else 0
    return bytes([c ^ xor_char(i + start) for i,c in enumerate(buf)])


class EncHandler:
    def __init__(self):
        pass

    def _find_enc_root(self, path):
        p = os.path.abspath(path)
        if os.path.isdir(p):
            current_dir = p
        else:
            current_dir = os.path.dirname(p)

        while True:
            if os.path.exists(os.path.join(current_dir, '.need_enc')):
                return current_dir
            parent_dir = os.path.dirname(current_dir)
            if parent_dir == current_dir:  # Reached the root of the filesystem
                return None
            current_dir = parent_dir
        return current_dir

    def path_conv(self, p):
        enc_root = self._find_enc_root(p)
        if not enc_root:
            base, fname = os.path.split(p)
            return os.path.join(base, name_conv(fname))

        relative_path = os.path.relpath(p, enc_root)
        fuzzed_parts = [name_conv(part) for part in relative_path.split(os.sep)]
        return os.path.join(enc_root, *fuzzed_parts)

    def content_conv(self, buf, start=0):
        return data_conv(buf, start)
