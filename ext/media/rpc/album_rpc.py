# -*- type=script -*-
def interp(store, q=None, e=None, src=None, **kw):
    a = AlbumDB(src, store)
    attrs = 'name tags circle va'.split()
    if q == None:
        alist = a.collect(None, *attrs)
        return dict(type='text/plain'), json.dumps(alist)
    elif e == None:
        return dict(type='text/plain'), '\n'.join(a.distinct(q))
    else:
        li = a.filt(q, e)
        alist = a.collect(li, *attrs)
        return dict(type='text/plain'), json.dumps(alist)
