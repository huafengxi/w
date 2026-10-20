#!/usr/bin/env python3
"""Real W HTTP -> socket -> P -> isolated pi, deterministic localhost model.

Requires PI_TEST_BIN, H2_P_SOURCE and H2_A_SOURCE. No user credentials/config,
production socket, browser or real model endpoint. All evidence is retained.
"""
import base64
import ctypes
import http.server
import json
import os
from pathlib import Path
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

W = Path(__file__).resolve().parents[1]
P_SOURCE, A_SOURCE = Path(os.environ['H2_P_SOURCE']), Path(os.environ['H2_A_SOURCE'])
PI = Path(os.environ['PI_TEST_BIN']).resolve()
ROOT = Path(tempfile.mkdtemp(prefix='wm-')).resolve()
WS = ROOT/'ws'; (WS/'bots/profiles').mkdir(parents=True)
(WS/'bots/profiles/good.json').write_text(json.dumps(dict(form='interactive', cwd='.')))
for part in ('home', 'config'):
    (ROOT/part).mkdir(mode=0o700)
print('ORDINARY_HTTP_ROOT=' + str(ROOT), flush=True)
CAPTURE = []


class Model(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args): pass

    def do_POST(self):
        CAPTURE.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
        self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.end_headers()
        for delta, finish in [({'role': 'assistant', 'content': 'minimal fixture answer'}, None), ({}, 'stop')]:
            obj = dict(id='fixture', object='chat.completion.chunk', created=1, model='fixture',
                       choices=[dict(index=0, delta=delta, finish_reason=finish)])
            self.wfile.write(('data: ' + json.dumps(obj) + '\n\n').encode())
        self.wfile.write(b'data: [DONE]\n\n'); self.wfile.flush()


model = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Model)
threading.Thread(target=model.serve_forever, daemon=True).start()
(ROOT/'config/models.json').write_text(json.dumps({'providers': {'fixture': {
    'baseUrl': 'http://127.0.0.1:%d/v1' % model.server_port, 'apiKey': 'fixture-not-a-secret',
    'api': 'openai-completions', 'models': [{'id': 'fixture', 'name': 'fixture', 'reasoning': False,
    'input': ['text'], 'contextWindow': 100000, 'maxTokens': 1024,
    'cost': {'input': 0, 'output': 0, 'cacheRead': 0, 'cacheWrite': 0}}]}}}))
(ROOT/'config/settings.json').write_text(json.dumps({'retry': {'enabled': False}, 'compaction': {'enabled': False}}))
(ROOT/'host-id').write_text(socket.gethostname()+' test\n')
(ROOT/'auth').write_text('fixture:fixture\n')
(ROOT/'fstab').write_text('/w Dir %s\n/sessiond Dir %s\n/ Dir %s\n' % (W, W/'ext/sessiond', WS))
wrapper = ROOT/'native-pi'
wrapper.write_text('#!/bin/sh\nexport PI_CODING_AGENT_DIR='+shlex.quote(str(ROOT/'config'))+
                   '\nexport PI_OFFLINE=1\nexec '+shlex.quote(str(PI))+
                   ' --provider fixture --model fixture --no-skills --no-prompt-templates --no-extensions "$@"\n')
wrapper.chmod(0o700)
env = dict(HOME=str(ROOT/'home'), PATH=os.environ['PATH'], TMPDIR=str(ROOT),
           PYTHONDONTWRITEBYTECODE='1', PI_CODING_AGENT_DIR=str(ROOT/'config'), PI_OFFLINE='1',
           NO_PROXY='127.0.0.1,localhost', W_WORKSPACE=str(WS), W_AGENTD_DIR=str(A_SOURCE),
           W_SESSION_HOST_ENTRY=str(P_SOURCE/'pi_host.py'), W_SESSION_RUNTIME=str(ROOT/'external'),
           W_SESSION_PI_BIN=str(wrapper), W_HOST_ID_FILE=str(ROOT/'host-id'),
           W_AUTH_FILE=str(ROOT/'auth'), W_FSTAB=str(ROOT/'fstab'))
