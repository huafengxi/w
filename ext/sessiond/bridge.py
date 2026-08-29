#!/usr/bin/env python3
"""bridge.py — sessiond <-> browser 桥接（w/ext/sessiond，任务 0829-1510-w25l；
完整历史/事件 ID 游标/缺口自愈：任务 0829-1609-8dwt）。

把浏览器的命令/事件流桥接到 sessiond 的本机 unix socket（协议见
~/m/sessiond/PROTOCOL.md）。本模块是 w HTTP 服务器内的常驻状态层：每个被挂接的
会话在进程内维持一条持久 `sock/<session>.sock` 连接（Bridge），上行命令与下行流
共用该连接——这样守护侧的响应 id 路由（`c<clientId>x<origId>`）命中同一挂接。

命令拦截面、dialog 仲裁、响应路由全部沿用 sessiond 守护侧实现，本模块不重复实现；
对守护只做透传。模块级注册表跨请求常驻（同一 w 进程），供同目录的
`rpc/api.py`（命令/控制面）与 `rpc/stream.py`（SSE 事件流）共享。

鉴权不在这里做——由 w 服务的全局 BasicAuth（~/.auth/passwd）承担。

游标与基线（0829-1609-8dwt，口径见 PROTOCOL.md §6）：
  - 实时流游标 = 桥接分配的不透明事件 ID `e<seq>`（环内稳定；桥重建即失效，
    失效后由 gap 标记 + entries 基线自愈，不依赖跨重启稳定性）。
  - 完整历史基线 = 经守护透传 pi rpc `get_entries`（since=entry id 游标）；
    `Bridge.get_entries()` 发命令并等待配对响应（守护 id 路由保证只回本桥）。
    响应行照常入环但瘦身（entries 列表替换为条数），避免巨型 SSE 负载。

仅 python3 标准库。
"""
import json
import os
import socket
import threading
import time
import uuid
from collections import deque

SOCK_DIR = os.environ.get("SESSIOND_SOCK_DIR",
                          os.path.expanduser("~/m/sessiond/sock"))
