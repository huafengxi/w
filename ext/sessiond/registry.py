"""Ordinary session registration, fenced CRUD and explicit client assembly.

File operations live in sessions, process operations in lifecycle.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import os
from pathlib import Path
import stat
import threading
import uuid
from ext.sessiond import lifecycle as H
from ext.sessiond.client import Client
from ext.sessiond import sessions as P, bridge as B

_EXPECTED_REGISTRATION = ContextVar('sessiond_registration', default=None)


def imported_history(life):
    """Explicit offline import evidence, valid only before any new launch record.

    Deployment writes this after stopping the old owner. It is not a lease,
    reservation, fake P identity or automatic recovery of a lost current record.
    """
    marker = life.local / 'imported-history.json'
    try:
        if marker.is_symlink() or not marker.is_file():
            return False
        if {p.name for p in life.local.iterdir()} - {'binding.json', 'imported-history.json'}:
            return False
        doc = json.loads(marker.read_text())
        st = Path(life.session).stat()
        return (doc['binding'] == life.binding and doc['history'] ==
                [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns])
    except (OSError, ValueError, KeyError, TypeError):
        return False


class Registry:
    def __init__(self, options):
        self.options = dict(options)
        self.root = Path(options['runtime'])
        if not self.root.is_absolute() or self.root.resolve() != self.root:
            raise ValueError('runtime must be an absolute canonical directory')
        if self.root.exists():
            H.private_dir(self.root)
        self.mutex = threading.RLock()
        self.registered = True

    @contextmanager
    def lock(self):
        # Protect process-local adapters/threads, not different Web processes.
        with self.mutex:
            yield

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
            raise ValueError('missing/incomplete session registration; unknown ownership')
        # The token is published only AFTER inbox creation succeeds. Validate
        # the completed identity on every resolution and cached client
        # check; never mkdir here or silently repair a partial registration.
        inbox = Path(P.WS) / 'agents/bot' / name / 'inbox'
        try:
            st = inbox.lstat()
        except OSError as e:
            raise ValueError('session inbox absent/unreadable; unknown ownership: %s' % e) from e
        if (not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid()
                or inbox.resolve() != inbox):
            raise ValueError('missing/incomplete session inbox; unknown ownership')
        prof, pd = P.profile_spec(doc.get('profile'))
        cwd = P.profile_cwd(prof, pd)
        if (doc.get('name') != name or doc.get('cwd') != cwd
                or doc.get('sessionDir') != str(a.parent) or doc.get('host') != P._self_host()):
            raise ValueError('registration binding mismatch')
        return doc, a, h

    def life(self, name, doc, history):
        # Identical configuration to the Client's data path; never launches.
        env = P.clean_env()
        env.update(SESSIOND_SESSION_FILE=str(history), W_WORKSPACE=P.WS,
                   AGENTD_DIR=self.options['agentd'], AGENTD_ROOT=P.WS,
                   DISPATCH_PROFILE=doc['profile'])
        return H.Lifecycle(str(history), name, doc['cwd'], env=env,
                           pi_binary=P.pi_binary(), **self.options)

    def create(self, profile=P.CC_PROFILE):
        with self.lock():
            H.private_dir(self.root)
            attempts = 0
            def allocate():
                nonlocal attempts
                attempts += 1
                if attempts > P.SESSION_NAME_TRIES:
                    raise ValueError('cannot allocate a free session name in bounded tries')
                name = P.new_session_name()
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
                    result = P.create_files(profile, name_factory=allocate)
                    break
                except NameCollision:
                    continue
                except ValueError as e:
                    if isinstance(e.__cause__, FileExistsError):
                        continue  # another creator published this name first
                    raise
            else:
                raise ValueError('cannot allocate a free session name (history/inbox/runtime collision)')
            name = result['name']
            a, _ = self.files(name)
            doc = json.loads(a.read_text())
            a.chmod(0o600)
            # Inbox-only ordinary identity. No spec/pid/enable/watcher/task.
            inbox = Path(P.WS) / 'agents/bot' / name / 'inbox'
            inbox.mkdir(mode=0o700, parents=True, exist_ok=False)
            # Completion fence, not a transaction: any preceding failure leaves
            # the original declaration unknown and unusable by startup paths.
            doc['h2Registration'] = uuid.uuid4().hex
            H.atomic(a, doc)
            return dict(result, registration=doc['h2Registration'])

    def rows(self):
        with self.lock():
            # Keep original title precedence, mtime order and row/API/tab shape;
            # explicitly disable the old pi/environ scanner, then replace state.
            rows = P.list_files()
            for r in rows:
                r.update(state='unknown', pids=[])
                try:
                    doc, _a, history = self.declaration(r['name'])
                    key = hashlib.sha256(os.fsencode(str(history))).hexdigest()[:16]
                    parent = self.root / key
                    if not parent.exists():
                        # Missing runtime facts alone do not prove never-started.
                        # Existing history (even empty/aliased) is contrary evidence;
                        # do not scan pi processes or infer a safely dead host.
                        if not os.path.lexists(history):
                            r.update(state='dormant')  # complete, no history/runtime
                        continue
                    if not (parent / 'binding.json').is_file():
                        continue  # listing must not manufacture missing runtime bindings
                    life = self.life(r['name'], doc, history)
                    if life.load() is None and imported_history(life):
                        r.update(state='dormant')
                        continue
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
            return P.rename_files(name, title)  # title-only original writer/validation

    def get_bridge(self, entry, store=None):
        with self.lock():
            if self.registered and isinstance(entry, str) and entry.startswith(P.site_path_of(P.sessions_dir()) + '/'):
                name = P.session_name_from_site(entry)
                self.declaration(name)
            with B._BRIDGES_LOCK:
                return B._get_bridge_locked(entry, store, client_factory=self.client_factory,
                                            bridge_factory=self.bridge_factory)

    def delete(self, name):
        with self.lock():
            try:
                doc, agent, history = self.declaration(name)
                expected_reg = _EXPECTED_REGISTRATION.get()
                if expected_reg is None:
                    expected_reg = doc['h2Registration']
                if not expected_reg or expected_reg != doc['h2Registration']:
                    raise ValueError('registration fence mismatch')
                life = self.life(name, doc, history)
                expected = H._EXPECTED.get()
                # No Bridge is constructed here; shutdown/detach is never stop.
                with life.lock():
                    d = life.load()
                    if expected is None:
                        expected = d['instance'] if d else 'unstarted'
                    if d is None and expected == 'unstarted' and (not history.exists() or imported_history(life)):
                        entries = {p.name for p in life.local.iterdir()}
                        if entries - {'binding.json', 'op.lock', 'imported-history.json'}:
                            raise ValueError('incomplete runtime; unknown ownership')
                        # No process ever launched: declaration fence plus an
                        # explicit unstarted capture, not an invented host token.
                        result = P.delete_files(name)
                        H.atomic(life.local / 'deleted-registration.json', dict(registration=expected_reg))
                        return dict(result, ok=True, process=dict(neverStarted=True), registration=expected_reg,
                                    retained=['inbox', 'runtime facts'])
                    if not d or not expected or d['instance'] != expected or d.get('fence'):
                        raise ValueError('instance fence mismatch or duplicate delete')
                    life.observe(d)  # unknown identity must not even write fence
                    def file_identity(p):
                        try:
                            st = p.lstat()
                        except FileNotFoundError:
                            return None
                        if p.is_symlink() or not p.is_file():
                            raise ValueError('not a regular deletion target')
                        return st.st_dev, st.st_ino
                    # Native pi defers JSONL persistence until its first assistant
                    # response. A started, empty session may legitimately lack it.
                    before = {str(p): file_identity(p) for p in (agent, history)}
                    if before[str(agent)] is None:
                        raise ValueError('registration disappeared before stop')
                    d.update(fence='delete', phase='fenced')
                    life.save(d)
                    life._stop(d)  # host + pi + known cooperative descendants
                    if not life.observe(d)['settled']:
                        raise RuntimeError('stop not confirmed')
                    for p in (agent, history):
                        if file_identity(p) != before[str(p)]:
                            raise RuntimeError('file identity changed during stop')
                    d['phase'] = 'stopped'
                    life.save(d)
                    # Original deletion semantics: only these TWO regular files.
                    # Inbox, runtime facts, locks and unrelated history stay put.
                    result = P.delete_files(name)
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
                            registration=expected_reg, retained=['inbox', 'runtime facts'])
            except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
                return dict(ok=False, error=str(e))


class NameCollision(ValueError):
    pass


def build(options, *, registered=True):
    registry = Registry(options)
    registry.registered = registered

    class RegisteredClient(Client):
        def __init__(self, session_path, on_event, cwd=None, profile=None, *, options):
            super().__init__(session_path, on_event, cwd, profile, options=options)
            self.registration = None
            self.registered = Path(self.session_file).parent == Path(P.sessions_dir())
            if self.registered:
                doc, _a, h = registry.declaration(self.name)
                if str(h) != self.session_file or cwd != doc['cwd'] or profile != doc['profile']:
                    raise ValueError('client registration mismatch')
                self.registration = doc['h2Registration']
                self.life.env['AGENTD_ROOT'] = P.WS

        def check(self):
            if self.registered:
                doc, _a, _h = registry.declaration(self.name)
                if doc['h2Registration'] != self.registration:
                    raise ValueError('stale client registration')
                if self.life.load() is None and not imported_history(self.life) and (
                        Path(self.session_file).exists() or
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
                    requested = _EXPECTED_REGISTRATION.get()
                    if requested is not None and (not self.registered or requested != self.registration):
                        raise ValueError('registration fence mismatch')
                    self.check()
                except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
                    return dict(ok=False, error=str(e))
                return super().change(operation, expected)

    class RegisteredBridge(B.OrdinaryBridge):
        # These are only process-local thread locks, including original queries
        # that call ensure_started. No cross-Web atomicity is promised.
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

        def stream_closed(self):
            if getattr(self.sup, 'registered', False):
                d = self.sup.life.load()
                if d and d.get('phase') == 'deleted':
                    with self.cond:
                        self.terminated = True
                        self.cond.notify_all()
            return super().stream_closed()

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
        return RegisteredClient(session_path, on_event, cwd, profile, options=options)

    registry.client_factory = factory if registered else (
            lambda session_path, on_event, cwd=None, profile=None:
                Client(session_path, on_event, cwd, profile, options=options))
    registry.bridge_factory = RegisteredBridge if registered else B.OrdinaryBridge
    return registry
