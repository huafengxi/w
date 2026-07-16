# -*- type=script -*-
# Stream a bash command's output, chunked to the client. This is the bash
# branch of the removed ext/shell/rpc/popen.py: it backs the sh: route, the
# fish shell (ext/shell/view/fish.html) and the org2* converters, which all
# run `bash -c <cmd>` with the source file (if any) piped in on stdin. stderr
# is merged into stdout so failures show inline; no content_len is set, so
# wsgiserver falls back to HTTP/1.1 chunked transfer-encoding.
def interp(store, cmd='sh', dir='', src=None, input=None, **kw):
    input = input or (src and store.read(src))
    env = dict_updated(os.environ, http_root='/')
    cmd_list = ['/bin/bash', '-c', cmd or 'sh']
    def gen():
        p = Popen(cmd_list, cwd=os.path.realpath('./' + dir), env=env,
                  stdin=input and PIPE or NULLFD, stdout=PIPE, stderr=STDOUT)
        if input:
            import threading
            data = input if isinstance(input, bytes) else input.encode()
            def feed():
                try: p.stdin.write(data)
                finally: p.stdin.close()
            threading.Thread(target=feed, daemon=True).start()
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