RING_MAX = int(os.environ.get("SESSIOND_BRIDGE_RING", "2000"))
BRIDGE_IDLE_SECS = 600      # 无 SSE 订阅且无活动的 Bridge 回收时限
ENTRIES_TIMEOUT = 20.0      # get_entries 往返超时（秒）
HELLO_TIMEOUT = 5.0         # 建桥后等待 sessiond.hello 的超时（秒）

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
        self.ring = deque(maxlen=RING_MAX)   # (seq, eid, obj)
        self.seq = 0
        # 事件 ID 命名空间 = 桥实例随机前缀（0829-1609-8dwt）：eid 只在单个 Bridge
        # 生命周期内有效；守护重启后新桥的 eid 前缀不同 → 旧游标必然查不到 →
        # gap 帧 → 前端基线自愈，不会被同序号的新桥事件误命中。
        self.ns = uuid.uuid4().hex[:8]
        self.epoch = None                    # sessiond.hello 报告的守护世代
        self.gen = None                      # sessiond.hello 报告的会话进程世代
        self.hello_evt = threading.Event()
        self.pending = {}                    # req id -> waiter dict（get_entries 等）
        self._req_seq = 0
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
            for w in self.pending.values():
                w["event"].set()
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

    def _append(self, obj):
        """入环（锁内调用）。返回 (seq, eid)。"""
        self.seq += 1
        eid = "%s:%d" % (self.ns, self.seq)
        self.ring.append((self.seq, eid, obj))
        self.last_active = time.time()
        return self.seq, eid

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
                    t = obj.get("type") if isinstance(obj, dict) else None
                    if t == "sessiond.hello":
                        self.epoch = obj.get("epoch")
                        self.gen = obj.get("gen")
                        self.hello_evt.set()
                    slim = obj
                    waiter = None
                    if t == "response" and isinstance(obj.get("id"), str):
                        waiter = self.pending.pop(obj["id"], None)
                        if waiter is not None and obj.get("command") == "get_entries":
                            # 环内瘦身：巨型 entries 列表不进 SSE 环（完整响应只交给等待者）
                            data = obj.get("data") if isinstance(obj.get("data"), dict) else {}
                            slim = dict(obj)
                            slim["data"] = {"entries": "<%d entries omitted>"
                                            % len(data.get("entries") or []),
                                            "leafId": data.get("leafId")}
                    seq, eid = self._append(slim)
                    if waiter is not None:
                        waiter["resp"] = obj          # 完整响应交给等待者
                        waiter["eid"] = eid           # 响应行的事件 ID = 水位
                        waiter["event"].set()
                    self.cond.notify_all()
        # socket 断开（如 sessiond 重启）：给订阅者一个标记后销毁本桥；
        # 前端重连后重建新桥（由 sessiond 回放 + entries 基线补齐）。
        with self.cond:
            self._append({"type": "webgw.bridge_lost", "session": self.session})
            self.dead = True
            for w in self.pending.values():
                w["event"].set()
            self.cond.notify_all()
        log("bridge [%s] socket closed; dropped", self.session)
        drop_bridge(self.session)

    # ---- 基线：get_entries 透传（0829-1609-8dwt） ----

    def get_entries(self, since=None, timeout=ENTRIES_TIMEOUT):
        """发 pi rpc get_entries 并等待配对响应。

        返回 dict：{"ok": True, "resp": <pi response>, "watermark": <响应行 eid>,
        "epoch": <守护世代>}；超时/桥死返回 {"ok": False, "error": ...}。
        watermark = 响应行入环时的事件 ID——快照时刻的精确分界：eid <= watermark
        的事件先于或等于快照，eid > watermark 的事件晚于快照。
        """
        if self.dead:
            return {"ok": False, "error": "bridge dead"}
        self.hello_evt.wait(HELLO_TIMEOUT)     # 拿到 epoch；命令本身不依赖 hello
        with self.lock:
            self._req_seq += 1
            rid = "wbge-%d-%d" % (os.getpid(), self._req_seq)
            waiter = {"event": threading.Event(), "resp": None, "eid": None}
            self.pending[rid] = waiter
        cmd = {"type": "get_entries", "id": rid}
        if since:
            cmd["since"] = since
        try:
            self.send(cmd)
        except (ConnectionError, OSError) as e:
            with self.lock:
                self.pending.pop(rid, None)
            return {"ok": False, "error": "send failed: %s" % e}
        if not waiter["event"].wait(timeout):
            with self.lock:
                self.pending.pop(rid, None)
            return {"ok": False, "error": "get_entries timed out after %.0fs"
                    % timeout}
        if waiter["resp"] is None:              # 等待期间桥被关闭
            return {"ok": False, "error": "bridge closed while waiting"}
        with self.lock:
            epoch = self.epoch
        return {"ok": True, "resp": waiter["resp"], "watermark": waiter["eid"],
                "epoch": epoch}

    # ---- 游标解析与流订阅 ----

    def resolve_since(self, since_eid):
        """事件 ID → 环内位置。返回 (start_seq, gap)。

        since 为空 → 从头（无 gap）；命中 → 该事件之后续流；未命中（未知/已淘汰/
        来自旧桥）→ 从最旧可用位续流并置 gap，由调用方发缺口标记让前端走基线自愈。
        """
        with self.lock:
            if not since_eid:
                return 0, False
            for seq, eid, _ in self.ring:
                if eid == since_eid:
                    return seq, False
            oldest = self.ring[0][0] if self.ring else self.seq + 1
            return oldest, True

    def iter_events(self, start_seq):
        """流订阅生成器：先补发 start_seq 之后的缓冲，再实时跟随。锁外 yield。"""
        sent = start_seq
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
                for seq, eid, obj in batch:
                    sent = seq
                    yield (eid, obj)
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
        log("bridge [%s] attached (ring=%d)", session, RING_MAX)
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
