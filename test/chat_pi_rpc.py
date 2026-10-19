"""Real pi RPC/extension contract test; caller supplies PI_TEST_BIN (>=0.84.4).
All writable state and the deterministic HTTP model live in a fresh temporary root.
No real credentials, global config, model requests or sessiond sessions are used.
Temporary evidence is retained, not recursively deleted.
"""
import base64
import http.server
import json
import os
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
import time
import uuid

ROOT = Path(tempfile.mkdtemp(prefix="chat-pi-rpc-"))
CONFIG = ROOT / "config"
CONFIG.mkdir()
CAPTURE = []
REQUESTED = threading.Event()
RELEASE = threading.Event()
RELEASE.set()


class Model(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        CAPTURE.append(body)
        REQUESTED.set()
        RELEASE.wait(15)
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        for delta, finish in [({'role': 'assistant', 'content': 'fixture answer'}, None), ({}, 'stop')]:
            payload = {'id': 'fixture', 'object': 'chat.completion.chunk', 'created': 1,
                       'model': 'fixture', 'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}
            self.wfile.write(('data: ' + json.dumps(payload) + '\n\n').encode())
        self.wfile.write(b'data: [DONE]\n\n')
        self.wfile.flush()


server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Model)
threading.Thread(target=server.serve_forever, daemon=True).start()
(CONFIG / 'models.json').write_text(json.dumps({'providers': {'fixture': {
    'baseUrl': 'http://127.0.0.1:%d/v1' % server.server_port,
    'apiKey': 'fixture-not-a-secret', 'api': 'openai-completions',
    'models': [{'id': 'fixture', 'name': 'fixture', 'reasoning': False, 'input': ['text', 'image'],
                'contextWindow': 100000, 'maxTokens': 1024, 'cost': {'input': 0, 'output': 0, 'cacheRead': 0, 'cacheWrite': 0}}]
}}}))
(CONFIG / 'settings.json').write_text(json.dumps({'retry': {'enabled': False}, 'compaction': {'enabled': False}}))
session = ROOT / 'registered.jsonl'
probe = Path(__file__).resolve().parents[1] / 'ext/sessiond/probe.ts'
cancel_ext = ROOT / 'cancel.ts'
cancel_ext.write_text('''export default function(pi:any) {
 let cancel=false;
 const registered=process.env.SESSIOND_SESSION_FILE;
 pi.registerCommand("fixture-wrong-path", {handler: async()=>{process.env.SESSIOND_SESSION_FILE=registered+".wrong";}});
 pi.registerCommand("fixture-restore-path", {handler: async()=>{process.env.SESSIOND_SESSION_FILE=registered;}});
 pi.registerCommand("fixture-cancel-tree", {handler: async()=>{cancel=true;}});
 pi.on("session_before_tree", ()=>{if(cancel){cancel=false;return {cancel:true};}});
}''')
# Valid PNG chunk checksums: newer pi image decoding rejects a corrupt IDAT CRC.
PIXEL = {'type': 'image', 'data': 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII=', 'mimeType': 'image/png'}
env = dict(os.environ, PI_CODING_AGENT_DIR=str(CONFIG), W_WORKSPACE=str(ROOT),
           SESSIOND_SESSION_FILE=str(session), PI_OFFLINE='1', NO_PROXY='127.0.0.1,localhost')
for k in list(env):
    if k.startswith(('AGENT_', 'AGENTD_', 'DISPATCH_')):
        env.pop(k)
argv = [os.environ['PI_TEST_BIN'], '--mode', 'rpc', '--session', str(session),
        '--provider', 'fixture', '--model', 'fixture', '--no-skills', '--no-prompt-templates',
        '-e', str(probe), '-e', str(cancel_ext)]


class Pi:
    def __init__(self):
        self.out = queue.Queue()
        self.events = []
        self.err = open(ROOT / ('stderr-' + uuid.uuid4().hex + '.log'), 'w')
        self.proc = subprocess.Popen(argv, cwd=ROOT, env=env, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=self.err, text=True)
        def reader():
            for line in self.proc.stdout:
                try:
                    obj = json.loads(line)
                    self.events.append(obj)
                    self.out.put(obj)
                except ValueError:
                    pass
        threading.Thread(target=reader, daemon=True).start()

    def wait(self, predicate, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                obj = self.out.get(timeout=max(.01, deadline-time.monotonic()))
            except queue.Empty:
                break
            if predicate(obj):
                return obj
        raise AssertionError('pi timeout; evidence: %s, exit=%s' % (ROOT, self.proc.poll()))

    def cmd(self, kind, **kw):
        rid = uuid.uuid4().hex
        self.proc.stdin.write(json.dumps(dict(kw, type=kind, id=rid)) + '\n')
        self.proc.stdin.flush()
        obj = self.wait(lambda e: e.get('type') == 'response' and e.get('id') == rid)
        assert obj.get('success'), obj
        return obj.get('data')

    def chat(self, action, **kw):
        nonce = uuid.uuid4().hex
        args = base64.b64encode(json.dumps(dict(kw, action=action, nonce=nonce)).encode()).decode()
        self.cmd('prompt', message='/sessiond-chat ' + args)
        return json.loads((ROOT / 'run/sessiond-inspect' / (nonce + '.json')).read_text())

    def settled(self):
        self.wait(lambda e: e.get('type') == 'agent_settled')

    def close(self):
        self.proc.terminate()
        self.proc.wait(timeout=15)
        self.err.close()


pi = Pi()
try:
    commands = pi.cmd('get_commands')['commands']
    assert any(c['name'] == 'sessiond-chat' for c in commands)
    assert pi.chat('state')['state'] == 'ready'
    pi.cmd('prompt', message='before edit', images=[PIXEL])
    pi.settled()
    first = pi.cmd('get_entries')
    first_user = next(e for e in first['entries'] if e['type'] == 'message' and e['message']['role'] == 'user')
    pi.cmd('prompt', message='old continuation MUST NOT LEAK')
    pi.settled()
    old = pi.cmd('get_entries')
    old_bytes = session.read_bytes()
    assert pi.chat('state')['state'] == 'success'
    pi.cmd('prompt', message='/fixture-wrong-path')
    mismatch = pi.chat('regenerate', entryId=first_user['id'], message='must not rebind registration', images=[])
    assert not mismatch['ok'] and 'path differs' in mismatch['error'], mismatch
    assert pi.cmd('get_entries') == old
    pi.cmd('prompt', message='/fixture-restore-path')
    print('PASS real registration mismatch guard: no history/file rebinding')
    pi.cmd('prompt', message='/fixture-cancel-tree')
    cancelled = pi.chat('regenerate', entryId=first_user['id'], message='must not submit when cancelled', images=[])
    assert not cancelled['ok'] and 'cancelled' in cancelled['error'], cancelled
    assert pi.cmd('get_entries') == old, 'cancelled navigation changed history'
    print('PASS real extension cancellation: no navigation or submission')
    result = pi.chat('regenerate', entryId=first_user['id'], message='edited first message', images=[PIXEL])
    assert result['ok'], result
    assert result['sessionFile'] == str(session)
    pi.settled()
    after = pi.cmd('get_entries')
    by_id = {e['id']: e for e in after['entries']}
    branch = []
    leaf = after['leafId']
    while leaf:
        branch.append(by_id[leaf])
        leaf = by_id[leaf]['parentId']
    assert first_user['id'] not in {e['id'] for e in branch}
    assert all(by_id[e['id']] == e for e in old['entries']), 'old branch was modified'
    assert session.read_bytes().startswith(old_bytes), 'JSONL must be append-only'
    assert 'old continuation MUST NOT LEAK' not in json.dumps(CAPTURE[-1]['messages'])
    assert 'before edit' not in json.dumps(CAPTURE[-1]['messages'])
    assert 'edited first message' in json.dumps(CAPTURE[-1]['messages'])
    assert 'image_url' in json.dumps(CAPTURE[-1]['messages']), 'historical image missing'
    assert pi.cmd('get_state')['sessionFile'] == str(session)
    print('PASS real same-file navigation: old branches intact, new model context excludes old suffix')

    # Hold a real model call while exercising the real queues and busy guard.
    RELEASE.clear()
    REQUESTED.clear()
    pi.cmd('prompt', message='held active turn')
    assert REQUESTED.wait(10)
    assert pi.chat('state')['state'] == 'busy'
    assert not pi.chat('regenerate', entryId=first_user['id'], message='must reject while busy', images=[])['ok']
    image = {'type': 'image', 'data': 'AAAA', 'mimeType': 'image/png'}
    pi.cmd('steer', message='steer one', images=[image])
    pi.cmd('steer', message='steer two')
    pi.cmd('follow_up', message='follow one', images=[image])
    cleared = pi.cmd('clear_queue')
    assert cleared == {'steering': ['steer one', 'steer two'], 'followUp': ['follow one']}, cleared
    assert pi.cmd('clear_queue') == {'steering': [], 'followUp': []}
    assert pi.cmd('get_state')['isStreaming'] is True, 'clear_queue must not abort'
    RELEASE.set()
    pi.settled()
    assert not any('steer one' in json.dumps(r['messages']) or 'follow one' in json.dumps(r['messages']) for r in CAPTURE)
    print('PASS real clear_queue: returned texts/order, cleared both queues/images, did not abort or deliver withdrawn messages')
    persisted = pi.cmd('get_entries')
    pi.close()
    pi = Pi()
    loaded = pi.cmd('get_entries')
    assert loaded == persisted, 'restart lost active branch / entries'
    assert pi.cmd('get_state')['sessionFile'] == str(session)
    assert pi.chat('state')['state'] == 'success'
    print('PASS reconnect/restart: same registered path, persisted branch and outcome')
    pi.close()
    # A separate synthetic, user-only persisted fixture exercises pi's current-leaf
    # navigateTree no-op without ever truncating/replacing a production session.
    user_only = ROOT / 'user-only.jsonl'
    header = json.loads(session.read_text().splitlines()[0])
    prefix = first['entries'][:first['entries'].index(first_user)+1]
    user_only.write_text(''.join(json.dumps(e)+'\n' for e in [header]+prefix))
    argv[argv.index('--session')+1] = str(user_only)
    env['SESSIOND_SESSION_FILE'] = str(user_only)
    pi = Pi()
    assert pi.cmd('get_entries')['leafId'] == first_user['id']
    regenerated = pi.chat('regenerate', entryId=first_user['id'], message='regenerated incomplete user leaf', images=[])
    assert regenerated['ok'], regenerated
    pi.settled()
    assert pi.cmd('get_state')['sessionFile'] == str(user_only)
    assert 'before edit' not in json.dumps(CAPTURE[-1]['messages'])
    assert 'regenerated incomplete user leaf' in json.dumps(CAPTURE[-1]['messages'])
    print('PASS real current-user-leaf regeneration via public non-context breadcrumb')
    assert not [e for e in pi.events if e.get('type') == 'extension_error'], pi.events
    print('ALL PASS; evidence retained:', ROOT)
finally:
    RELEASE.set()
    pi.close()
    server.shutdown()
    (ROOT / 'captured-model-requests.json').write_text(json.dumps(CAPTURE, indent=2))
