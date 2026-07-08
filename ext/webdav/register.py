from ext.webdav.webdav_store import WebDavStore

def register(reg):
    reg.register_store('WebDav', WebDavStore)
    reg.fstab_fragments.append('w/ext/webdav/fstab.frag')
