from core.registry import suffix_rule

def register(reg):
    reg.register_mime(suffix_rule({'.md': 'text/md'}))
