from core.registry import suffix_rule

def register(reg):
    reg.register_mime(suffix_rule({'.org': 'text/org'}))
    reg.bin_dirs.append('w/ext/org/bin')
