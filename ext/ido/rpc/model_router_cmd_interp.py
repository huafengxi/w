# -*- type=script -*-
# RPC form of ext/shell/bin/model_router_cmd_interp.py: bridge markdown.html
# inline commands to model_router's ido without the cmd.py + bin subprocess hop.
# markdown.html posts {path, cmd}; we turn the dir path into ido dot-form and
# dispatch $model_router_dir/ido/ido.py <a.b.c>.<cmd>. Output is streamed
# line-framed with a leading tag byte ('1'=stdout -> result pane, '2'=stderr ->
# log console), matching what the cmd widget in markdown.html consumes.

def dot_form(path):
    # e.g. .../R/output/prod_g/v1/release-regression.md -> prod_g.v1
    # keep only the dir segments under the model_router output root, dropping
    # the leading tree up to `output` and the trailing .md filename.
    parts = [p for p in path.strip('/').split('/') if p]
    if parts and parts[-1].endswith('.md'):
        parts = parts[:-1]
    if 'output' in parts:
        parts = parts[len(parts) - 1 - parts[::-1].index('output') + 1:]
    return '.'.join(parts)

def interp(store, path='', cmd='', **kw):
    import shlex, threading, queue
    rpath = store.get_rpath(path) or path
    tokens = shlex.split(cmd)
    sub, opts = (tokens[0], tokens[1:]) if tokens else ('', [])
    root = os.environ.get('model_router_dir', '/data/yuanqi.xhf/workspace/ob_modelrouter')
    ido = os.path.join(root, 'ido', 'ido.py')
    dotted = dot_form(rpath)
    target = '%s.%s' % (dotted, sub) if dotted else sub
    logging.info('model_router: dispatching %r %s via %s', target, opts, ido)
    cmd_list = [sys.executable, ido, target] + opts
    def gen():
        # One HTTP stream carries both streams: each line is prefixed with a tag
        # byte ('1'=stdout, '2'=stderr) so the client routes it to the result
        # pane or the log console. Separate reader threads drain stdout/stderr
        # through a queue to interleave them as they are produced.
        p = Popen(cmd_list, cwd=root, stdin=NULLFD, stdout=PIPE, stderr=PIPE)
        q = queue.Queue()
        def reader(f, tag):
            try:
                for line in iter(f.readline, b''):
                    if not line.endswith(b'\n'): line += b'\n'
                    q.put(tag + line)
            finally:
                f.close()
                q.put(None)
        for f, tag in [(p.stdout, b'1'), (p.stderr, b'2')]:
            threading.Thread(target=reader, args=(f, tag), daemon=True).start()
        done = 0
        while done < 2:
            item = q.get()
            if item is None: done += 1
            else: yield item
        p.wait()
    return dict(type='text/plain'), gen()
