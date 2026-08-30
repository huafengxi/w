# -*- type=script -*-
# Core read-only file rpc: echo/head/read/dir/dir2. No mutation, no ext deps.
def interp(store, v='echo', src=None, **kw):
    def make_html(html='/w/view/layout.html', meta='<meta name="viewport" content="width=device-width,initial-scale=1">', **kw):
        return string.Template(store.read(html).decode()).safe_substitute(meta=meta, **kw)
    if v == 'echo':
        return dict(type='text/plain'), 'src=%s kw=%s'%(src, kw)
    elif v == 'head':
        return dict(type='text/plain'), repr(store.head(src))
    elif v == 'read':
        src_meta = store.head(src) or dict()
        if src_meta.get('type') == 'script' and not kw.get('_no_exec'):
            # type=script 文件：read 返回执行输出而非源码（任务 0830-1853-pfxr），
            # 使依赖 v=read 的视图（markdown/编辑等）对脚本拿到输出。复用
            # handler.run_script——与直接 GET 同一执行路径：失败/超时由脚本自身
            # 降级输出兜底，裸异常与直接 GET 等价（do_req 统一 500 traceback），
            # 认证经 wsgi BasicAuth 全局继承。_no_exec 防 rpc 脚本自引用递归。
            _meta, output = run_script(store, src, dict(kw, _no_exec='1'))
            if isinstance(output, bytes):
                output = output.decode('utf-8', 'replace')
            elif not isinstance(output, str):
                output = str(output)
            return dict(type='text/plain'), output
        return response_part_file(store, src)
    elif v == 'dir' or v == 'dir2':
        if v == 'dir2':
            content = store.find(src)
        else:
            content = store.read(src).split('\n')
        li = ['<li><a href="%s" target="target" ><code>%s</code></a></li>'%(name, name) for name in content]
        return dict(type='text/html'), make_html(title=src, css='code{margin-top:0; margin-bottom:0;} code:hover{background-color: lightgray;}', body='<ul>%s</ul>' % '\n'.join(li))
