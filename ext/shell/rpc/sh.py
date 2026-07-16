# -*- type=script -*-
# Stream a bash command's output, chunked to the client. This is the bash
# branch of the removed ext/shell/rpc/popen.py: it backs the sh: route, the
# fish shell (ext/shell/view/fish.html) and the org2* converters, which all
# run `bash -c <cmd>` with the source file (if any) piped in on stdin. stderr
# is merged into stdout so failures show inline; no content_len is set, so
# wsgiserver falls back to HTTP/1.1 chunked transfer-encoding.
def interp(store, i='', cmd='', dir='', src=None, input=None, pipe_src=0, stream=1, **kw):
    if pipe_src: input = store.read(src)
    env = dict_updated(os.environ, http_root='/', BASH_ENV=store.get_rpath('/w/ext/shell/sh.rc'), src=(src and (store.get_rpath(src) or src) or ''))
    if i:
        full_cmd = f'{i} {cmd}'.strip()
    else:
        full_cmd = cmd or 'sh'
    cmd_list = ['/bin/bash', '-c', full_cmd]
    cwd = os.path.realpath('./' + dir)
    stream = int(stream)
    def start(stderr):
        return Popen(cmd_list, cwd=cwd, env=env,
                     stdin=input and PIPE or NULLFD, stdout=PIPE, stderr=stderr)
    def feed(p):
        if input:
            import threading
            data = input if isinstance(input, bytes) else input.encode()
            def run():
                try: p.stdin.write(data)
                finally: p.stdin.close()
            threading.Thread(target=run, daemon=True).start()
    if stream == 0:
        # Fully buffered: capture stdout, raise on non-zero exit.
        p = start(PIPE)
        data = input and (input if isinstance(input, bytes) else input.encode()) or None
        output, err = p.communicate(data)
        if err:
            logging.info('sh: %s: err=%s', cmd_list, err)
        if p.wait():
            raise Exception('sh Fail: cmd_list=%s %s' % (cmd_list, err))
        return dict(type='text/plain'), output
    if stream == 1:
        # Progressive stream: stdout+stderr merged, chunked to the client.
        def gen():
            p = start(STDOUT)
            feed(p)
            try:
                fd = p.stdout.fileno()
                while True:
                    chunk = os.read(fd, 65536)
                    if not chunk: break
                    yield chunk
            finally:
                p.stdout.close()
                p.wait()
        return dict(type='text/plain'), gen()
    def gen():
        # One HTTP stream carries both streams: each line is prefixed with a tag
        # byte ('1'=stdout, '2'=stderr) so the client can route it to the result
        # pane or the log console. stderr and stdout are drained by separate
        # threads through a queue to interleave them as they are produced.
        import threading, queue
        p = start(PIPE)
        feed(p)
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
