"""Finite, identity-checked lifecycle operations for independent pi hosts.

Owns local instance facts, not pi PIPEs, agent scheduling or browser transport.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import ctypes
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

from ext.sessiond import sessions as P

_EXPECTED = ContextVar('sessiond_instance', default=None)
_LIBC = ctypes.CDLL(None, use_errno=True)
_PROCESS_TOOLS = None
_SESSION_MUTEXES = {}  # process-local thread serialization, not file locks


def configure_process_tools(host_entry):
    """Load the process tools beside the explicitly selected ordinary P.

    One fixed P tool source per web process, never a PATH/platform fallback.
    The caller's manifest pins both host_entry and local_process.py.
    """
    global _PROCESS_TOOLS
    source = Path(host_entry).resolve().with_name('local_process.py')
    if _PROCESS_TOOLS is not None:
        if Path(_PROCESS_TOOLS.__file__).resolve() != source:
            raise RuntimeError('conflicting ordinary process tool source')
        return
    spec = importlib.util.spec_from_file_location('_w_local_process', source)
    tools = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tools)
    tools.boot_id()  # unsupported platform/errors fail before any launch
    _PROCESS_TOOLS = tools


def atomic(path, obj):
    temp = Path(str(path) + '.tmp-' + uuid.uuid4().hex)
    try:
        with temp.open('x') as f:
            os.chmod(temp, 0o600)
            json.dump(obj, f, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


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
        actual = identity(expected['pid'])
        if actual is None:
            return  # already gone; absence is not a reused identity
        if not same(actual, expected):
            raise RuntimeError('process identity mismatch')
        try:
            os.kill(expected['pid'], sig)
        except ProcessLookupError:
            pass  # exited after the check; stop observation still confirms closure
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


def unverified_members(members, known, host):
    """Read-only ancestry check for live tool subprocesses, not stop authority.

    Group membership alone is insufficient. Every accepted descendant must
    retain its sampled identity and a currently verified parent chain.
    Nothing is persisted or added to the set of identities we may signal.
    """
    verified = {i['pid']: i for i in known}
    pending = [i for i in members if not any(same(i, k) for k in known)]
    while pending:
        remaining = []
        for member in pending:
            current = identity(member['pid'])
            if not live(current):  # exited after the group snapshot
                continue
            parent = verified.get(current['ppid'])
            actual_parent = identity(parent['pid']) if parent else None
            if (same(current, member) and parent and live(actual_parent)
                    and same(actual_parent, parent)
                    and all(current[k] == host[k] for k in ('boot', 'uid', 'pgid', 'sid'))):
                verified[current['pid']] = current
            else:
                remaining.append(member)
        if len(remaining) == len(pending):
            return remaining
        pending = remaining
    return []


def private_dir(path):
    path = Path(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    st = path.lstat()
    if (not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid()
            or st.st_mode & 0o077 or path.resolve() != path):
        raise ValueError('runtime must be a canonical private local directory')
    return path


class Lifecycle:
    """Finite operations: P owns host.json, Web owns .agent/runtime.

    No pre-spawn marker, random generation, per-run directory or command spool.
    Missing launch evidence has the explicitly accepted crash-window ambiguity.
    """
    def __init__(self, session, name, cwd, runtime, host_entry, agentd, env,
                 pi_binary, extensions=(), claim=P._claim_origin, truncate=None,
                 prepare=None, stop_timeout=3, startup_timeout=5, declaration_path=None):
        configure_process_tools(host_entry)
        self.session = str(Path(session).resolve())
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9._-]+', name) or name in ('.', '..'):
            raise ValueError('ordinary name must remain a legal participant short name')
        if name != P.display_name(self.session):
            raise ValueError('name must match the canonical history basename')
        self.name, self.cwd = name, str(Path(cwd).resolve())
        self.host_entry, self.agentd = str(host_entry), str(agentd)
        self.env, self.pi_binary, self.extensions = dict(env), pi_binary, list(extensions)
        self.claim, self.prepare = claim, prepare
        self.truncate = truncate or self._truncate
        self.stop_timeout, self.startup_timeout = stop_timeout, startup_timeout
        history = Path(self.session)
        if history.parent.parent == Path(P.sessions_dir()).resolve() and history.parent.name == name:
            self.local = private_dir(history.parent)
        else:
            # Arbitrary declarations/aliases still converge by CANONICAL history,
            # never by the spelling or parent of an alias .agent.
            self.local = private_dir(private_dir(runtime) / hashlib.sha256(os.fsencode(self.session)).hexdigest()[:16])
        self._mutex = _SESSION_MUTEXES.setdefault(str(self.local), threading.RLock())
        self.sock = str(self.local / 's')
        if len(os.fsencode(self.sock)) > 100:
            raise ValueError('socket path too long; supply a short private session/runtime root')
        self.current = self.local / 'host.json'
        self.declaration = Path(declaration_path).resolve() if declaration_path else history.with_suffix('.agent')
        if not P._within_roots(str(self.declaration)):
            raise ValueError('control declaration escapes workspace')
        self.handles = {}
        self.binding = dict(session=self.session, name=name, cwd=self.cwd)

    @staticmethod
    def process_key(host):
        # Existing gen/expected_instance API carries native identity, not a UUID.
        return '%s:%s:%s' % (host['boot'], host['pid'], host['start'])

    @contextmanager
    def lock(self):
        with self._mutex:
            yield

    def web_record(self):
        doc = json.loads(self.declaration.read_text())
        if not isinstance(doc, dict):
            raise ValueError('invalid session declaration')
        return doc

    def read_host(self):
        if not self.current.exists():
            return None
        d = json.loads(self.current.read_text())
        if not isinstance(d, dict) or d.get('binding') != self.binding:
            raise RuntimeError('host state binding mismatch')
        if not isinstance(d.get('host'), dict):
            raise RuntimeError('missing host identity')
        wait = d.get('childWait')
        if wait is not None and (not isinstance(wait, dict) or not d.get('child')
                                 or type(wait.get('returncode')) is not int):
            raise RuntimeError('invalid actual child wait')
        return d

    def load(self):
        d = self.read_host()
        if d is None:
            return None
        d['instance'] = self.process_key(d['host'])
        d['socket'] = self.sock
        d['known'] = [i for i in (d['host'], d.get('child')) if i]
        d['phase'], d['fence'] = 'unverified', None
        control = self.web_record().get('runtime', {})
        if not isinstance(control, dict):
            raise ValueError('invalid Web control record')
        if control.get('host'):
            if not same(control['host'], d['host']):
                raise RuntimeError('Web control/actual host identity mismatch')
            d.update(control)
        return d

    def save(self, d):
        # Only this caller writes .agent. Never rewrite P's host.json or a prior
        # instance archive. Preserve concurrently read presentation/config data.
        doc = self.web_record()
        control = {k: d[k] for k in ('host', 'phase', 'fence', 'known',
                   'startupAcceptance', 'hostWait', 'error', 'startupCleanup') if k in d}
        old = doc.get('runtime', {})
        if (d.get('host') and old.get('host') and same(old['host'], d['host'])
                and 'hostWait' not in control and 'hostWait' in old):
            control['hostWait'] = old['hostWait']
        doc['runtime'] = control
        atomic(self.declaration, doc)

    def observe(self, d):
        if d is None:
            return dict(state='idle', instance=None)
        if d.get('host') is None or d.get('child') is None:
            raise RuntimeError('incomplete process identity; unknown, no blind takeover')
        actual_record = self.read_host()
        if (not actual_record or not same(actual_record['host'], d['host'])
                or not same(actual_record.get('child'), d['child'])):
            raise RuntimeError('host state changed during observation')
        wait = d['childWait'] = actual_record.get('childWait')
        h, c = identity(d['host']['pid']), identity(d['child']['pid'])
        for actual, expected in ((h, d['host']), (c, d['child'])):
            if actual and not same(actual, expected):
                raise RuntimeError('stale/reused process identity')
        handle = self.handles.get(d['instance'])
        if handle is not None and handle.poll() is not None:
            d['hostWait'] = dict(source='W Popen.poll/wait', returncode=handle.wait())
            self.save(d)
        members = group_members(d['host'])
        known = d['known']
        unknown = (unverified_members(members, known, d['host']) if live(h) and live(c)
                   else [i for i in members if not any(same(i, k) for k in known)])
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
        state = ('running' if d['phase'] == 'running' and live(h) and live(c) and not unknown
                 else ('dormant' if natural else 'unknown'))
        if d.get('fence'):
            state = 'stopped' if settled and d.get('phase') == 'stopped' else 'fenced'
        return dict(state=state, instance=d['instance'], host=d['host'], child=d['child'],
                    socket=self.sock, members=members, knownLive=known_live, unknown=unknown,
                    settled=settled, childWait=wait, hostWait=d.get('hostWait'), phase=d['phase'])

    def status(self):
        with self.lock():
            try:
                return self.observe(self.load())
            except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
                return dict(state='unknown', error=str(e))

    def _reap(self, handle, host):
        rc = handle.wait()
        with self.lock():
            try:
                actual = self.read_host()
                if not actual or not same(actual['host'], host):
                    return  # a later P owns the state; no old callback write
                current = self.load()
                if current and same(current['host'], host):
                    current['hostWait'] = dict(source='W Popen.wait', returncode=rc)
                    self.save(current)
            except (FileNotFoundError, RuntimeError):
                pass  # missing/changed declaration: never recreate or repair it

    def _accept_start(self, d):
        """One post-launch connection: our P's kernel peer AND a real pi RPC.

        Not pre-launch arbitration, a command replay, or a uniqueness promise.
        Never send even the probe to a socket owned by another host.
        """
        request_id = 'w-start-' + uuid.uuid4().hex
        deadline = time.monotonic() + self.startup_timeout
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(self.startup_timeout)
            s.connect(self.sock)
            peer, uid, _gid = _PROCESS_TOOLS.peer_credentials(s)
            if (peer != d['host']['pid'] or uid != os.getuid()
                    or not same(identity(peer), d['host'])):
                raise RuntimeError('startup socket peer is not the launched host')
            s.sendall(json.dumps(dict(type='get_state', id=request_id)).encode() + b'\n')
            with s.makefile('rb') as stream:
                while time.monotonic() < deadline:
                    s.settimeout(max(.001, deadline - time.monotonic()))
                    line = stream.readline(P.LINE_LIMIT + 1)
                    if not line:
                        raise RuntimeError('startup socket EOF before get_state response')
                    if len(line) > P.LINE_LIMIT:
                        raise RuntimeError('startup socket response exceeds line limit')
                    response = json.loads(line)
                    if response.get('type') != 'response' or response.get('id') != request_id:
                        continue
                    if response.get('command') != 'get_state' or response.get('success') is not True:
                        raise RuntimeError('startup get_state rejected or invalid response')
                    return dict(peerPid=peer, requestId=request_id, command='get_state', success=True)
        raise TimeoutError('startup get_state response timeout')

    def _reject_start(self, d, handle, error):
        # Only identities belonging to THIS Popen, never the foreign socket peer.
        try:
            actual = self.read_host()
            if d.get('host') and actual and same(actual['host'], d['host']):
                d['child'] = actual.get('child')
        except (OSError, ValueError, KeyError, RuntimeError):
            pass
        known = [i for i in (d.get('child'), d.get('host')) if i]
        errors = []
        for sig in (signal.SIGTERM, signal.SIGKILL):
            for i in known:
                try:
                    if same(identity(i['pid']), i) and live(identity(i['pid'])):
                        signal_identity(i, sig)
                except (OSError, RuntimeError) as e:
                    errors.append(str(e))
            try:
                d['hostWait'] = dict(source='W failed-start Popen.wait',
                                     returncode=handle.wait(timeout=max(.1, self.stop_timeout)))
            except subprocess.TimeoutExpired:
                continue
        remaining = [i for i in known if same(identity(i['pid']), i) and live(identity(i['pid']))]
        d.update(phase='startup_failed', fence='startup_failed', error=str(error),
                 startupCleanup=dict(remainingLive=remaining, errors=errors,
                                     childIdentityUnknown=d.get('child') is None))
        self.save(d)  # an ACTUAL failed attempt, never a pre-Popen prediction

    def _start(self):
        if os.path.exists(self.session):
            self.claim(self.session)
            if self.prepare:
                self.prepare()
        config = dict(sessionFile=self.session, sessionName=self.name, cwd=self.cwd,
                      piBinary=self.pi_binary, extensionArgv=self.extensions,
                      socketPath=self.sock, env=self.env)
        # No persistent launch marker, random instance, config file or per-run
        # directory. A crash before actual identity publication may need manual
        # investigation. The log is diagnostic and is never a startup marker.
        with (self.local / 'host.log').open('ab', buffering=0) as log:
            handle = subprocess.Popen([sys.executable, '-B', self.host_entry, '-'],
                                      cwd=self.cwd, env=dict(self.env), stdin=subprocess.PIPE,
                                      stdout=log, stderr=log, start_new_session=True, close_fds=True)
        d = dict(binding=self.binding, socket=self.sock, host=None, child=None,
                 phase='unverified', fence=None)
        try:
            d['host'] = identity(handle.pid)
            if not d['host'] or d['host']['sid'] != handle.pid or d['host']['pgid'] != handle.pid:
                raise RuntimeError('host did not acquire independent session/group')
            d['instance'] = self.process_key(d['host'])
            self.handles[d['instance']] = handle
            try:
                handle.stdin.write(json.dumps(config).encode())
                handle.stdin.close()
            finally:
                if not handle.stdin.closed:
                    handle.stdin.close()
            deadline = time.monotonic() + self.startup_timeout
            while time.monotonic() < deadline:
                actual = self.read_host()
                if actual and same(actual['host'], d['host']) and actual.get('child'):
                    child = identity(actual['child']['pid'])
                    if not same(child, actual['child']):
                        raise RuntimeError('child identity unavailable/changed')
                    if child['pgid'] != d['host']['pgid'] or child['sid'] != d['host']['sid']:
                        raise RuntimeError('noncooperative child group')
                    d.update(child=child, childWait=actual.get('childWait'), known=[d['host'], child])
                    break
                if handle.poll() is not None:
                    raise RuntimeError('host exited before actual process facts')
                time.sleep(.01)
            else:
                raise TimeoutError('host process facts unavailable')
            d['startupAcceptance'] = self._accept_start(d)
            d['phase'] = 'running'
            self.save(d)
        except Exception as e:
            self._reject_start(d, handle, e)
            raise RuntimeError('host startup acceptance failed: %s' % e) from e
        threading.Thread(target=self._reap, args=(handle, d['host']), daemon=True,
                         name='sessiond-child-wait').start()
        return d

    def ensure(self):
        with self.lock():
            d = self.load()
            if d is None:
                if self.web_record().get('runtime'):
                    raise RuntimeError('missing actual host record; unknown, explicit recovery required')
                return self._start()
            obs = self.observe(d)
            if d.get('fence'):
                raise RuntimeError('failed operation; no implicit retry/recovery')
            if obs['state'] == 'running':
                return d
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
                raise RuntimeError('process identity mismatch')
            if d.get('fence'):
                raise RuntimeError('duplicate/failed operation; do not retry')
            if operation not in ('stop', 'reload', 'clear', 'recover'):
                raise ValueError('unknown lifecycle operation')
            obs = self.observe(d)
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
                self.claim(self.session)
            return self._start()
