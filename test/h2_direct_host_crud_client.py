#!/usr/bin/env python3
"""Real offline web OS process, requests through original CRUD/Bridge/API/CLI."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ext.sessiond import sessions as P, bridge as B
from ext.sessiond.rpc import stream
from stores.store import RootStore
from stores.dir_store import DirStore
import h2_direct_host as H
import h2_direct_host_crud as C

options = json.loads(Path(sys.argv[1]).read_text())
store = RootStore([('/', DirStore(os.environ['W_WORKSPACE']))])
streams = {}
spawn_attempts = []


def audit(event, args):
    # Observe actual subprocess launches, without substituting Popen or results.
    if event == 'subprocess.Popen':
        executable, argv, cwd, _env = args
        spawn_attempts.append(dict(executable=executable, argv=argv, cwd=cwd, time=time.time()))


sys.addaudithook(audit)
with C.installed(options) as registry:
    for line in sys.stdin:
        req = json.loads(line)
        try:
            special = req.pop('special', None)
            session = req.get('session', '')
            if special == 'spawn_audit':
                result = list(spawn_attempts)
            elif special == 'create':
                names = req.get('names')
                with patch.object(P, 'new_session_name', side_effect=names) if names else contextlib.nullcontext():
                    result = registry.create(req['profile'])
            elif special == 'cli':
                # Explicit read-only source copied into this test's workspace.
                spec = importlib.util.spec_from_file_location('fixture_cli', os.environ['H2_CLI_SOURCE'])
                mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    rc = mod.main(req['argv'])
                result = dict(rc=rc, stdout=out.getvalue(), stderr=err.getvalue())
            elif special in ('rpc', 'identity', 'events'):
                b = B.get_bridge(session, store=store)
                if special == 'rpc':
                    result = b.request(req['command'], timeout=5)
                elif special == 'identity':
                    result = dict(status=b.sup.status_doc(), handles=list(b.sup.life.handles),
                                  registration=b.sup.registration, web=os.getpid(),
                                  imports=dict(proc=P.__file__, bridge=B.__file__))
                else:
                    it = b.iter_events(0)
                    result = dict(events=list(it) if req.get('drain') else [next(it)], terminated=b.terminated)
            elif special == 'stream_open':
                meta, it = stream.interp(store, session=session)
                first = next(it)
                streams[session] = it
                result = dict(meta=meta, first=first.decode())
            elif special == 'stream_finish':
                it = streams.pop(session)
                frames = [x.decode() for x in it]
                it.close()
                key = str(Path(P.WS) / session.lstrip('/').replace('.agent', '.jsonl'))
                b = B._BRIDGES[key]
                result = dict(frames=frames, terminated=b.terminated, subscribers=b.subscribers)
            elif special == 'cache_unstarted':
                # Construct the real Bridge/Supervisor without get_bridge's
                # immediate ensure_started, then test its cached start entry.
                with registry.lock():
                    meta, body = B.resolve_agent(store, session)
                    declaration = json.loads(body)
                    if not declaration.get('ok'):
                        raise ValueError(declaration)
                    b = registry.bridge_factory(declaration['session'], cwd=declaration['cwd'],
                                                profile=declaration['profile'], client_factory=registry.client_factory)
                    key = P.resolve_session_path(declaration['session'])
                    with B._BRIDGES_LOCK:
                        B._BRIDGES[key] = b
                    result = dict(gen=b.sup.gen, state=b.sup.state, handles=list(b.sup.life.handles))
            elif special == 'cached_change':
                key = str(Path(P.WS) / session.lstrip('/').replace('.agent', '.jsonl'))
                result = B._BRIDGES[key].sup.change(req['operation'], req.get('expected_instance'))
            elif special == 'cached':
                # Do NOT resolve the deleted declaration: exercise the old object.
                key = str(Path(P.WS) / session.lstrip('/').replace('.agent', '.jsonl'))
                b = B._BRIDGES[key]
                before = b.sup.gen
                b.sup.ensure_started()
                result = dict(before=before, after=b.sup.gen, sent=b.sup.send({'type':'effect','value':'forbidden'}),
                              status=b.sup.status_doc(), terminated=b.terminated)
            elif special == 'history_appears_during_stop':
                original_stop = H.Lifecycle._stop
                def stop_then_create(life, d):
                    original_stop(life, d)  # real identity-scoped close and wait
                    Path(life.session).write_bytes(b'late unknown file' + bytes([10]))
                with patch.object(H.Lifecycle, '_stop', stop_then_create):
                    meta, body = C.api_call(store, **req)
                result = dict(meta=meta, body=json.loads(body))
            elif special == 'inject_identity_denial':
                with patch.object(H, 'identity', side_effect=PermissionError('injected identity denial')):
                    meta, body = C.api_call(store, **req)
                result = dict(meta=meta, body=json.loads(body))
            else:
                meta, body = C.api_call(store, **req)
                result = dict(meta=meta, body=json.loads(body))
            print(json.dumps(dict(ok=True, result=result)), flush=True)
        except Exception as e:
            print(json.dumps(dict(ok=False, error=repr(e))), flush=True)
    for b in list(B._BRIDGES.values()):
        b.sup.shutdown()  # web exit = detach only
