# -*- type=script -*-

def interp(store, v='echo', src=None, **kw):
    def make_html(html='/w/view/template.html', meta='<meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="https://fonts.googleapis.com/icon?family=Material+Icons">', **kw):
        return string.Template(store.read(html).decode()).safe_substitute(meta=meta, **kw)
    if v == 'echo':
        return dict(type='text/plain'), 'src=%s kw=%s'%(src, kw)
    elif v == 'pack':
        return dict(type='text/plain'), pack()
    elif v == 'head':
        return dict(type='text/plain'), repr(store.head(src))
    elif v == 'find':
        pat_def = dict(audio='[.]mp3$|[.]wav$|[.]ogg$', text='[.]txt$', img='[.]png$|[.]jpg$|[.]gif$', plist='[.]plist$')
        pat = pat_def.get(kw.get('t'), '')
        li = [i for i in store.find(src) if re.search(pat, i, re.IGNORECASE)]
        return dict(type='text/plain'), '\n'.join(li)
    elif v == 'read':
        return response_part_file(store, src)
    elif v == 'write':
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
    elif v == 'dir' or v == 'dir2':
        if v == 'dir2':
            content = store.find(src)
        else:
            content = store.read(src).split('\n')
        li = ['<li><a href="%s" target="target" ><code>%s</code></a></li>'%(name, name) for name in content]
        return dict(type='text/html'), make_html(title=src, css='code{margin-top:0; margin-bottom:0;} code:hover{background-color: lightgray;}', body='<ul>%s</ul>' % '\n'.join(li))
    elif v == 'tar':
        return dict(type='application/tar'), archive.archive_to_tar('local', src)
    elif v == 'sitemap':
        real_host = 'https://www.lockfree.top'
        path_list = archive.gen_sitemap('local', src)
        xml_template = '''<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n%s\n</urlset>'''
        return dict(type='text/xml'), xml_template %('\n'.join('<url><loc>%s%s</loc></url>'%(real_host, path) for path in path_list))

