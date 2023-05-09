# -*- type=script -*-
def interp(store, i='', src=None, input=None, cmd='', dir='', **kw):
    logging.info('popen: pwd=%s i=%s src=%s input=%s cmd=%s dir=%s kw=%s', os.getcwd(), i, src, input, cmd, dir, kw)
    def is_html(content):
        if type(content) == list or type(content) == tuple:
           content = content[0]
        return content and re.match(b'^<.*>$', content, re.S) and len(re.findall(b'<.*?>', content)) > 3
    def popen(cmd_list, input, env):
        p = Popen(cmd_list, cwd=os.path.realpath('./' + dir), env=env, stdin=input and PIPE or NULLFD, stdout=PIPE, stderr=PIPE)
        output, err = p.communicate(input)
        if err:
            logging.info('cmd: %s: err=%s', cmd_list, err)
        if p.wait():
            raise Exception('popen Fail: req_cmd=%s cmd_list=%s %s'%(cmd, cmd_list, err))
        return dict(type= 'text/html' if is_html(output) else 'text/plain'), output
    term = kw.get('is_crawled_by_curl', False) and 'term' or 'html'
    input = input or (src and store.read(src))
    env = dict_updated(os.environ, term=term, http_root='/')
    if not i:
       if src.endswith('.db'): i = 'db'
       elif src.endswith('.sh'): i = 'bash'
       elif src.endswith('.tab'): i = 'tsql'
       else: i = 'bash'
    if i == 'bash':
        if not cmd: cmd = 'sh'
        cmd_list = ['/bin/bash', '-c', cmd]
    elif i == 'conv':
        cmd_list = [sys.executable, 'bin/conv.py', 'guess']
        if cmd: cmd_list.append(cmd)
    elif i == 'tsql':
        cmd_list = [sys.executable, 'tsql/tsql.py', cmd]
        env.update(sep='\t')
    elif i == 'db':
        cmd_list = [sys.executable, 'tsql/tsql.py', cmd]
        env.update(db_path=store.get_rpath(src), sep='\t')
        input = None
    elif i == 'ish':
        if not cmd: cmd = 'ish'
        cmd_args = ['%s=%s'%(k,v) for k,v in kw.items()]
        cmd_list = [sys.executable, os.path.realpath('hit/hit.py')] + cmd.split() + cmd_args
    else:
        return 'popen not support: i=%s src=%s cmd=%s kw=%s'%(i, src, cmd, kw)
    logging.debug('i=%s cmd=%s input=%.100s src=%s', i, cmd_list, input, src)
    return popen(cmd_list, input, env)

