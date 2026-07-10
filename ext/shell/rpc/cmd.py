# -*- type=script -*-
def interp(store, interp='', path='', cmd='', dir='', stream=2, **kw):
    logging.info('cmd: interp=%s path=%s cmd=%s dir=%s stream=%s', interp, path, cmd, dir, stream)
    if not interp:
        return dict(type='text/plain'), '2no #!cmd-interp directive for %s\n' % path
    import shlex, threading, queue
    rpath = store.get_rpath(path) or path
    cwd = dir or 'w/ext/shell/bin'
    cmd_list = shlex.split(interp) + [rpath] + shlex.split(cmd)
    stream = int(stream)
    if stream == 0:
        # Fully buffered: capture stdout, raise on non-zero exit.
        p = Popen(cmd_list, cwd=cwd, stdin=NULLFD, stdout=PIPE, stderr=PIPE)
        output, err = p.communicate()
        if err:
            logging.info('cmd: %s: err=%s', cmd_list, err)
        if p.wait():
            raise Exception('cmd Fail: cmd_list=%s %s'%(cmd_list, err))
        return dict(type='text/plain'), output
    if stream == 1:
        # Progressive stream: stdout+stderr merged, chunked to the client.
        def gen():
            p = Popen(cmd_list, cwd=cwd, stdin=NULLFD, stdout=PIPE, stderr=STDOUT)
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
        p = Popen(cmd_list, cwd=cwd, stdin=NULLFD, stdout=PIPE, stderr=PIPE)
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
