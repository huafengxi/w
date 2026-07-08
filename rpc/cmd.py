# -*- type=script -*-
def interp(store, interp='', path='', cmd='', dir='', **kw):
    logging.info('cmd: interp=%s path=%s cmd=%s dir=%s', interp, path, cmd, dir)
    if not interp:
        return dict(type='text/plain'), '2no #!cmd-interp directive for %s\n' % path
    import shlex, threading, queue
    rpath = store.get_rpath(path) or path
    cwd = dir or 'w/bin'
    cmd_list = shlex.split(interp) + [rpath, cmd]
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
