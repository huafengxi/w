from ext.dsync import local_file_operator as lfop

def register(reg):
    reg.script_globals['lfop'] = lfop
    reg.vmap_fragments.append('w/ext/dsync/vmap.frag')
