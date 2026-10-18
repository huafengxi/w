"""Unwired ordinary CRUD factory, bllzca. No default consumer imports this.

Original proc CLI helpers / declaration / Bridge / API remain the entry points.
A private flock serializes them across web processes; per-session op locks and
strong host identities remain in h2_direct_host.Lifecycle. See contract .md.
"""
from contextlib import contextmanager, ExitStack
from contextvars import ContextVar
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import threading
import uuid
from unittest.mock import patch

import h2_direct_host as H
from ext.sessiond import proc as P, bridge as B
from ext.sessiond.rpc import api

_EXPECTED_REGISTRATION = ContextVar('h2_registration', default=None)
_CREATE = P.create_cc_session
_LIST = P.list_cc_sessions
_RENAME = P.rename_cc_session
_DELETE_FILES = P.delete_cc_session
_GET_BRIDGE = B.get_bridge
_NEW_NAME = P.new_session_name


class Registry:
    def __init__(self, options):
        self.options = dict(options)
        self.root = H.private_dir(options['runtime'])
        self.mutex = threading.RLock()
        self.local = threading.local()

    @contextmanager
    def lock(self):
        # One fd per outer operation. A second flock fd in a nested original
        # Bridge/CLI call would deadlock against this same process's first fd.
        with self.mutex:
            if getattr(self.local, 'depth', 0):
                self.local.depth += 1
                try:
                    yield
                finally:
                    self.local.depth -= 1
                return
            fd = os.open(self.root / 'registry.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                self.local.depth = 1
                yield
            finally:
                self.local.depth = 0
                os.close(fd)  # stable inode, never unlink

    def files(self, name):
        a, h, base = P.session_files(name)
        # Original realpath-prefix guard alone permits in-directory aliases.
        # CRUD must not mutate another name's declaration/history via aliases.
        for suffix, resolved in (('.agent', a), ('.jsonl', h)):
            lexical = Path(base) / (name + suffix)
            if lexical.is_symlink() or str(lexical) != resolved:
                raise ValueError('CRUD requires exact non-aliased session files')
            if lexical.exists() and not stat.S_ISREG(lexical.lstat().st_mode):
                raise ValueError('session file is not regular')
        return Path(a), Path(h)

    def declaration(self, name):
        a, h = self.files(name)
        try:
            doc = json.loads(a.read_text())
        except OSError as e:
            raise ValueError('registration absent/unreadable: %s' % e) from e
        if (not isinstance(doc, dict) or not isinstance(doc.get('h2Registration'), str)
                or len(doc['h2Registration']) != 32):
            raise ValueError('missing/incomplete candidate registration; unknown ownership')
        prof, pd = P.profile_spec(doc.get('profile'))
        cwd = P.profile_cwd(prof, pd)
        if (doc.get('name') != name or doc.get('cwd') != cwd
                or doc.get('sessionDir') != str(a.parent) or doc.get('host') != P._self_host()):
            raise ValueError('registration binding mismatch')
        return doc, a, h

    def life(self, name, doc, history):
        # Identical configuration to the Supervisor's data path; never launches.
        env = P.clean_env()
        env.update(SESSIOND_SESSION_FILE=str(history), W_WORKSPACE=P.WS,
                   AGENTD_DIR=self.options['agentd'], AGENTD_ROOT=P.WS,
                   DISPATCH_PROFILE=doc['profile'])
        return H.Lifecycle(str(history), name, doc['cwd'], env=env,
                           pi_binary=P.pi_binary(), **self.options)

    def create(self, profile=P.CC_PROFILE):
        with self.lock():
            attempts = 0
            def allocate():
                nonlocal attempts
                attempts += 1
                if attempts > P.SESSION_NAME_TRIES:
                    raise ValueError('cannot allocate a free session name in bounded tries')
                name = _NEW_NAME()
                if not P.SESSION_NAME_OK.fullmatch(name):
                    raise ValueError('generated session name invalid')
                a, h = self.files(name)
                inbox_owner = Path(P.WS) / 'agents/bot' / name
                key = hashlib.sha256(os.fsencode(str(h))).hexdigest()[:16]
                # Preserve orphan histories/inboxes and prior runtime bindings.
                # Return an existing declaration name to the original bounded
                # retry loop, or reserve the generated name via a local reject.
                if (os.path.lexists(h) or os.path.lexists(inbox_owner)
                        or (self.root / key).exists()) and not a.exists():
                    raise NameCollision(name)
                return name
            # Reuse the original allocator's bounded number of attempts and
            # profile/host/cwd/declaration writer. No existing file is overwritten.
            for _ in range(P.SESSION_NAME_TRIES):
                try:
                    with patch.object(P, 'new_session_name', allocate):
                        result = _CREATE(profile)
                    break
                except NameCollision:
                    continue
            else:
                raise ValueError('cannot allocate a free session name (history/inbox/runtime collision)')
            name = result['name']
            a, _ = self.files(name)
            doc = json.loads(a.read_text())
            doc['h2Registration'] = uuid.uuid4().hex
            H.atomic(a, doc)
            a.chmod(0o600)
            # Inbox-only ordinary identity. No spec/pid/enable/watcher/task.
            inbox = Path(P.WS) / 'agents/bot' / name / 'inbox'
            inbox.mkdir(mode=0o700, parents=True, exist_ok=False)
            return dict(result, registration=doc['h2Registration'])

    def rows(self):
        with self.lock():
            # Keep original title precedence, mtime order and row/API/tab shape;
            # explicitly disable the old pi/environ scanner, then replace state.
            with patch.object(P, 'host_map', lambda _: {}):
                rows = _LIST()
            for r in rows:
                r.update(state='unknown', pids=[])
                try:
                    doc, _a, history = self.declaration(r['name'])
                    key = hashlib.sha256(os.fsencode(str(history))).hexdigest()[:16]
                    parent = self.root / key
                    if not parent.exists():
                        r.update(state='dormant')  # registered, never started
                        continue
                    life = self.life(r['name'], doc, history)
                    obs = life.status()
                    state = obs['state']
                    if state == 'dormant' and (obs.get('childWait') or {}).get('returncode') != 0:
                        state = 'failed'
                    if state == 'idle':
                        state = 'unknown'  # incomplete runtime record
                    if state in ('fenced', 'stopped'):
                        state = 'failed'
                    if state not in ('running', 'dormant', 'failed'):
                        state = 'unknown'
                    r.update(state=state, pids=[obs['host']['pid']] if state == 'running' else [])
                except (OSError, ValueError, KeyError, RuntimeError, TypeError):
                    pass  # invalid/unreadable does NOT mean dormant or deletable
            return rows

    def rename(self, name, title):
        with self.lock():
            self.declaration(name)
            return _RENAME(name, title)  # title-only original writer/validation

    def get_bridge(self, entry, store=None):
        with self.lock():
            if isinstance(entry, str) and entry.startswith(P.site_path_of(P.sessions_dir()) + '/'):
                name = api._cc_name(entry)
                self.declaration(name)
            return _GET_BRIDGE(entry, store=store)

    def delete(self, name):
        with self.lock():
            try:
                doc, agent, history = self.declaration(name)
                expected_reg = _EXPECTED_REGISTRATION.get()
                if not expected_reg or expected_reg != doc['h2Registration']:
                    raise ValueError('registration fence mismatch')
                life = self.life(name, doc, history)
                expected = H._EXPECTED.get()
                # No Bridge is constructed here; shutdown/detach is never stop.
                with life.lock():
                    d = life.load()
                    if d is None and expected == 'unstarted' and not history.exists():
                        entries = {p.name for p in life.local.iterdir()}
                        if entries != {'binding.json', 'op.lock'}:
                            raise ValueError('incomplete runtime; unknown ownership')
                        # No process ever launched: declaration fence plus an
                        # explicit unstarted capture, not an invented host token.
                        result = _DELETE_FILES(name)
                        H.atomic(life.local / 'deleted-registration.json', dict(registration=expected_reg))
                        return dict(result, ok=True, process=dict(neverStarted=True), registration=expected_reg,
                                    retained=['inbox', 'runtime facts and stable locks'])
                    if not d or not expected or d['instance'] != expected or d.get('fence'):
                        raise ValueError('instance fence mismatch or duplicate delete')
                    life.observe(d)  # unknown identity must not even write fence
                    before = {str(p): (p.stat().st_dev, p.stat().st_ino) for p in (agent, history)}
                    d.update(fence='delete', phase='fenced')
                    life.save(d)
                    life._stop(d)  # host + pi + known cooperative descendants
                    if not life.observe(d)['settled']:
                        raise RuntimeError('stop not confirmed')
                    for p in (agent, history):
                        if p.is_symlink() or (p.stat().st_dev, p.stat().st_ino) != before[str(p)]:
                            raise RuntimeError('file identity changed during stop')
                    d['phase'] = 'stopped'
                    life.save(d)
                    # Original deletion semantics: only these TWO regular files.
                    # Inbox, runtime facts, locks and unrelated history stay put.
                    result = _DELETE_FILES(name)
                    d['phase'] = 'deleted'
                    life.save(d)  # permanent tombstone denies old object revival
                key = str(history)
                with B._BRIDGES_LOCK:
                    b = B._BRIDGES.pop(key, None)
                if b:
                    b.sup.shutdown()  # detach after verified closure, not proof
                    with b.cond:
                        b.terminated = True
                        b.cond.notify_all()
                return dict(result, ok=True, process=dict(instance=expected, settled=True),
                            registration=expected_reg, retained=['inbox', 'runtime facts and stable locks'])
            except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
                return dict(ok=False, error=str(e))


class NameCollision(ValueError):
    pass


@contextmanager
def installed(options):
    registry = Registry(options)

    class RegisteredSupervisor(H.DirectSupervisor):
        def __init__(self, session_path, on_event, cwd=None, profile=None, *, options):
            super().__init__(session_path, on_event, cwd, profile, options=options)
            self.registration = None
            self.registered = Path(self.session_file).parent == Path(P.sessions_dir())
            if self.registered:
                doc, _a, h = registry.declaration(self.name)
                if str(h) != self.session_file or cwd != doc['cwd'] or profile != doc['profile']:
                    raise ValueError('supervisor registration mismatch')
                self.registration = doc['h2Registration']
                self.life.env['AGENTD_ROOT'] = P.WS

        def check(self):
            if self.registered:
                doc, _a, _h = registry.declaration(self.name)
                if doc['h2Registration'] != self.registration:
                    raise ValueError('stale supervisor registration')
                if self.life.load() is None and (Path(self.session_file).exists() or
                        (self.life.local / 'deleted-registration.json').exists() or
                        {p.name for p in self.life.local.iterdir()} - {'binding.json', 'op.lock'}):
                    raise ValueError('missing instance record with historical/runtime artifacts; unknown')

        def ensure_started(self):
            with registry.lock():
                try:
                    self.check()
                except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
                    self.state = 'unknown'
                    self._broadcast(dict(type='sessiond.error', message=str(e)))
                    return
                return super().ensure_started()

        def send(self, obj):
            with registry.lock():
                try:
                    self.check()
                except (OSError, ValueError, KeyError, RuntimeError, TypeError):
                    return False
                return super().send(obj)

        def change(self, operation, expected):
            with registry.lock():
                try:
                    self.check()
                except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
                    return dict(ok=False, error=str(e))
                return super().change(operation, expected)

    class RegisteredBridge(H.DirectBridge):
        # Uniform registry -> web op -> lifecycle lock order, also for queries
        # that call ensure_started within the original Bridge implementation.
        def get_entries(self, *a, **kw):
            with registry.lock():
                return super().get_entries(*a, **kw)

        def request(self, *a, **kw):
            with registry.lock():
                return super().request(*a, **kw)

        def get_commands(self, *a, **kw):
            with registry.lock():
                return super().get_commands(*a, **kw)

        def inspect(self, *a, **kw):
            with registry.lock():
                return super().inspect(*a, **kw)

        def iter_events(self, *args, **kwargs):
            # Reuse the original SSE wait/keepalive, no additional polling loop.
            # Remote webs see durable delete tombstones even after socket EOF.
            for event in super().iter_events(*args, **kwargs):
                if getattr(self.sup, 'registered', False):
                    d = self.sup.life.load()
                    if d and d.get('phase') == 'deleted':
                        with self.cond:
                            self.terminated = True
                            self.cond.notify_all()
                        return
                yield event

    def factory(session_path, on_event, cwd=None, profile=None):
        return RegisteredSupervisor(session_path, on_event, cwd, profile, options=options)

    with ExitStack() as stack:
        for module, name, value in ((P, 'Supervisor', factory), (B, 'Bridge', RegisteredBridge),
                (P, 'create_cc_session', registry.create), (P, 'list_cc_sessions', registry.rows),
                (P, 'rename_cc_session', registry.rename), (B, 'delete_cc_session', registry.delete),
                (B, 'get_bridge', registry.get_bridge)):
            stack.enter_context(patch.object(module, name, value))
        yield registry


def api_call(store, *, expected_instance=None, expected_registration=None, **kwargs):
    """Explicit fixture-captured server fences, not a default HTTP route change."""
    i = H._EXPECTED.set(expected_instance)
    r = _EXPECTED_REGISTRATION.set(expected_registration)
    try:
        return api.interp(store, **kwargs)
    finally:
        _EXPECTED_REGISTRATION.reset(r)
        H._EXPECTED.reset(i)
