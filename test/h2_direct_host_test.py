#!/usr/bin/env python3
"""Real independent-client fakepi acceptance; retained synthetic roots only."""
import ctypes
import json
import os
from pathlib import Path
import selectors
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1]
BASE = Path(tempfile.mkdtemp(prefix='h24-')).resolve()
for part in ('home', 'tmp', 'cwd', 'workspace', 'runtime'):
    (BASE / part).mkdir(mode=0o700)
os.environ.update(HOME=str(BASE / 'home'), TMPDIR=str(BASE / 'tmp'),
                  W_WORKSPACE=str(BASE / 'workspace'), W_AGENTD_DIR=os.environ['H2_A_SOURCE'])
os.chdir(BASE / 'cwd')
sys.path[:0] = [str(SOURCE), str(SOURCE / 'test')]
from h2_direct_host import (Lifecycle, identity, same, live, signal_identity,
                            atomic, installed, api_call, DirectBridge)
from ext.sessiond import proc as P, bridge as B
from ext.sessiond.rpc import api, stream
from stores.store import RootStore
from stores.dir_store import DirStore

# Adopt only our own orphan fakepi/hosts to obtain real cleanup waits. This is
# harness containment, not product supervision or a fabricated web wait fact.
if sys.platform.startswith('linux'):
    assert ctypes.CDLL(None).prctl(36, 1, 0, 0, 0) == 0
EVIDENCE = BASE / 'wire.jsonl'
print('H2_ROOT=' + str(BASE), flush=True)


def record(**obj):
    with EVIDENCE.open('a') as f:
        f.write(json.dumps(dict(at=time.time(), **obj), sort_keys=True) + '\n')


def wait_for(fn, timeout=8):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = fn()
        if value:
            return value
        time.sleep(.02)
    raise TimeoutError('condition not reached')


def socket_rpc(s, command):
    # Startup now contributes a real get_state response to the ordinary ring.
    # Match this request, rather than mistaking replayed success for its receipt.
    request_id = 'fixture-' + uuid.uuid4().hex
    s.settimeout(5)
    s.sendall(json.dumps(dict(type=command, id=request_id)).encode() + b'\n')
    with s.makefile('rb') as reader:
        for line in reader:
            response = json.loads(line)
            if response.get('type') == 'response' and response.get('id') == request_id:
                assert response.get('success'), response
                return response
    raise RuntimeError('socket closed before matching command response')


