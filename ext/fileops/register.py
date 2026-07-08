from ext.fileops import archive

def register(reg):
    reg.script_globals['archive'] = archive
    reg.vmap_fragments.append('w/ext/fileops/vmap.frag')
