# -*- type=script -*-
# Render a tabular file through tsql, deducing the mode from the src extension
# (mirrors the .db/.tab handling in ext/shell/rpc/popen.py). A .db is opened
# directly as a sqlite database; any other file is loaded into an in-memory
# table t1 via stdin, split on tabs. The sql query (cmd) comes from the
# ext/sql/vmap.frag entry. stdout is the result; stderr (tsql's own logging)
# is only surfaced on failure.
def interp(store, src=None, cmd='', **kw):
    sql = cmd or 'select * from t1'
    tsql = store.get_rpath('/tsql/tsql.py')
    term = 'text' if kw.get('is_crawled_by_curl') else 'html'
    env = dict_updated(os.environ, sep='\t', term=term, http_root='/')
    if src and src.endswith('.db'):
        env['db_path'], data = store.get_rpath(src), None
    else:
        data = store.read(src)
        if data is None:
            raise Exception('no such file: %s' % src)
        if not isinstance(data, bytes):
            data = data.encode('utf-8')
    p = Popen([sys.executable, tsql, sql], cwd=os.path.dirname(tsql),
              stdin=PIPE, stdout=PIPE, stderr=PIPE, env=env)
    out, err = p.communicate(data)
    if p.wait():
        raise Exception('tsql fail: sql=%s\n%s' % (sql, err.decode('utf-8', 'replace')))
    return dict(type='text/html' if term == 'html' else 'text/plain'), out