class Client:
    def __init__(self, fixture):
        self.f = fixture
        err = (fixture.root / ('web-%d.stderr' % len(fixture.clients))).open('wb')
        self.p = subprocess.Popen([sys.executable, '-B', str(SOURCE / 'test/h2_direct_host_client.py'),
                                   str(fixture.options_path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=err, cwd=BASE / 'cwd', env=fixture.web_env())
        err.close()
        fixture.clients.append(self)
        record(webStart=self.p.pid, env=fixture.web_env(), cwd=str(BASE / 'cwd'))

    def request(self, **req):
        self.p.stdin.write(json.dumps(req).encode() + b'\n')
        self.p.stdin.flush()
        with selectors.DefaultSelector() as sel:
            sel.register(self.p.stdout, selectors.EVENT_READ)
            if not sel.select(12):
                raise TimeoutError('web response timeout')
        line = self.p.stdout.readline()
        if not line:
            raise RuntimeError('web died, see ' + str(self.f.root))
        obj = json.loads(line)
        record(web=self.p.pid, request=req, response=obj)
        assert obj['ok'], obj
        return obj['result']

    def close(self, kill=False):
        if self.p.poll() is None:
            if kill:
                self.p.kill()  # exact harness-owned web Popen only
            else:
                self.p.stdin.close()
            self.p.wait(timeout=10)
        for s in (self.p.stdin, self.p.stdout):
            if not s.closed:
                s.close()
        record(webEnd=self.p.pid, returncode=self.p.returncode)


class Fixture:
    def __init__(self, key):
        self.root = BASE / key
        self.root.mkdir()
        self.ws = self.root / 'ws'
        self.ws.mkdir()
        (self.ws / 'chat-a.agent').write_text(json.dumps(dict(host='test', cwd=str(self.ws), title='visible title')))
        # I0=A address is inbox-only, not an agent registration or receiver PASS.
        (self.ws / 'agents/bot/chat-a/inbox').mkdir(parents=True)
        self.hostmap = self.root / 'host-id'
        self.hostmap.write_text(socket.gethostname() + ' test\n')
        self.fake = self.root / 'fakepi'
        # Absolute existing interpreter; not /usr/bin/env and no real pi fallback.
        self.fake.write_text('#!' + sys.executable + '\nimport sys; sys.path.insert(0, ' +
                             repr(os.environ['H2_P_SOURCE']) + ')\n' +
                             (SOURCE / 'test/h2_direct_host_fakepi.py').read_text())
        self.fake.chmod(0o700)
        self.options = dict(runtime=str(BASE / 'runtime'), host_entry=str(Path(os.environ['H2_P_SOURCE']) / 'pi_host.py'),
                            agentd=os.environ['H2_A_SOURCE'], stop_timeout=.25)
        self.options_path = self.root / 'options.json'
        atomic(self.options_path, self.options)
        self.clients = []
        env = self.web_env()
        self.life = Lifecycle(str(self.ws / 'chat-a.jsonl'), 'chat-a', str(self.ws),
                              env=env, pi_binary=str(self.fake), **self.options)
        self.known = []

    def web_env(self):
        return dict(HOME=str(BASE / 'home'), TMPDIR=str(BASE / 'tmp'), PATH='/usr/bin:/bin',
                    W_WORKSPACE=str(self.ws), W_AGENTD_DIR=os.environ['H2_A_SOURCE'],
                    W_HOST_ID_FILE=str(self.hostmap), W_SESSION_PI_BIN=str(self.fake),
                    AGENTD_ROOT=str(self.ws), PYTHONDONTWRITEBYTECODE='1')

    def remember(self):
        d = self.life.load()
        if d:
            self.known.extend(i for i in d.get('known', []) if i)
            for k in ('host', 'child'):
                if d.get(k):
                    self.known.append(d[k])
        desc = self.ws / 'descendant.json'
        if desc.exists():
            i = identity(json.loads(desc.read_text())['pid'])
            if i:
                self.known.append(i)

    def cleanup(self):
        self.remember()
        for c in self.clients:
            c.close()
        # No process-name scan/group kill. Exact retained identities only.
        for i in self.known:
            try:
                if same(identity(i['pid']), i) and live(identity(i['pid'])):
                    signal_identity(i, signal.SIGKILL)
            except ProcessLookupError:
                pass
        for i in self.known:
            try:
                os.waitpid(i['pid'], 0)
            except ChildProcessError:
                pass
        record(cleanup='harness exact identity/pidfd only', known=self.known,
               live=[i for i in self.known if same(identity(i['pid']), i) and live(identity(i['pid']))])


class Acceptance(unittest.TestCase):
    def setUp(self):
        self.f = Fixture(self.id().split('.')[-1])

    def tearDown(self):
        self.f.cleanup()

    def start(self):
        d = self.f.life.ensure()
        self.f.remember()
        wait_for(lambda: (self.f.ws / 'chat-a.jsonl').exists())
        record(start=d, observed=self.f.life.status())
        return d

    def test_A_real_web_death_reconnect(self):
        c1 = Client(self.f)
        a = c1.request(op='attach')['body']
        self.assertTrue(a['ok'], a)
        token = a['gen']
        effect = c1.request(special='rpc', command=dict(type='effect', value='one-only'))
        self.assertTrue(effect['ok'])
        before = effect['data']
        self.f.remember()
        c1.request(special='sse_disconnect')
        c1.close(kill=True)
        self.assertTrue(live(identity(self.f.life.load()['host']['pid'])))
        # A different declaration basename resolves through a session symlink;
        # canonical name/inbox must not follow the alias or presentation title.
        (self.f.ws / 'alias.agent').write_text(json.dumps(dict(host='test', cwd=str(self.f.ws), title='New presentation')))
        (self.f.ws / 'alias.jsonl').symlink_to(self.f.ws / 'chat-a.jsonl')
        (self.f.ws / 'chat-a.agent').write_text(json.dumps(dict(host='test', cwd=str(self.f.ws), title='Renamed title')))
        c2 = Client(self.f)
        alias = c2.request(op='attach', session='/alias.agent')['body']
        self.assertTrue(alias['ok'])
        self.assertEqual(alias['gen'], token)
        a2 = c2.request(op='attach')['body']
        self.assertTrue(a2['ok'], a2)
        self.assertEqual(a2['gen'], token)
        ident = c2.request(special='identity')
        self.assertEqual(ident['handles'], [])  # no inherited Popen map
        after = c2.request(special='rpc', command=dict(type='identity'))['data']
        self.assertEqual(before, after)  # PID/start/name/all stdio inodes
        self.assertEqual((self.f.ws / 'effects').read_text(), 'one-only\n')
        self.assertTrue(any(x.get('id') == 'one-only' for x in a2['entries']))
        self.assertEqual(after['name'], 'chat-a')
        self.assertTrue((self.f.ws / 'agents/bot/chat-a/inbox').is_dir())
        self.assertFalse((self.f.ws / 'agents/bot/alias').exists())
        c2.request(special='sse_disconnect')
        self.assertEqual(c2.request(special='rpc', command=dict(type='identity'))['data'], after)
        self.assertNotEqual(c1.p.pid, c2.p.pid)
        record(A=dict(first=before, second=after, firstInstance=token, secondInstance=a2['gen'],
                      web1=c1.p.pid, web2=c2.p.pid, history=a2, imports=ident['imports']))
        # In normal deployment init reaps this orphan. Our subreaper harness
        # instead performs a real wait of this exact adopted P; W2 receives no
        # fabricated hostWait and still has no Popen handle for it.
        import threading
        host = self.f.life.load()['host']
        waits = []
        def reap_orphan():
            waits.append(os.waitpid(host['pid'], 0))
        reaper = None
        if sys.platform.startswith('linux'):
            reaper = threading.Thread(target=reap_orphan, daemon=True)
            reaper.start()
        # Darwin init reaps the orphan; the harness cannot claim an adoptive wait.
        reload_result = c2.request(op='reload', expected_instance=token)
        self.assertTrue(reload_result['body']['ok'], reload_result)
        if reaper is not None:
            reaper.join(3)
            self.assertFalse(reaper.is_alive())
        self.f.remember()
        self.assertNotEqual(reload_result['body']['gen'], token)
        self.assertEqual((self.f.ws / 'effects').read_text(), 'one-only\n')
        self.assertTrue(c2.request(op='attach')['body']['ok'])
        record(reconnectedReload=reload_result, harnessOnlyOrphanWait=waits)

    def test_B_pump_replacement_and_multiple_webs(self):
        # Two independent Webs attach in order. Cross-process simultaneous
        # startup uniqueness is deliberately outside the no-file-lock contract.
        c1, c2 = Client(self.f), Client(self.f)
        rs = [c1.request(op='status'), c2.request(op='status')]
        ids = [r['body']['instance'] for r in rs]
        self.assertIsNotNone(ids[0])
        self.assertEqual(ids[0], ids[1])
        d = self.f.life.load()
        self.f.remember()
        # One actual single-client socket; detach both web readers then issue a
        # real flood command and close, leaving no client to drain pi PIPE.
        c1.close(); c2.close()
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(self.f.life.sock)
        s.sendall(b'{"type":"flood","waitForGate":true}\n')
        wait_for(lambda: (self.f.ws / 'flood.armed').exists())
        s.close()
        (self.f.ws / 'flood.go').write_text('client already closed')
        wait_for(lambda: (self.f.ws / 'flood.done').exists())
        record(noClientFlood=dict(lines=7000, payloadBytesPerLine=4096,
                                  gate='socket closed before flood.go', marker=str(self.f.ws / 'flood.done')))
        c3 = Client(self.f)
        self.assertTrue(c3.request(op='attach')['body']['ok'])
        self.assertEqual(c3.request(op='status')['body']['instance'], d['instance'])
        # An already-live fixed endpoint still refuses a sequential second P.
        # Alternate socket names for one session are no longer supported.
        config = json.loads((self.f.life.local / d['instance'] / 'declaration.json').read_text())
        alias = self.f.ws / 'alias.jsonl'
        alias.symlink_to(self.f.ws / 'chat-a.jsonl')
        for n, (session, sock) in enumerate(((config['sessionFile'], config['socketPath']),
                 (str(alias), config['socketPath']))):
            cfg = dict(config, sessionFile=session, socketPath=sock)
            p = self.f.root / ('duplicate%d.json' % n)
            atomic(p, cfg)
            r = subprocess.run([sys.executable, '-B', self.f.options['host_entry'], str(p)],
                               env=dict(self.f.web_env(), AGENTD_DIR=os.environ['H2_A_SOURCE']),
                               cwd=BASE / 'cwd', capture_output=True, timeout=10)
            record(duplicateConfig=cfg, rc=r.returncode, stdout=r.stdout.decode(), stderr=r.stderr.decode())
            self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.f.life.load()['host'], d['host'])

    def test_B_unknown_endpoint_and_binding(self):
        unknown = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        unknown.bind(self.f.life.sock)
        ino = os.lstat(self.f.life.sock).st_ino
        try:
            with self.assertRaises(RuntimeError):
                self.start()
            self.assertEqual(os.lstat(self.f.life.sock).st_ino, ino)
            with self.assertRaises(RuntimeError):
                self.f.life.ensure()  # incomplete launch never blindly repeated
        finally:
            unknown.close()
        with self.assertRaises(ValueError):
            Lifecycle(self.f.life.session, 'changed-title-as-name', str(self.f.ws),
                      env=self.f.web_env(), pi_binary=str(self.f.fake), **self.f.options)

    def test_C_natural_normal_nonzero_and_explicit_recovery(self):
        for code in (0, 7):
            d = self.start()
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(self.f.life.sock)
            s.sendall(json.dumps(dict(type='exit', code=code)).encode() + b'\n')
            wait_for(lambda: (self.f.ws / 'exit.requested').exists())
            s.close()
            obs = wait_for(lambda: (o if (o := self.f.life.status()).get('settled') else None))
            self.assertEqual(obs['state'], 'dormant', obs)
            self.assertEqual(obs['childWait']['returncode'], code)
            self.assertEqual(obs['hostWait']['returncode'], code)
            record(natural=obs)
            new = self.f.life.operate('recover', d['instance'])
            self.f.remember()
            self.assertNotEqual(new['instance'], d['instance'])
            self.f.life.operate('stop', new['instance'])
            # Explicit stopped instances are fenced: use a separate fixture for
            # the second exit code rather than erasing evidence to retry it.
            if code == 0:
                self.f.cleanup()
                self.f = Fixture(self.id().split('.')[-1] + '-nonzero')

    def test_C_crash_residual_unknown_no_blind_kill(self):
        d = self.start()
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(self.f.life.sock)
        socket_rpc(s, 'hold_eof'); s.close()
        signal_identity(d['host'], signal.SIGKILL)
        wait_for(lambda: not live(identity(d['host']['pid'])))
        obs = self.f.life.status()
        self.assertEqual(obs['state'], 'unknown')
        self.assertIsNone(obs['childWait'])
        with self.assertRaises(RuntimeError):
            self.f.life.ensure()
        with self.assertRaises(RuntimeError):
            self.f.life.operate('reload', d['instance'])
        self.assertTrue(live(identity(d['child']['pid'])))
        self.assertEqual(self.f.life.load()['instance'], d['instance'])
        record(crash=obs, residualChild=identity(d['child']['pid']))

    def test_C_unknown_descendant_and_checked_crash_recovery(self):
        d = self.start()
        history = (self.f.ws / 'chat-a.jsonl').read_bytes()
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(self.f.life.sock)
        socket_rpc(s, 'descendant'); s.close()
        self.f.remember()
        descendant = identity(json.loads((self.f.ws / 'descendant.json').read_text())['pid'])
        signal_identity(d['host'], signal.SIGKILL)
        wait_for(lambda: not live(identity(d['host']['pid'])) and not live(identity(d['child']['pid'])))
        obs = self.f.life.status()
        self.assertEqual(obs['state'], 'unknown')
        self.assertTrue(obs['unknown'])
        self.assertTrue(live(identity(descendant['pid'])))
        with self.assertRaises(RuntimeError):
            self.f.life.ensure()
        # Independent harness owns this exact synthetic descendant; product
        # does not kill it or pretend to wait for pi after leader loss.
        signal_identity(descendant, signal.SIGKILL)
        if sys.platform.startswith('linux'):
            os.waitpid(descendant['pid'], 0)
        else:
            wait_for(lambda: not live(identity(descendant['pid'])))
        self.assertTrue(self.f.life.status()['settled'])
        self.assertIsNone(self.f.life.status()['childWait'])
        new = self.f.life.operate('recover', d['instance'])
        self.f.remember()
        self.assertNotEqual(new['instance'], d['instance'])
        self.assertEqual((self.f.ws / 'chat-a.jsonl').read_bytes(), history)
        record(unknownResidual=obs, harnessCollected=descendant, recovered=new)

    def test_C_stale_unreadable_stop_timeout(self):
        d = self.start()
        stale = json.loads(json.dumps(d))
        stale['host']['start'] = '0'
        self.f.life.save(stale)
        with self.assertRaises(RuntimeError):
            self.f.life.operate('clear', d['instance'])
        self.assertTrue(live(identity(d['host']['pid'])))
        self.f.life.save(d)
        with patch('h2_direct_host.identity', side_effect=PermissionError('injected proc denial')):
            self.assertEqual(self.f.life.status()['state'], 'unknown')
            with self.assertRaises(PermissionError):
                self.f.life.operate('reload', d['instance'])
        self.assertTrue(live(identity(d['child']['pid'])))
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(self.f.life.sock)
        socket_rpc(s, 'ignore_term'); s.close()
        before = (self.f.ws / 'chat-a.jsonl').read_bytes()
        with self.assertRaises(TimeoutError):
            self.f.life.operate('clear', d['instance'])
        self.assertEqual((self.f.ws / 'chat-a.jsonl').read_bytes(), before)
        self.assertTrue(live(identity(d['child']['pid'])))
        self.assertEqual(self.f.life.load()['phase'], 'fenced')
        record(timeout=self.f.life.status(), injection='stale start and PermissionError are explicit injections')

    def test_C_known_descendants_stop_and_missing_wait(self):
        d = self.start()
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(self.f.life.sock)
        socket_rpc(s, 'descendant'); s.close()
        self.f.remember()
        self.f.life.operate('stop', d['instance'])
        obs = self.f.life.status()
        self.assertTrue(obs['settled'], obs)
        self.assertEqual(obs['state'], 'stopped')
        self.assertIsNone(obs['childWait'])  # signals do not invent P wait facts
        self.assertEqual(len(self.f.life.load()['known']), 3)
        record(cooperativeStop=obs, known=self.f.life.load()['known'])

    def test_D_running_reload_clear_late_duplicate_and_failure(self):
        c = Client(self.f)
        first = c.request(op='attach')['body']
        self.assertTrue(first['ok'])
        c.request(special='rpc', command=dict(type='effect', value='history-before-reload'))
        old = first['gen']
        history = (self.f.ws / 'chat-a.jsonl').read_bytes()
        r = c.request(op='reload', expected_instance=old)
        self.assertTrue(r['body']['ok'], r)
        new = r['body']['gen']; self.f.remember()
        self.assertNotEqual(old, new)
        self.assertEqual((self.f.ws / 'chat-a.jsonl').read_bytes(), history)
        stale = c.request(op='clear', expected_instance=old)
        self.assertFalse(stale['body']['ok'])
        self.assertEqual((self.f.ws / 'chat-a.jsonl').read_bytes(), history)
        self.assertEqual(self.f.life.load()['instance'], new)
        missing = c.request(op='reload')
        self.assertFalse(missing['body']['ok'])
        cleared = c.request(op='clear', expected_instance=new)
        self.assertTrue(cleared['body']['ok'], cleared)
        latest = cleared['body']['gen']; self.f.remember()
        entries = c.request(op='attach')['body']['entries']
        self.assertFalse(any(e.get('id') == 'history-before-reload' for e in entries))
        duplicate = c.request(op='clear', expected_instance=new)
        self.assertFalse(duplicate['body']['ok'])
        self.assertEqual(self.f.life.load()['instance'], latest)
        self.assertEqual((self.f.ws / 'effects').read_text(), 'history-before-reload\n')

    def test_D_late_old_token_rejected_after_reload(self):
        d = self.start()
        c1, c2 = Client(self.f), Client(self.f)
        results = [c1.request(op='reload', expected_instance=d['instance']),
                   c2.request(op='reload', expected_instance=d['instance'])]
        self.assertEqual(sum(r['body']['ok'] for r in results), 1, results)
        self.assertNotEqual(self.f.life.load()['instance'], d['instance'])
        self.f.remember()
        record(lateReload=results)

    def test_D_truncate_claim_failure_fenced_not_success(self):
        d = self.start()
        before = (self.f.ws / 'chat-a.jsonl').read_bytes()
        def fail(_):
            raise PermissionError('injected truncate failure')
        self.f.life.truncate = fail
        with self.assertRaises(PermissionError):
            self.f.life.operate('clear', d['instance'])
        self.assertEqual((self.f.ws / 'chat-a.jsonl').read_bytes(), before)
        self.assertTrue(self.f.life.status()['settled'])
        self.assertEqual(self.f.life.load()['phase'], 'stopped')
        with self.assertRaises(RuntimeError):
            self.f.life.operate('clear', d['instance'])
        with self.assertRaises(RuntimeError):
            self.f.life.ensure()
        record(truncateFailure=self.f.life.status(), history=before.decode())
        # A separate real fixture for raising claim. Original _claim_origin may
        # swallow chown errors; that weakness is not upgraded by this adapter.
        self.f.cleanup()
        self.f = Fixture(self.id().split('.')[-1] + '-claim')
        d = self.start()
        self.f.life.claim = fail
        with self.assertRaises(PermissionError):
            self.f.life.operate('clear', d['instance'])
        self.assertEqual((self.f.ws / 'chat-a.jsonl').stat().st_size, 0)
        self.assertEqual(self.f.life.load()['instance'], d['instance'])
        record(claimFailure=self.f.life.status(), injection='raising callable, NOT stronger original claim')

    def test_D_original_api_failure_result_and_profile_validation(self):
        with patch.dict(os.environ, self.f.web_env()), installed(self.f.options), patch.object(P, 'WS', str(self.f.ws)), patch.object(P, 'WS_REAL', str(self.f.ws)), patch.object(B, '_BRIDGES', {}):
            store = RootStore([('/', DirStore(str(self.f.ws)))])
            profiles = self.f.ws / 'bots/profiles'
            profiles.mkdir(parents=True)
            (profiles / 'bad.json').write_text(json.dumps(dict(form='task')))
            (profiles / 'good.json').write_text(json.dumps(dict(form='interactive', caps=[])))
            declaration = self.f.ws / 'chat-a.agent'
            for profile in ('bad', 'missing', '../escape'):
                declaration.write_text(json.dumps(dict(host='test', cwd=str(self.f.ws), profile=profile)))
                meta, body = api_call(store, op='attach', session='/chat-a.agent')
                self.assertTrue(meta['http_status'].startswith('400'), (meta, body))
                self.assertFalse(json.loads(body)['ok'])
                self.assertIsNone(self.f.life.load())
            declaration.write_text(json.dumps(dict(host='test', cwd=str(self.f.ws), profile='good', title='Title B')))
            b = B.get_bridge('/chat-a.agent', store=store)
            self.assertTrue(b.get_entries()['ok'])
            self.f.remember()
            d = self.f.life.load()
            cfg = json.loads((self.f.life.local / d['instance'] / 'declaration.json').read_text())
            self.assertEqual(cfg['env']['DISPATCH_PROFILE'], 'good')
            self.assertEqual(cfg['sessionName'], 'chat-a')
            self.assertEqual(cfg['env']['SESSIOND_SESSION_FILE'], str(self.f.ws / 'chat-a.jsonl'))
            self.assertEqual(cfg['env']['W_WORKSPACE'], str(self.f.ws))
            self.assertEqual(cfg['env']['AGENTD_DIR'], self.f.options['agentd'])
            self.assertTrue((Path(cfg['env']['AGENTD_ROOT']) / 'agents/bot/chat-a/inbox').is_dir())
            self.assertFalse((self.f.ws / 'agents/bot/chat-a/spec.json').exists())
            before = (self.f.ws / 'chat-a.jsonl').read_bytes()
            def fail(_):
                raise PermissionError('injected truncate denial for original HTTP result')
            b.sup.life.truncate = fail
            meta, body = api_call(store, op='clear', session='/chat-a.agent', expected_instance=d['instance'])
            raw = json.loads(body)
            self.assertEqual(meta['http_status'], '502 Bad Gateway')
            self.assertFalse(raw['ok'])
            self.assertIn('injected truncate denial', raw['error'])
            self.assertEqual((self.f.ws / 'chat-a.jsonl').read_bytes(), before)
            self.assertEqual(self.f.life.load()['instance'], d['instance'])
            record(originalApiFailure=dict(meta=meta, body=raw), config=cfg)
            b.sup.shutdown()

    def test_E_original_validation_agent_route_sse_gap(self):
        with installed(self.f.options), patch.object(B, 'get_bridge', side_effect=AssertionError('must not open')):
            meta, body = api_call(None, op='delete_session', session='/chat-a.agent', confirm=1)
            self.assertEqual(meta['http_status'], '501 Not Implemented')
            self.assertFalse(json.loads(body)['ok'])
            for cmd in ('{', '{}', '{"type":"switch_session"}', '{"type":"set_session_name"}'):
                meta, body = api_call(None, op='cmd', session='/chat-a.agent', cmd=cmd)
                self.assertFalse(json.loads(body)['ok'])
        # Original SocketSupervisor type and genuine agent control proxy branch
        # remain intact; mock receipt proves routing, not a real agent control.
        with installed(self.f.options):
            agent = B.Bridge('/chat-a.jsonl', sock_path='/not-connected', participant_id='task/test')
            self.assertIsInstance(agent.sup, P.SocketSupervisor)
            with patch.object(B, 'get_bridge', return_value=agent), patch.object(api, '_agentd_control_proxy', return_value=('proxy', 'real-branch')) as proxy:
                self.assertEqual(api_call(None, op='reload', session='/agent'), ('proxy', 'real-branch'))
                proxy.assert_called_once()
        # Real Bridge/SSE iterator with a minimal event ring; no second SSE stack.
        with patch.dict(os.environ, self.f.web_env()), installed(self.f.options), patch.object(P, 'WS', str(self.f.ws)), patch.object(P, 'WS_REAL', str(self.f.ws)):
            store = RootStore([('/', DirStore(str(self.f.ws)))])
            with patch.object(B, '_BRIDGES', {}):
                b = B.get_bridge('/chat-a.agent', store=store)
                self.assertTrue(b.get_entries()['ok'])
                self.f.remember()
                from collections import deque
                b.ring = deque(maxlen=2)
                for n in range(4): b._append(dict(n=n))
                seq, gap = b.resolve_since('missing-namespace')
                self.assertTrue(gap)
                _, it = stream.interp(store, session='/chat-a.agent')
                next(it); it.close()
                self.assertEqual(b.subscribers, 0)
                b.sup.shutdown()


if __name__ == '__main__':
    unittest.main(verbosity=2)
