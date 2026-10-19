"""Unwired Linux/Darwin ordinary-host candidate, task/w-mac-01a151c5.

Only tests explicitly install this factory. No default module imports this file.
See h2_direct_host.md for the cooperative local-process contract and exclusions.
"""
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
import ctypes
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import signal
import socket
import stat
import struct
import subprocess
import sys
import threading
import time
import uuid
from unittest.mock import patch

from ext.sessiond import proc as P, bridge as B
from ext.sessiond.rpc import api

_EXPECTED = ContextVar('h2_expected_instance', default=None)
_LIBC = ctypes.CDLL(None, use_errno=True)
_PROCESS_TOOLS = None


def configure_process_tools(host_entry):
    """Load the process tools beside the explicitly selected ordinary P.

    One fixed P tool source per web process, never a PATH/platform fallback.
    The caller's manifest pins both host_entry and ordinary_process.py.
    """
    global _PROCESS_TOOLS
    source = Path(host_entry).resolve().with_name('ordinary_process.py')
    if _PROCESS_TOOLS is not None:
        if Path(_PROCESS_TOOLS.__file__).resolve() != source:
            raise RuntimeError('conflicting ordinary process tool source')
        return
    spec = importlib.util.spec_from_file_location('_w_ordinary_process', source)
    tools = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tools)
    tools.boot_id()  # unsupported platform/errors fail before any launch
    _PROCESS_TOOLS = tools
_ORIGINAL_SUPERVISOR = P.Supervisor
_ORIGINAL_BRIDGE = B.Bridge


def atomic(path, obj):
    temp = Path(str(path) + '.tmp')
    with temp.open('w') as f:
        json.dump(obj, f, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)


def identity(pid):
    """Missing is exited; unreadable/malformed is an error, never 'dead'."""
    if _PROCESS_TOOLS is None:
        raise RuntimeError('ordinary process tool source not configured')
    return _PROCESS_TOOLS.identity(pid)


def same(a, b):
    return bool(a and b and all(a[k] == b[k] for k in
                ('pid', 'start', 'boot', 'uid', 'pgid', 'sid')))


def live(i):
    return bool(i and i['state'] not in ('Z', 'X'))


def pidfd_open(pid):
    if not sys.platform.startswith('linux') or platform.machine() not in ('x86_64', 'aarch64'):
        raise RuntimeError('unverified Linux pidfd ABI')
    n = _LIBC.syscall(434, int(pid), 0)
    if n < 0:
        raise OSError(ctypes.get_errno(), 'pidfd_open')
    return n


def pidfd_signal(fd, sig):
    if _LIBC.syscall(424, int(fd), int(sig), 0, 0) < 0:
        raise OSError(ctypes.get_errno(), 'pidfd_send_signal')


def signal_identity(expected, sig):
    if expected['boot'] != _PROCESS_TOOLS.boot_id():
        raise RuntimeError('boot identity mismatch')
    if sys.platform == 'darwin':
        # Explicitly accepted check-then-kill race; not an atomic object handle.
        if not same(identity(expected['pid']), expected):
            raise RuntimeError('process identity mismatch')
        os.kill(expected['pid'], sig)
        return
    fd = pidfd_open(expected['pid'])
    try:
        if not same(identity(expected['pid']), expected):
            raise RuntimeError('process identity mismatch')
        pidfd_signal(fd, sig)
    finally:
        os.close(fd)


def group_members(host):
    return _PROCESS_TOOLS.group_members(host)