os.environ.clear(); os.environ.update(env)
sys.path.insert(0, str(W))
from ext.sessiond.bootstrap import configure
from ext.sessiond import lifecycle as H

if sys.platform.startswith('linux'):
    assert ctypes.CDLL(None).prctl(36, 1, 0, 0, 0) == 0
registry = configure()
created = registry.create('good')
name, site = created['name'], created['agent_site']
folder = Path(created['agent_path']).parent
history = Path(created['session_path'])
servers, known, orphan_waits = [], [], []
AUTH = 'Basic ' + base64.b64encode(b'fixture:fixture').decode()
base = ''


def wait(predicate, seconds=30):
    end = time.monotonic()+seconds
    while time.monotonic()<end:
        value = predicate()
        if value: return value
        time.sleep(.05)
    raise TimeoutError('condition not reached; evidence '+str(ROOT))


def start_web():
    global base
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]
    log = (ROOT/('web%d.log' % len(servers))).open('wb')
    process = subprocess.Popen([sys.executable, '-B', str(W/'core/server.py'), '127.0.0.1:%d' % port],
                               env=env, cwd=ROOT, stdout=log, stderr=log)
    log.close(); servers.append(process); base = 'http://127.0.0.1:%d' % port
    def reachable():
        assert process.poll() is None, 'web failed; '+str(ROOT)
        try:
            urllib.request.urlopen(base+'/', timeout=1).close()
        except urllib.error.HTTPError as e:
            return e.code == 401
        except OSError:
            return False
    wait(reachable, 10)


def call(op, **kw):
    data = urllib.parse.urlencode(dict(op=op, session=site, **kw)).encode()
    req = urllib.request.Request(base+'/sessiond/rpc/api.py', data=data, headers={'Authorization': AUTH})
    try:
        with urllib.request.urlopen(req, timeout=30) as response: raw = response.read()
    except urllib.error.HTTPError as e:
        raw = e.read()
    obj = json.loads(raw)
    with (ROOT/'http.jsonl').open('a') as log: log.write(json.dumps(dict(op=op, response=obj))+'\n')
    return obj


def remember():
    d = json.loads((folder/'host.json').read_text())
    assert 'child' not in d
    assert 'known' not in json.loads((folder/(name+'.agent')).read_text())['runtime']
    known.extend(H.group_members(d['host']))  # harness observations only
    return d


