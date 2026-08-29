#!/usr/bin/env python3
"""bridge.py — sessiond <-> browser 桥接（w/ext/sessiond，任务 0829-1510-w25l）。

把浏览器的命令/事件流桥接到 sessiond 的本机 unix socket（协议见
~/m/sessiond/PROTOCOL.md）。本模块是 w HTTP 服务器内的常驻状态层：每个被挂接的
会话在进程内维持一条持久 `sock/<session>.sock` 连接（Bridge），上行命令与下行流
共用该连接——这样守护侧的响应 id 路由（`c<clientId>x<origId>`）命中同一挂接。

命令拦截面、dialog 仲裁、响应路由全部沿用 sessiond 守护侧实现，本模块不重复实现；
对守护只做透传。模块级注册表跨请求常驻（同一 w 进程），供同目录的
`rpc/api.py`（命令/控制面）与 `rpc/stream.py`（SSE 事件流）共享。

鉴权不在这里做——由 w 服务的全局 BasicAuth（~/.auth/passwd）承担。

仅 python3 标准库。
"""
import json
import os
import socket
import threading
import time
from collections import deque

SOCK_DIR = os.environ.get("SESSIOND_SOCK_DIR",
                          os.path.expanduser("~/m/sessiond/sock"))
RING_MAX = 2000
BRIDGE_IDLE_SECS = 600      # 无 SSE 订阅且无活动的 Bridge 回收时限

_log_lock = threading.Lock()


def log(fmt, *args):
    import logging
    try:
        logging.info("sessiond-bridge: " + (fmt % args if args else fmt))
    except Exception:
        pass


class Bridge:
    """一个会话的持久挂接连接（进程内常驻）。"""

    def __init__(self, session):
        self.session = session
        self.path = os.path.join(SOCK_DIR, session + ".sock")
        self.lock = threading.Lock()
        self.cond = threading.Condition(self.lock)
        self.ring = deque(maxlen=RING_MAX)   # (seq, obj)
        self.seq = 0
        self.subscribers = 0                 # 当前流订阅数
        self.last_active = time.time()
        self.dead = False
        self.sock = None

    def connect(self):
        sk = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sk.connect(self.path)
        self.sock = sk
        threading.Thread(target=self._reader, daemon=True).start()

    def close(self):
        self.dead = True
        with self.cond:
            self.cond.notify_all()
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass

    def send(self, obj):
        if self.dead:
            raise ConnectionError("bridge dead")
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8") + b"\n"
        with self.lock:
            self.last_active = time.time()
        self.sock.sendall(data)

    def _reader(self):
        buf = b""
        while not self.dead:
            try:
                chunk = self.sock.recv(1 << 20)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                with self.cond:
                    self.seq += 1
                    self.ring.append((self.seq, obj))
                    self.last_active = time.time()
                    self.cond.notify_all()
        # socket 断开（如 sessiond 重启）：给订阅者一个标记后销毁本桥；
        # 前端重连后重建新桥（由 sessiond 回放补齐）。
        with self.cond:
            self.seq += 1
            self.ring.append((self.seq, {"type": "webgw.bridge_lost",
                                         "session": self.session}))
            self.dead = True
            self.cond.notify_all()
        log("bridge [%s] socket closed; dropped", self.session)
        drop_bridge(self.session)

    def iter_from(self, since):
        """流订阅生成器：先补发 since 之后的缓冲，再实时跟随。锁外 yield。"""
        sent = since
        with self.cond:
            self.subscribers += 1
        try:
            while True:
                batch = []
                with self.cond:
                    while True:
                        batch = [e for e in self.ring if e[0] > sent]
                        if batch:
                            break
                        if self.dead:
                            return
                        if not self.cond.wait(timeout=15):
                            break           # 超时 → keep-alive
                if not batch:
                    yield None              # keep-alive
                    continue
                for seq, obj in batch:
                    sent = seq
                    yield (seq, obj)
                    if obj.get("type") == "webgw.bridge_lost":
                        return
        finally:
            with self.cond:
                self.subscribers -= 1


BRIDGES = {}            # session -> Bridge
BRIDGES_LOCK = threading.Lock()


def session_socket_exists(session):
    return os.path.exists(os.path.join(SOCK_DIR, session + ".sock"))


def get_bridge(session, create=True):
    with BRIDGES_LOCK:
        b = BRIDGES.get(session)
        if b and not b.dead:
            return b
        if not create:
            return None
        if not session_socket_exists(session):
            raise FileNotFoundError("no such session socket: %s" % session)
        b = Bridge(session)
        b.connect()
        BRIDGES[session] = b
        log("bridge [%s] attached", session)
        return b


def drop_bridge(session):
    with BRIDGES_LOCK:
        BRIDGES.pop(session, None)


def close_bridge(session):
    with BRIDGES_LOCK:
        b = BRIDGES.pop(session, None)
    if b:
        b.close()


def ctl_status():
    path = os.path.join(SOCK_DIR, "ctl.sock")
    sk = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sk.settimeout(5)
        sk.connect(path)
        sk.sendall(b'{"cmd":"status"}\n')
        buf = b""
        while b"\n" not in buf:
            chunk = sk.recv(1 << 20)
            if not chunk:
                break
            buf += chunk
        return json.loads(buf.split(b"\n", 1)[0])
    finally:
        sk.close()


def gc_idle_bridges():
    """由 stream/api 调用时顺带清理久未活动的桥（无常驻线程）。"""
    now = time.time()
    with BRIDGES_LOCK:
        stale = [b for b in BRIDGES.values()
                 if b.subscribers == 0
                 and now - b.last_active > BRIDGE_IDLE_SECS]
    for b in stale:
        log("bridge [%s] idle; closing", b.session)
        close_bridge(b.session)
