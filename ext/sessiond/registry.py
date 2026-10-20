"""Ordinary session CRUD and explicit client assembly.

Web owns host.json; declarations contain configuration and controls stay in memory. There
are no per-generation archives, import markers or separate deletion tombstones.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat
import threading
from ext.sessiond import lifecycle as H
from ext.sessiond.client import Client
from ext.sessiond import sessions as P, bridge as B

class Registry:
    def __init__(self, options):
        self.options = dict(options)
        self.root = Path(options['runtime'])  # short fallback for external histories
        if not self.root.is_absolute() or self.root.resolve() != self.root:
            raise ValueError('runtime must be an absolute canonical directory')
        if self.root.exists():
            H.private_dir(self.root)
        self.mutex = threading.RLock()
        self.registered = True

    @contextmanager
    def lock(self):
        # Thread serialization, not cross-Web arbitration.
        with self.mutex:
            yield

    def files(self, name):
        a, h, base = P.session_files(name)
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
            raise ValueError('registration absent/unreadable; flat legacy sessions require offline migration') from e
        if not isinstance(doc, dict):
            raise ValueError('declaration must be an object')
        inbox = Path(P.WS) / 'agents/bot' / name / 'inbox'
        try:
            st = inbox.lstat()
        except OSError as e:
            raise ValueError('session inbox absent/unreadable; unknown ownership') from e
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
        env = P.clean_env()
        env.update(SESSIOND_SESSION_FILE=str(history), W_WORKSPACE=P.WS,
                   AGENTD_ROOT=P.WS, DISPATCH_PROFILE=doc['profile'])
        return H.Lifecycle(str(history), name, doc['cwd'], env=env,
                           pi_binary=P.pi_binary(),
                           extensions=['-e', P.PI_EXTENSION] if os.path.exists(P.PI_EXTENSION) else [],
                           prepare=lambda: P.sync_header_cwd(str(history), doc['cwd']), **self.options)

    def create(self, profile=P.CC_PROFILE):
        with self.lock():
            attempts = 0
            def allocate():
                nonlocal attempts
                attempts += 1
                if attempts > P.SESSION_NAME_TRIES:
                    raise ValueError('cannot allocate a free session name in bounded tries')
                name = P.new_session_name()
                if not P.SESSION_NAME_OK.fullmatch(name):
                    raise ValueError('generated session name invalid')
                # Inbox contents reserve a retired name; never swallow messages
                # or reuse another registration's receiver identity.
                if os.path.lexists(Path(P.WS) / 'agents/bot' / name):
                    raise NameCollision(name)
                return name
            for _ in range(P.SESSION_NAME_TRIES):
                try:
                    result = P.create_files(profile, name_factory=allocate)
                    break
                except NameCollision:
                    continue
            else:
                raise ValueError('cannot allocate a free session name')
            name = result['name']
            inbox = Path(P.WS) / 'agents/bot' / name / 'inbox'
            inbox.mkdir(mode=0o700, parents=True, exist_ok=False)
            return result

    def rows(self):
        with self.lock():
            rows = P.list_files()
            for r in rows:
                r.update(state='unknown', pids=[])
                try:
                    doc, _a, history = self.declaration(r['name'])
                    life = self.life(r['name'], doc, history)
                    if not life.current.exists():
                        if not os.path.lexists(history) and not life.control and not os.path.lexists(life.sock):
                            r.update(state='dormant')
                        continue
                    obs = life.status()
                    state = obs['state']
                    if state == 'dormant' and (obs.get('hostWait') or {}).get('returncode') != 0:
                        state = 'failed'
                    if state in ('fenced', 'stopped'):
                        state = 'failed'
                    if state not in ('running', 'dormant', 'failed'):
                        state = 'unknown'
                    r.update(state=state, pids=[obs['host']['pid']] if state == 'running' else [])
                except (OSError, ValueError, KeyError, RuntimeError, TypeError):
                    pass
            return rows

    def rename(self, name, title):
        with self.lock():
            self.declaration(name)
            return P.rename_files(name, title)

    def get_bridge(self, entry, store=None):
        with self.lock():
            if self.registered and isinstance(entry, str) and entry.startswith(P.site_path_of(P.sessions_dir()) + '/'):
                self.declaration(P.session_name_from_site(entry))
            with B._BRIDGES_LOCK:
                return B._get_bridge_locked(entry, store, client_factory=self.client_factory,
                                            bridge_factory=self.bridge_factory)

    def delete(self, name):
        with self.lock():
            try:
                doc, agent, history = self.declaration(name)
                life = self.life(name, doc, history)
                with life.lock():
                    d = life.load()
                    expected = H._EXPECTED.get()
                    if expected is None:
                        expected = d['instance'] if d else 'unstarted'
                    socket_path = Path(life.sock)
                    before_socket = None
                    if d is None:
                        if (expected != 'unstarted' or os.path.lexists(history)
                                or life.control or os.path.lexists(life.sock)):
                            raise ValueError('missing process facts; unknown ownership')
                    else:
                        if expected != d['instance'] or d.get('fence'):
                            raise ValueError('process identity mismatch or duplicate delete')
                        life.observe(d)
                        before_history = self.file_identity(history)
                        before_socket = self.socket_identity(socket_path)
                        before_config = doc
                        d.update(fence='delete', phase='fenced')
                        life.save(d)
                        life._stop(d)
                        if not life.observe(d)['settled']:
                            raise RuntimeError('stop not confirmed')
                        after = life.web_record()
                        if (after != before_config
                                or self.file_identity(history) != before_history):
                            raise RuntimeError('file identity changed during stop (or declaration config changed)')
                        remaining_socket = self.socket_identity(socket_path)
                        if remaining_socket is not None and remaining_socket != before_socket:
                            raise RuntimeError('socket identity changed during stop')
                    # Cached clients recheck the declaration; retired inboxes reserve names.
                    result = P.delete_files(name)
                    if self.socket_identity(socket_path) is not None:
                        if self.socket_identity(socket_path) != before_socket:
                            raise RuntimeError('socket identity changed before cleanup')
                        socket_path.unlink()  # confirmed dead owner + exact original inode
                    for path in (life.current, life.local / 'host.log'):
                        if path.exists():
                            self.file_identity(path)
                            path.unlink()
                    if not any(life.local.iterdir()):
                        life.local.rmdir()
                    life.control.clear()
                with B._BRIDGES_LOCK:
                    b = B._BRIDGES.pop(str(history), None)
                if b:
                    b.sup.shutdown()
                    with b.cond:
                        b.terminated = True
                        b.cond.notify_all()
                return dict(result, ok=True, process=dict(instance=expected, settled=True, neverStarted=d is None),
                            retained=['inbox'])
            except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
                return dict(ok=False, error=str(e))

    @staticmethod
    def socket_identity(path):
        try:
            st = path.lstat()
        except FileNotFoundError:
            return None
        if not stat.S_ISSOCK(st.st_mode):
            raise ValueError('endpoint is not an exact socket')
        return st.st_dev, st.st_ino

    @staticmethod
    def file_identity(path):
        try:
            st = path.lstat()
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(st.st_mode):
            raise ValueError('not a regular deletion target')
        return st.st_dev, st.st_ino


class NameCollision(ValueError):
    pass


def build(options, *, registered=True):
    registry = Registry(options)
    registry.registered = registered

    class RegisteredClient(Client):
        def __init__(self, session_path, on_event, cwd=None, profile=None, *, options, declaration_path=None):
            super().__init__(session_path, on_event, cwd, profile, options=options,
                             declaration_path=declaration_path)
            parent = Path(self.session_file).parent
            self.registered = parent.parent == Path(P.sessions_dir()) and parent.name == self.name
            if self.registered:
                doc, _a, h = registry.declaration(self.name)
                if str(h) != self.session_file or cwd != doc['cwd'] or profile != doc['profile']:
                    raise ValueError('client registration mismatch')
                self.life.env['AGENTD_ROOT'] = P.WS

        def check(self):
            if self.registered:
                doc, _a, _h = registry.declaration(self.name)
                if self.life.load() is None and (
                        os.path.lexists(self.session_file) or self.life.control
                        or os.path.lexists(self.life.sock)):
                    raise ValueError('missing process facts with history/runtime; unknown')

        def declaration_alive(self):
            # A process replacement is not deletion. SSE must survive the brief
            # interval while Web accepts the replacement P.
            if not self.registered:
                return True
            try:
                registry.declaration(self.name)
                return True
            except (OSError, ValueError, KeyError, RuntimeError, TypeError):
                return False

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

    class RegisteredBridge(B.OrdinaryBridge):
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
            if getattr(self.sup, 'registered', False) and not self.sup.declaration_alive():
                with self.cond:
                    self.terminated = True
                    self.cond.notify_all()
            return super().stream_closed()

        def iter_events(self, *args, **kwargs):
            # Existing event/keepalive cadence, no new polling worker or tombstone.
            for event in super().iter_events(*args, **kwargs):
                if self.stream_closed():
                    return
                yield event

    def factory(session_path, on_event, cwd=None, profile=None, declaration_path=None):
        return RegisteredClient(session_path, on_event, cwd, profile, options=options,
                                declaration_path=declaration_path)

    registry.client_factory = factory if registered else (
        lambda session_path, on_event, cwd=None, profile=None, declaration_path=None:
        Client(session_path, on_event, cwd, profile, options=options, declaration_path=declaration_path))
    registry.bridge_factory = RegisteredBridge if registered else B.OrdinaryBridge
    return registry
