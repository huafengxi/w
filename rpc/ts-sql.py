# -*- type=script -*-
def interp(store, src=None, db='stat', tab='', m='', s='', e='', i=1, filt='', **kw):
    if not db: raise Exception('no db given')
    def time2ts(t): return int(time.mktime(time.strptime(t, '%Y%m%d%H%M%S')))
    def metric_groupby(m): return re.sub('(\w+)', r'sum(\1)', m)
    def render_index(index):
        index = ['%s /w/rpc/ts-sql.py?v=ts&db=%s&tab=%s&m=%s&s=%s&e=%s&i=%s&filt=%s\n'%(name, db, tab, urllib.quote(expr), s, e, i, urllib.quote(filt)) for name, tab, expr, filt in index]
        return ['IQuery /stat/stat.sh?db=%s\n'%(db), 'Index ts.index?v=code\n'] + [str(idx) for idx in index]
    def query_db(db, sql):
        conn = tsql.TConn(db, globals())
        header, index = conn.query(sql, globals())
        conn.close()
        return header, index

    if src and src.endswith('.db'):
        db = os.path.splitext(os.path.basename(src))[0]
        src = "%s/ts.index"%(os.path.dirname(src))
    if src and src.endswith('.index'): return render_index(re.findall('^(\w+) +(\w+) +(\S+)(?: +(.+))*', store.read(src), re.M))
   
    dbpath = store.get_rpath('/stat/%s.db'%(db))
    if not s: s = '20190101010101'
    if not e: e = '21000101010101'
    if not filt: filt = '1=1'
    if not tab: raise Exception('no table defined')
    sql = 'select ts,%s from %s where ts > %s and ts < %s and %s group by ts/%s'%(metric_groupby(m), tab, time2ts(s), time2ts(e), filt, i)
    logging.debug('db=%s sql=%s', db, sql)
    header, rows = query_db(dbpath, sql)
    return [','.join(header) + '\n', '\n'.join(','.join(map(str, r)) for r in rows)]
