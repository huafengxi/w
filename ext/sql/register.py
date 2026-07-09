from core.registry import suffix_rule

def register(reg):
    reg.register_mime(suffix_rule({'.tab': 'tab', '.db': 'db'}))
