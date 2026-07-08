# -*- type=script -*-
# File-mutation + archive rpc (ext/fileops): write/upload/append/del/mv/tar/sitemap.
# `archive` is an injected global provided by ext/fileops.

def interp(store, v='write', src=None, **kw):
    if v == 'write':
        store.write(src, kw.get('store_content', ''))
        return 'write %s ok'%(src)
    elif v == 'del':
        store.delete(src)
        return 'delete %s ok'%(src)
    elif v == 'upload':
        file = kw.get('file', '')
        if hasattr(file, 'read'):
            file = file.read()
        store.write(src, file)
        return 'upload %s, size=%d'%(src, len(file))
    elif v == 'append':
        store.write(src, (store.read(src) or '') + kw.get('text', ''))
        return 'append %s, text=%s'%(src, kw.get('text', ''))
    elif v == 'mv':
        return store.mv(src, kw.get('target'))
    elif v == 'tar':
        return dict(type='application/tar'), archive.archive_to_tar('local', src)
    elif v == 'sitemap':
        real_host = 'https://www.lockfree.top'
        path_list = archive.gen_sitemap('local', src)
        xml_template = '''<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n%s\n</urlset>'''
        return dict(type='text/xml'), xml_template %('\n'.join('<url><loc>%s%s</loc></url>'%(real_host, path) for path in path_list))
