from core.registry import suffix_rule

def register(reg):
    reg.register_mime(suffix_rule({'.enc': 'enc'}))
    reg.vmap_fragments.append('w/ext/encrypt/vmap.frag')
