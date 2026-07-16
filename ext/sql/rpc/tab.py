# -*- type=script -*-
# Render a whitespace-separated .tab file as an html table by piping it through
# ext/sql/bin/tsql (loads stdin into table t1, then runs the query). stdout is
# the result; stderr (tsql's own logging) is only surfaced on failure.
def interp(store, src=None, cmd='', **kw):
    sql = cmd or 'select * from t1'
    data = store.read(src)
    if data is None:
        raise Exception('no such file: %s' % src)
    if not isinstance(data, bytes):
        data = data.encode('utf-8')
    tsql = store.get_rpath('/w/ext/sql/bin/tsql/tsql.py')
    term = 'text' if kw.get('is_crawled_by_curl') else 'html'
    env = dict_updated(os.environ, sep=r'\s+', term=term, http_root='/', db_path=':memory:')
    p = Popen([sys.executable, tsql, sql], cwd=os.path.dirname(tsql),
              stdin=PIPE, stdout=PIPE, stderr=PIPE, env=env)
    out, err = p.communicate(data)
    if p.wait():
        raise Exception('tsql fail: sql=%s\n%s' % (sql, err.decode('utf-8', 'replace')))
    return dict(type='text/html' if term == 'html' else 'text/plain'), out
