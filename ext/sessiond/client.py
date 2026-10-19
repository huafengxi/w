"""Ordinary pi-host socket client. Detach never means process termination."""
import json
import logging
import os
from pathlib import Path
import socket
import threading

from ext.sessiond import sessions as P, lifecycle as L

log = logging.getLogger("sessiond-client")

class Client:
    """Bridge-facing ordinary socket client; Lifecycle owns explicit process operations."""
    def __init__(self, session_path, on_event, cwd=None, profile=None, *, options):
        rp = os.path.realpath(session_path)
        self.session_file = rp if P._within_roots(rp) else P.resolve_session_path(session_path)
        self.name = P.display_name(self.session_file)
        self.cwd = P.resolve_cwd(cwd) if cwd else os.path.dirname(self.session_file)
        self.profile = profile
        self.on_event = on_event
        self.pid = None
        self.state = "idle"
        self._closed = False
        self._op_lock = threading.RLock()
        if profile:
            P.profile_spec(profile)
        env = P.clean_env()
        # Bind the canonical JSONL after scrubbing. A is a W-side dependency;
        # neither P nor its pi child needs AGENTD_DIR for the ordinary host.
        env[P.HOST_MARKER] = self.session_file
        env['W_WORKSPACE'] = P.WS
        env.pop('AGENTD_DIR', None)
        if profile:
            env['DISPATCH_PROFILE'] = profile
        ext = ['-e', P.PI_EXTENSION] if os.path.exists(P.PI_EXTENSION) else []
        self.life = L.Lifecycle(self.session_file, self.name, self.cwd,
                              env=env, pi_binary=P.pi_binary(), extensions=ext,
                              prepare=lambda: P.sync_header_cwd(self.session_file, self.cwd), **options)
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
            peer, uid, _gid = L._PROCESS_TOOLS.peer_credentials(s)
            with self.life.lock():
                now = self.life.load()
                if (now['instance'] != d['instance'] or now.get('fence') or uid != os.getuid()
                        or peer != d['host']['pid'] or not L.same(L.identity(peer), d['host'])):
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
        return not self._closed and self.state == 'running' and self._sock is not None

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
            if self._closed:
                return dict(ok=False, error='session closed')
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
        return self.change('reload', L._EXPECTED.get())

    def clear(self):
        return self.change('clear', L._EXPECTED.get())

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


    def _broadcast(self, obj):
        try:
            self.on_event(obj)
        except Exception:
            log.exception("on_event failed")
