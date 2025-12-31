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
    def xor_char(i): return ord(char_map[i % char_map_len]) if i < (1<<21) else 255
    return bytes([c ^ xor_char(i + start) for i,c in enumerate(buf)])


class EncHandler:
    def __init__(self):
        pass

    def path_conv(self, p):
        base, fname = os.path.split(p)
        return os.path.join(base, name_conv(fname))

    def content_conv(self, buf, start=0):
        return data_conv(buf, start)