def private_dir(path):
    path = Path(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    st = path.lstat()
    if (not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid()
            or st.st_mode & 0o077 or path.resolve() != path):
        raise ValueError('runtime must be a canonical private local directory')
    return path


class Lifecycle:
    """Finite syscall operations, not a server, supervisor or command spool."""
    def __init__(self, session, name, cwd, runtime, host_entry, agentd, env,
                 pi_binary, extensions=(), claim=P._claim_origin, truncate=None,
                 prepare=None, stop_timeout=3):
        configure_process_tools(host_entry)
        self.session = str(Path(session).resolve())
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9._-]+', name) or name in ('.', '..'):
            raise ValueError('ordinary name must remain a legal participant short name')
        self.name, self.cwd = name, str(Path(cwd).resolve())
        self.host_entry, self.agentd = str(host_entry), str(agentd)
        self.env, self.pi_binary, self.extensions = dict(env), pi_binary, list(extensions)
        self.claim = claim
        self.prepare = prepare
        self.truncate = truncate or self._truncate
        self.stop_timeout = stop_timeout
        root = private_dir(runtime)
        self.local = private_dir(root / hashlib.sha256(os.fsencode(self.session)).hexdigest()[:16])
        self.sock = str(self.local / 's')
        if len(os.fsencode(self.sock)) > 100:
            raise ValueError('socket path too long; supply a short stable runtime root')
        self.current = self.local / 'current.json'
        self.handles = {}  # optional true wait only, never required for reconnect
        self.binding = dict(session=self.session, name=name, cwd=self.cwd)
        with self.lock():
            p = self.local / 'binding.json'
            if p.exists():
                if json.loads(p.read_text()) != self.binding:
                    raise ValueError('canonical session binding changed')
            else:
                atomic(p, self.binding)

    @contextmanager
    def lock(self):
        fd = os.open(self.local / 'op.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)  # lock inode is never unlinked

    def load(self):
        if not self.current.exists():
            return None
        d = json.loads(self.current.read_text())
        if d['binding'] != self.binding or d['socket'] != self.sock:
            raise RuntimeError('metadata binding mismatch')
        return d

    def save(self, d):
        path = self.local / d['instance'] / 'record.json'
        if path.exists() and 'hostWait' not in d:
            previous = json.loads(path.read_text())
            if 'hostWait' in previous:
                d['hostWait'] = previous['hostWait']
        atomic(path, d)
        atomic(self.current, d)

    def facts(self, d):
        rows = [json.loads(line) for line in (self.local / d['instance'] / 'stdout').read_text().splitlines()]
        starts = [r for r in rows if r.get('event') == 'host_started']
        if len(starts) != 1:
            raise RuntimeError('missing/duplicate host_started fact')
        f = starts[0]
        h = d['host']
        if (f['hostPid'] != h['pid'] or str(f['hostProcStart']) != h['start']
                or f['sessionFile'] != self.session or f['sessionName'] != self.name
                or f['socketPath'] != self.sock):
            raise RuntimeError('host_started identity mismatch')
        waits = [r for r in rows if r.get('event') == 'child_wait']
        if len(waits) > 1 or (waits and (waits[0]['childPid'] != f['childPid']
                or type(waits[0]['returncode']) is not int)):
            raise RuntimeError('invalid child_wait fact')
        return f, waits[0] if waits else None

    def observe(self, d):
        if d is None:
            return dict(state='idle', instance=None)
        if d.get('host') is None or d.get('child') is None:
            raise RuntimeError('incomplete launch identity; unknown, no automatic retry')
        h, c = identity(d['host']['pid']), identity(d['child']['pid'])
        # PID reuse is not absence; do not signal it or take over its socket.
        for actual, expected in ((h, d['host']), (c, d['child'])):
            if actual and not same(actual, expected):
                raise RuntimeError('stale/reused process identity')
        f, wait = self.facts(d)
        if f['childPid'] != d['child']['pid'] or str(f['childProcStart']) != d['child']['start']:
            raise RuntimeError('child fact identity mismatch')
        handle = self.handles.get(d['instance'])
        if handle is not None and handle.poll() is not None:
            d['hostWait'] = dict(source='W Popen.poll/wait', returncode=handle.wait())
            self.save(d)
        members = group_members(d['host'])
        known = d.get('known', [d['host'], d['child']])
        unknown = [i for i in members if not any(same(i, k) for k in known)]
        known_live = []
        for expected in known:
            actual = identity(expected['pid'])
            if actual and not same(actual, expected):
                raise RuntimeError('known descendant identity/group changed')
            if live(actual):
                known_live.append(actual)
        settled = not members and not known_live and not live(h) and not live(c)
        natural = (settled and wait is not None and d.get('hostWait') is not None
                   and d['hostWait']['returncode'] == (wait['returncode'] if wait['returncode'] >= 0
                                                       else 128 - wait['returncode']))
        state = 'running' if live(h) and live(c) and not unknown else ('dormant' if natural else 'unknown')
        if d.get('fence'):
            state = 'stopped' if settled and d.get('phase') == 'stopped' else 'fenced'
        return dict(state=state, instance=d['instance'], host=d['host'], child=d['child'],
                    socket=self.sock, members=members, knownLive=known_live, unknown=unknown, settled=settled,
                    childWait=wait, hostWait=d.get('hostWait'), phase=d['phase'])

    def status(self):
        with self.lock():
            try:
                return self.observe(self.load())
            except (OSError, ValueError, KeyError, RuntimeError) as e:
                return dict(state='unknown', error=str(e))

    def _reap(self, handle, token):
        # Ordinary child reaping only. Never restart/signal; if web dies this
        # thread dies too and no replacement wait fact is manufactured.
        rc = handle.wait()
        with self.lock():
            path = self.local / token / 'record.json'
            d = json.loads(path.read_text())
            d['hostWait'] = dict(source='W Popen.wait', returncode=rc)
            current = self.load()
            if current and current['instance'] == token:
                self.save(d)
            else:
                atomic(path, d)

    def _start(self):
        previous = self.load()
        if previous and previous.get('host'):
            end = time.monotonic() + 1
            while identity(previous['host']['pid']) is not None and time.monotonic() < end:
                time.sleep(.01)
            if identity(previous['host']['pid']) is not None:
                raise RuntimeError('prior host not reaped; do not launch into H1 live-owner gate')
        if os.path.exists(self.session):
            self.claim(self.session)
            if self.prepare:
                self.prepare()
        # A launch marker precedes the syscall. Interrupted launch is unknown,
        # not a reason to search by name or start a replacement.
        token = uuid.uuid4().hex
        run = private_dir(self.local / token)
        d = dict(instance=token, binding=self.binding, socket=self.sock,
                 phase='launching', host=None, child=None, fence=None)
        self.save(d)
        config = dict(sessionFile=self.session, sessionName=self.name, cwd=self.cwd,
                      piBinary=self.pi_binary, extensionArgv=self.extensions,
                      socketPath=self.sock, env=self.env)
        atomic(run / 'declaration.json', config)
        hostenv = dict(self.env, AGENTD_DIR=self.agentd)
        with (run / 'stdout').open('ab', buffering=0) as out, (run / 'stderr').open('ab', buffering=0) as err:
            handle = subprocess.Popen([sys.executable, '-B', self.host_entry, str(run / 'declaration.json')],
                                      cwd=self.cwd, env=hostenv, stdin=subprocess.DEVNULL,
                                      stdout=out, stderr=err, start_new_session=True, close_fds=True)
        self.handles[token] = handle
        # Start waiting only after record identity is complete; launch failure
        # paths still reap their own Popen via poll/wait, with evidence retained.
        d['host'] = identity(handle.pid)
        if not d['host'] or d['host']['sid'] != handle.pid or d['host']['pgid'] != handle.pid:
            raise RuntimeError('host did not acquire independent session/group')
        self.save(d)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                f, _ = self.facts(d)
                child = identity(f['childPid'])
                if not child or child['start'] != str(f['childProcStart']):
                    raise RuntimeError('child identity unavailable')
                if child['pgid'] != d['host']['pgid'] or child['sid'] != d['host']['sid']:
                    raise RuntimeError('noncooperative child group')
                d.update(child=child, known=[d['host'], child], phase='running')
                self.save(d)
                threading.Thread(target=self._reap, args=(handle, token), daemon=True,
                                 name='h2-own-child-wait').start()
                return d
            except (ValueError, KeyError, RuntimeError):
                if handle.poll() is not None:
                    break
                time.sleep(.01)
        if handle.poll() is not None:
            handle.wait()
        else:
            threading.Thread(target=self._reap, args=(handle, token), daemon=True,
                             name='h2-own-child-wait').start()
        raise RuntimeError('host launch facts unavailable; unknown, no blind retry')

    def ensure(self):
        with self.lock():
            d = self.load()
            if d is None:
                return self._start()
            obs = self.observe(d)
            if d.get('fence'):
                raise RuntimeError('instance fenced; no implicit retry/recovery')
            if obs['state'] == 'running':
                return d
            # Only locally witnessed natural exit may implicitly revive on a
            # new explicit access. Crash/unknown requires recover(expected).
            if obs['state'] == 'dormant':
                return self._start()
            raise RuntimeError('unknown prior instance; explicit checked recovery required')

    @staticmethod
    def _truncate(path):
        with open(path, 'a') as f:
            f.truncate(0)

    def _stop(self, d):
        obs = self.observe(d)
        if obs['settled']:
            return
        if not same(identity(d['host']['pid']), d['host']) or not live(identity(d['host']['pid'])):
            raise RuntimeError('leader lost with residual group; refuse blind signalling')
        frozen = []
        try:
            # Freeze only verified descendants while leader exists. Each new
            # member must have ancestry in the already known cooperative tree.
            known = {i['pid']: i for i in d['known']}
            for _ in range(32):
                members = group_members(d['host'])
                for i in members:
                    if i['uid'] != os.getuid():
                        raise RuntimeError('unknown group owner')
                    if i['pid'] not in known:
                        parent = identity(i['ppid'])
                        if not parent or parent['pid'] not in known or not same(parent, known[parent['pid']]):
                            raise RuntimeError('unknown descendant ancestry')
                        known[i['pid']] = i
                    if not same(i, known[i['pid']]):
                        raise RuntimeError('group identity changed')
                    if not any(same(i, x) for x in frozen):
                        signal_identity(i, signal.SIGSTOP)
                        frozen.append(i)
                after = group_members(d['host'])
                if all(any(same(i, x) for x in frozen) and i['state'] in ('T', 't') for i in after):
                    break
                time.sleep(.01)
            else:
                raise RuntimeError('could not freeze cooperative tree')
            d['known'] = list(known.values())
            self.save(d)
            for i in frozen:
                if live(identity(i['pid'])):
                    signal_identity(i, signal.SIGTERM)
            for i in frozen:
                if live(identity(i['pid'])):
                    signal_identity(i, signal.SIGCONT)
            deadline = time.monotonic() + self.stop_timeout
            while time.monotonic() < deadline:
                obs = self.observe(d)
                if obs['unknown']:
                    raise RuntimeError('new/unknown residual descendants')
                if obs['settled']:
                    return
                time.sleep(.02)
            raise TimeoutError('stop not confirmed; no restart/truncate')
        finally:
            # Restore our exact stopped identities on any failure; no group kill.
            for i in frozen:
                try:
                    if same(identity(i['pid']), i) and live(identity(i['pid'])):
                        signal_identity(i, signal.SIGCONT)
                except ProcessLookupError:
                    pass

    def operate(self, operation, expected):
        with self.lock():
            d = self.load()
            if not d or not expected or d['instance'] != expected:
                raise RuntimeError('instance fence mismatch')
            if d.get('fence'):
                raise RuntimeError('duplicate/failed operation is fenced; do not retry')
            if operation not in ('stop', 'reload', 'clear', 'recover'):
                raise ValueError('unknown lifecycle operation')
            obs = self.observe(d)  # identity gate precedes even recording a fence
            d['fence'], d['phase'] = operation, 'fenced'
            self.save(d)
            if operation == 'recover':
                if not obs['settled']:
                    raise RuntimeError('recovery requires confirmed prior group closure')
            else:
                self._stop(d)
            d['phase'] = 'stopped'
            self.save(d)
            if operation == 'stop':
                return d
            if operation == 'clear':
                self.truncate(self.session)
                self.claim(self.session)  # original ordinary weak claim, no new guarantee
            return self._start()


class DirectSupervisor(_ORIGINAL_SUPERVISOR):
    """Original Bridge-facing API, data socket only; never a SocketSupervisor."""
    def __init__(self, session_path, on_event, cwd=None, profile=None, *, options):
        super().__init__(session_path, on_event, cwd=cwd, profile=profile)
        if profile:
            P.profile_spec(profile)
        env = P.clean_env()
        # Original probe/edit contract: bind the exact canonical JSONL after
        # scrubbing, and explicitly select this candidate's fixed tool source.
        env[P.HOST_MARKER] = self.session_file
        env['W_WORKSPACE'] = P.WS
        env['AGENTD_DIR'] = options['agentd']
        if profile:
            env['DISPATCH_PROFILE'] = profile
        ext = ['-e', P.PROBE_EXT] if os.path.exists(P.PROBE_EXT) else []
        self.life = Lifecycle(self.session_file, self.name, self.cwd,
                              env=env, pi_binary=P.pi_binary(), extensions=ext,
                              prepare=self._sync_header_cwd, **options)
        self._sock = None
        self._reader = None
        self.gen = None  # actual ordinary instance token, NOT S generation

    def _disconnect(self):
        s, self._sock = self._sock, None
        if s is not None:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            s.close()
        if self._reader is not None and self._reader is not threading.current_thread():
            self._reader.join(3)
            if self._reader.is_alive():
                raise TimeoutError('old data reader did not close')
        self._reader = None

    def _connect(self, d):
        self._disconnect()
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.connect(self.life.sock)
            peer, uid, _gid = _PROCESS_TOOLS.peer_credentials(s)
            with self.life.lock():
                now = self.life.load()
                if (now['instance'] != d['instance'] or now.get('fence') or uid != os.getuid()
                        or peer != d['host']['pid'] or not same(identity(peer), d['host'])):
                    raise RuntimeError('socket peer/instance identity mismatch')
            self._sock = s
            self.gen, self.pid = d['instance'], d['host']['pid']
            self.state = 'running'
            self._broadcast(dict(type='sessiond.session_restarted', gen=self.gen,
                                 instance=self.gen, session=self.name, pid=self.pid))
            self._reader = threading.Thread(target=self._read, args=(s, self.gen), daemon=True)
            self._reader.start()
        except BaseException:
            s.close()
            raise

    def _read(self, s, token):
        try:
            with s.makefile('rb') as f:
                for line in f:
                    if len(line) <= P.LINE_LIMIT:
                        self._broadcast(json.loads(line))
        except (OSError, ValueError):
            pass
        finally:
            if self._sock is s and self.gen == token:
                self.state = 'disconnected'  # never infer pi exit from EOF
                self._broadcast(dict(type='sessiond.channel_disconnected', session=self.name,
                                     instance=token, reason='data disconnected; exit unknown'))

    def ensure_started(self):
        with self._op_lock:
            if self._closed:
                return
            try:
                d = self.life.ensure()
                if self._sock is None or self.state != 'running' or self.gen != d['instance']:
                    self._connect(d)
            except (OSError, ValueError, KeyError, RuntimeError) as e:
                self.state = 'unknown'
                self._broadcast(dict(type='sessiond.error', message=str(e)))

    def wait_ready(self, timeout=25):
        return self.state == 'running' and self._sock is not None

    def send(self, obj):
        with self._op_lock, self.life.lock():
            d = self.life.load()
            if (self._closed or self._sock is None or not d or d.get('fence')
                    or d['instance'] != self.gen or self.life.observe(d)['state'] != 'running'):
                return False
            try:
                self._sock.sendall(json.dumps(obj).encode() + b'\n')
                return True  # native RPC receipt still required; never auto replay
            except OSError:
                return False

    def change(self, operation, expected):
        with self._op_lock:
            try:
                # Reject a stale caller before disturbing even its local reader.
                with self.life.lock():
                    current = self.life.load()
                    if not current or not expected or current['instance'] != expected or current.get('fence'):
                        raise RuntimeError('instance fence mismatch or duplicate request')
                # Reader boundary precedes new token / historical mutation.
                self._disconnect()
                d = self.life.operate(operation, expected)
                if operation != 'stop':
                    self._connect(d)
                else:
                    self.state = 'stopped'
                return dict(ok=True, gen=d['instance'], instance=d['instance'], pid=d['host']['pid'])
            except (OSError, ValueError, KeyError, RuntimeError) as e:
                self.state = 'unknown'
                return dict(ok=False, error=str(e))

    def reload(self):
        return self.change('reload', _EXPECTED.get())

    def clear(self):
        return self.change('clear', _EXPECTED.get())

    def shutdown(self, reason=''):
        # Never let original drop_session mistake a detach for a verified kill.
        if reason == 'delete_session':
            return dict(ok=False, error='candidate delete is not migrated; explicit fenced stop required')
        # Browser/web disposal is detach only. Explicit stop is change(stop, token).
        with self._op_lock:
            self._closed = True
            self._disconnect()
            return dict(ok=True, state='detached')

    def status_doc(self):
        obs = self.life.status()
        return dict(session=self.name, session_file=self.session_file, cwd=self.cwd,
                    gen=obs.get('instance'), pid=obs.get('host', {}).get('pid'),
                    mode='ordinary-direct-host', **obs)


class DirectBridge(_ORIGINAL_BRIDGE):
    # Keep each original synchronous query and its captured instance together.
    # The reader never takes this operation lock, so RPC responses can arrive.
    # Existing agent SocketSupervisor is not given ordinary control/locks.
    def get_entries(self, *a, **kw):
        with getattr(self.sup, '_op_lock', nullcontext()):
            return super().get_entries(*a, **kw)

    def request(self, *a, **kw):
        with getattr(self.sup, '_op_lock', nullcontext()):
            return super().request(*a, **kw)

    def get_commands(self, *a, **kw):
        with getattr(self.sup, '_op_lock', nullcontext()):
            return super().get_commands(*a, **kw)

    def inspect(self, *a, **kw):
        with getattr(self.sup, '_op_lock', nullcontext()):
            return super().inspect(*a, **kw)


@contextmanager
def installed(options):
    """Explicit test patch: original Bridge/API/SSE/declaration stay unchanged.

    SocketSupervisor and agentd control routing are deliberately NOT patched.
    """
    def factory(session_path, on_event, cwd=None, profile=None):
        return DirectSupervisor(session_path, on_event, cwd, profile, options=options)
    with patch.object(P, 'Supervisor', factory), patch.object(B, 'Bridge', DirectBridge):
        yield


def api_call(store, *, expected_instance=None, **kwargs):
    """Finite test-only API adapter. Timeout is delivery unknown, not retryable.

    Destructive calls have an explicit captured ordinary instance fence. No
    production route is wired to this wrapper; agentd still uses its real proxy.
    """
    if kwargs.get('op') == 'delete_session':
        return api._j(dict(ok=False, error='delete is outside direct-host candidate scope'),
                      '501 Not Implemented')
    token = _EXPECTED.set(expected_instance)
    try:
        return api.interp(store, **kwargs)
    finally:
        _EXPECTED.reset(token)
