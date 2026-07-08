from core.registry import suffix_rule

def register(reg):
    reg.register_mime(suffix_rule({'.md': 'text/md'}))
    reg.vmap_fragments.append('w/ext/markdown/vmap.frag')
