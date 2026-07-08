from core.registry import suffix_rule
from ext.shell.cmd_store import CmdStore

def register(reg):
    reg.register_store('Cmd', CmdStore)
    reg.register_mime(suffix_rule({'.wsh': 'wsh', '.ish': 'ish'}))
    reg.bin_dirs.append('w/ext/shell/bin')
    reg.vmap_fragments.append('w/ext/shell/vmap.frag')
    reg.fstab_fragments.append('w/ext/shell/fstab.frag')
