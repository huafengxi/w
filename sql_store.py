class SqlStore:
    def __init__(self, conn_str):
        self.conn_str = conn_str
        mod, conn = make_sql_conn(conn_str)
        cursor = conn.cursor()
        cursor.execute('create table if not exists __root_dict__ (id int not null, name varchar(256) not null, value varchar(65536), ref int, primary key (id, name));')
        try:
            cursor.execute('insert into __root_dict__ values(0, "next_id", "", 1);')
        except mod.IntegrityError:
            pass
        conn.commit()
    def get_sql_conn(self):
        return make_sql_conn(self.conn_str)
    def sql_query(self, sql):
        logging.info('execute sql: %s', sql)
        mod, conn = self.get_sql_conn()
        cursor = conn.cursor()
        for stmt in sql.split('\n'):
            cursor.execute(stmt)
        conn.commit()
        return cursor
    def create(self):
        sql = '''update __root_dict__ set ref = ref+1 where id = 0 and name = "next_id";
              select ref from __root_dict__ where id = 0 and name = "next_id";'''
        return int(self.sql_query(sql).fetchone()[0])
    def delete(self, id):
        self.sql_query('delete from __root_dict__ where id=%s'%(id))
    def update(self, id, attr):
        mod, conn = self.get_sql_conn()
        if mod.paramstyle == 'qmark':
            param = '?'
        elif mod.paramstyle == 'format':
            param = '%s'
        sql = '''replace into __root_dict__  values(%d, %s, %s, %s)''' %(id, param, param, param)
        def get_value(v):
            if type(v) == int:
                return None
            else:
                return v
        def get_ref(v):
            if type(v) == int:
                return v
            else:
                return None
        cursor = conn.cursor()
        cursor.executemany(sql, [(k, get_value(v), get_ref(v)) for k, v in attr.items()])
        conn.commit()
        
    def list(self, id):
        sql = '''select name,ref from __root_dict__ where id = %ld''' % (id)
        return [(str(name), ref) for name, ref in self.sql_query(sql).fetchall()]
    def get(self, id, key_list):
        if not key_list:
            sql = '''select name, value, ref from __root_dict__ where id = %d'''%(id)
        else:
            sql = '''select name, value, ref from __root_dict__ where id = %d and name in (%s)'''%(id, ','.join(['"%s"'%(key) for key in key_list]))
        return dict((name, ref == None and str(value) or int(ref)) for name, value, ref in self.sql_query(sql).fetchall())

class SqlDirStore:
    def __init__(self, get_sql_conn):
        self.obj_store = SqlObjStore(get_sql_conn)
    def do_search(self, path):
        norm_path = os.path.normpath(path)
        value = 0
        id = 0
        for part in norm_path.split('/'):
            if id == None:
                raise StoreException('resolve "%s" to a regular file: "%s"'%(norm_path, part))
            if not part: continue # igore '//'
            if part == '.': continue
            obj = self.obj_store.get(id, [part])
            value = obj.get(part)
            if type(value) == int:
                id = value
            else:
                id = None
        return value
    def delete(self, path):
        pass
    def mkdir(self, path):
        norm_path = os.path.normpath(path)
        value = 0
        id = 0
        for part in norm_path.split('/'):
            if id == None:
                raise StoreException('resolve "%s" to a regular file: "%s"'%(norm_path, part))
            if not part: continue # igore '//'
            if part == '.': continue
            obj = self.obj_store.get(id, [part])
            value = obj.get(part)
            if value == None:
                new_id = self.obj_store.create()
                self.obj_store.update(id, {part:new_id})
                value = new_id
            if type(value) == int:
                id = value
            else:
                id = None
        return value
    def write(self, path, content):
        norm_path = os.path.normpath(path)
        m = re.match('(.*)/([^/]+)$', '/' + norm_path)
        if not m: raise StoreException('"%s" is not a file'%(path))
        dir, file = m.groups()
        id = self.do_search(dir)
        if type(id) != int:
            raise StoreException('"%s" is not a dir'%(dir))
        self.obj_store.update(id, {file:content})
    def read(self, path):
        value = self.do_search(path)
        if type(value) == int:
             dir_content = ['..'] + [str(row[0]) + ['', '/'][row[1] != None] for row in self.obj_store.list(value)]
             ret = str('\n'.join(dir_content))
             return ret
        else:
            return value
    def head(self, path):
        value = self.do_search(path)
        header_vars = {}
        if type(value) == int:
            mime_type = 'dir'
        elif type(value) == str:
            mime_type = get_mime_type(path)
            header_vars = dict(re.findall(r'-\*-\s*(\w+)\s*=\s*(.*?)\s*-\*-', value))
        else:
            raise StoreException('no "%s" found'%(path))
        meta = dict(type=mime_type)
        meta.update(header_vars)
        return meta
    def __repr__(self):
        return 'SqlDirStore()'

def make_sqlite_conn(path):
    import sqlite3
    conn = sqlite3.connect(path)
    conn.text_factory = str
    return sqlite3, conn
def make_sql_conn(conn_str):
    sqlite_prefix = 'sqlite:'
    if conn_str.starswitch(sqlite_prefix):
        return make_sqlite_conn(conn_str[len(sqlite_prefix):])
