# -*- type=script -*-
# Core read-only file rpc: echo/head/find/read/dir/dir2. No mutation, no ext deps.
# t= presets live on registry.REGISTRY.find_presets (populated by ext/media).
import core.registry as registry

def interp(store, v='echo', src=None, **kw):
    def make_html(html='/w/view/template.html', meta='<meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="https://fonts.googleapis.com/icon?family=Material+Icons">', **kw):
        return string.Template(store.read(html).decode()).safe_substitute(meta=meta, **kw)
    if v == 'echo':
        return dict(type='text/plain'), 'src=%s kw=%s'%(src, kw)
    elif v == 'head':
        return dict(type='text/plain'), repr(store.head(src))
    elif v == 'find':
        pat = kw.get('pat') or registry.REGISTRY.find_presets.get(kw.get('t'), '')
        li = [i for i in store.find(src) if re.search(pat, i, re.IGNORECASE)]
        return dict(type='text/plain'), '\n'.join(li)
    elif v == 'read':
        return response_part_file(store, src)
    elif v == 'dir' or v == 'dir2':
        if v == 'dir2':
            content = store.find(src)
        else:
            content = store.read(src).split('\n')
        li = ['<li><a href="%s" target="target" ><code>%s</code></a></li>'%(name, name) for name in content]
        return dict(type='text/html'), make_html(title=src, css='code{margin-top:0; margin-bottom:0;} code:hover{background-color: lightgray;}', body='<ul>%s</ul>' % '\n'.join(li))
