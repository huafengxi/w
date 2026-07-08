from core.registry import suffix_rule
from ext.encrypt.enc_store import EncStore

def register(reg):
    reg.register_store('Enc', EncStore)
    reg.register_mime(suffix_rule({'.enc': 'enc'}))
    reg.vmap_fragments.append('w/ext/encrypt/vmap.frag')
    reg.fstab_fragments.append('w/ext/encrypt/fstab.frag')