result = dict(root=str(ROOT), ok=False)
try:
    start_web()
    attached = call('attach'); assert attached['ok'], attached
    first = attached['gen']; original = remember()
    assert set(p.name for p in folder.iterdir()) <= {name+'.agent', name+'.jsonl', 'host.json', 'host.log', 's'}
    assert first == H.Lifecycle.process_key(original['host'])
    state = call('chat_state'); assert state['ok'] and state['state']=='ready', state
    assert call('cmd', cmd=json.dumps(dict(type='prompt', id='fixture-prompt', message='minimal first message')))['ok']
    wait(lambda: len(CAPTURE)==1 and call('chat_state').get('state')=='success')
    attached = call('attach'); assert attached['ok'], attached
    user = next(e for e in attached['entries'] if e.get('type')=='message' and e['message']['role']=='user')
    before = history.read_bytes()
    # Kill ONLY the fixture Web. P/pi must keep their identities and history.
    servers[-1].terminate(); servers[-1].wait(timeout=10)
    assert H.same(H.identity(original['host']['pid']), original['host'])
    if sys.platform.startswith('linux'):
        def reap(): orphan_waits.append(os.waitpid(original['host']['pid'], 0))
        threading.Thread(target=reap, daemon=True).start()
    start_web()
    reattached = call('attach'); assert reattached['ok'] and reattached['gen']==first, reattached
    assert remember()==original and history.read_bytes()==before
    reloaded = call('reload', expected_instance=first); assert reloaded['ok'], reloaded
    assert reloaded['gen'] != first
    remember()
    assert history.read_bytes().startswith(before)
    after_reload = history.read_bytes()
    assert not call('clear', expected_instance=first)['ok']
    assert history.read_bytes()==after_reload
    edited = call('regenerate', entryId=user['id'], message='minimal edited first', images='[]')
    assert edited['ok'], edited
    wait(lambda: len(CAPTURE)==2 and call('chat_state').get('state')=='success')
    assert history.read_bytes().startswith(after_reload)
    assert call('queue_rpc', action='clear_queue')['ok']
    cleared = call('clear', expected_instance=reloaded['gen']); assert cleared['ok'], cleared
    remember()
    state = call('chat_state'); assert state['ok'] and state['state']=='ready', state
    empty = call('attach'); assert empty['ok'] and not any(e.get('type')=='message' for e in empty['entries']), empty
    assert set(p.name for p in folder.iterdir()) <= {name+'.agent', name+'.jsonl', 'host.json', 'host.log', 's'}
    deleted = call('delete_session', confirm='1', expected_instance=cleared['gen'])
    assert deleted['ok'] and deleted['process']['settled'], deleted
    assert not folder.exists()
    assert (WS/'agents/bot'/name/'inbox').is_dir()
    # Exercise native pi's portable stdin-EOF contract without Linux prctl.
    # This tests the actual installed pi, not a fake that conveniently exits.
    eof_root = ROOT/'stdin-eof'; eof_root.mkdir()
    launch = ('import sys;sys.path.insert(0,sys.argv.pop(1));import pi_host;'
              'pi_host.Host.launch_argv=lambda self,c:c.argv();sys.exit(pi_host.main())')
    cfg = dict(sessionFile=str(eof_root/'history.jsonl'), cwd=str(eof_root), sessionName='eof',
               piBinary=str(wrapper), socketPath=str(eof_root/'s'), extensionArgv=[], env=env)
    with (eof_root/'host.log').open('wb') as log:
        p = subprocess.Popen([sys.executable, '-B', '-c', launch, str(P_SOURCE), '-'],
                             stdin=subprocess.PIPE, stdout=log, stderr=log, env=env,
                             start_new_session=True)
        child = None
        try:
            p.stdin.write(json.dumps(cfg).encode()); p.stdin.close()
            wait(lambda: (eof_root/'host.json').exists())
            with socket.socket(socket.AF_UNIX) as s:
                s.settimeout(25); s.connect(cfg['socketPath'])
                s.sendall(b'{"type":"get_state","id":"eof-ready"}\n')
                with s.makefile('rb') as f:
                    wait(lambda: json.loads(f.readline()).get('id') == 'eof-ready')
                host = json.loads((eof_root/'host.json').read_text())['host']
                child = next(i for i in H.group_members(host) if i['ppid'] == p.pid)
                known.extend([host, child])
                p.kill(); p.wait(timeout=5)
                wait(lambda: not H.live(H.identity(child['pid'])), seconds=8)
                assert 'childWait' not in json.loads((eof_root/'host.json').read_text())
                result['nativeStdinEOF'] = True
        finally:
            if p.poll() is None: p.kill(); p.wait(timeout=5)
    assert len(CAPTURE)==2
    result.update(ok=True, actualModelRequests=2, minimalLayout=True, preservedHistory=True,
                  webRestartSameP=True, staleRequestRejected=True, regenerate=True, clear=True, delete=True)
finally:
    for process in servers:
        if process.poll() is None: process.terminate(); process.wait(timeout=10)
    if (folder/'host.json').exists(): remember()
    for i in known:
        current = H.identity(i['pid'])
        if H.same(current, i) and H.live(current): H.signal_identity(i, signal.SIGKILL)
    for pid in {i['pid'] for i in known}:
        try: os.waitpid(pid, 0)
        except ChildProcessError: pass
    result['remainingLive'] = [i for i in known if H.same(H.identity(i['pid']), i) and H.live(H.identity(i['pid']))]
    (ROOT/'result.json').write_text(json.dumps(result, indent=2))
    (ROOT/'model-requests.json').write_text(json.dumps(CAPTURE, indent=2))
    model.shutdown()
    assert not result['remainingLive']
assert result['ok']
print('PASS real W HTTP/socket/P/pi: minimal files, restart attach, native identity, regenerate/clear/delete; two localhost model requests')
