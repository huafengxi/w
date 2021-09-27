# -*- type=script -*-
def interp(store, src='', q='ls', ts='0', data='', **kw):
    rp = store.get_rpath(src)
    ts = int(ts)
    logging.debug('src=%s real_path=%s store=%s'%(src, rp, store.get_store(src)))
    if q == 'ls':
        return '\n'.join('%d %s'%(mtime, rp) for rp, mtime in lfop.list_mtime(rp, ts))
    elif q == 'get':
        logging.info('xxxx resp with mtime')
        return '%d\n%s'%(lfop.get_mtime(rp), file(rp).read())
    elif q == 'put':
        lfop.update_with_mtime(rp, data, ts)
    else:
        raise Fail('command not support: %s'%(q))
