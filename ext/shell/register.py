from core.registry import suffix_rule

def register(reg):
    reg.register_mime(suffix_rule({'.wsh': 'wsh', '.ish': 'ish'}))
    reg.bin_dirs.append('w/ext/shell/bin')
