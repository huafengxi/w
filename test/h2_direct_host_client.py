#!/usr/bin/env python3
"""A real independent web/client OS process; no inherited Popen map."""
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ext.sessiond import bridge as B
from ext.sessiond.rpc import stream
from stores.store import RootStore
from stores.dir_store import DirStore
from h2_direct_host import installed, api_call

options = json.loads(Path(sys.argv[1]).read_text())
store = RootStore([('/', DirStore(os.environ['W_WORKSPACE']))])
with installed(options):
    for line in sys.stdin:
        req = json.loads(line)
        try:
            special = req.pop('special', None)
            if special == 'rpc':
                b = B.get_bridge('/chat-a.agent', store=store)
                result = b.request(req['command'], timeout=5)
            elif special == 'sse_disconnect':
                b = B.get_bridge('/chat-a.agent', store=store)
                b.sup.ensure_started()
                _, it = stream.interp(store, session='/chat-a.agent')
                next(it)
                it.close()
                result = dict(subscribers=b.subscribers, status=b.sup.status_doc())
            elif special == 'detach':
                b = B.get_bridge('/chat-a.agent', store=store)
                result = b.sup.shutdown()
            elif special == 'identity':
                b = B.get_bridge('/chat-a.agent', store=store)
                result = dict(status=b.sup.status_doc(), handles=list(b.sup.life.handles),
                              imports=dict(proc=__import__('ext.sessiond.proc', fromlist=['x']).__file__,
                                           bridge=B.__file__), pid=os.getpid())
            else:
                req.setdefault('session', '/chat-a.agent')
                meta, body = api_call(store, **req)
                result = dict(meta=meta, body=json.loads(body))
            print(json.dumps(dict(ok=True, result=result)), flush=True)
        except Exception as e:
            print(json.dumps(dict(ok=False, error=repr(e))), flush=True)
