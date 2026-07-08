from core.registry import suffix_rule
from ext.sql.sql_store import SqlStore

def register(reg):
    reg.register_store('Sql', SqlStore)
    reg.register_mime(suffix_rule({'.tab': 'tab', '.db': 'db'}))
    reg.vmap_fragments.append('w/ext/sql/vmap.frag')
