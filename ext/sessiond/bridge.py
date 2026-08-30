#!/usr/bin/env python3
"""bridge.py — web ↔ 按路径多会话桥接（任务 0829-1958-od0t；R1：路径路由）。

web 进程内的常驻状态层：每会话路径一个 `Bridge`（持有事件环）+ 各自的
`proc.Supervisor`（直接监督一个 `pi --mode rpc` 会话，进程内直连，无 unix
socket）。首次访问某路径时创建并拉起会话进程，之后复用；不同路径（含不同
目录的同名文件）互不干扰。本模块为同目录 `rpc/api.py`（命令/控制面）与
`rpc/stream.py`（SSE 事件流）提供统一接口。

事件 ID 游标与基线口径沿用（0829-1609-8dwt，原见 sessiond/PROTOCOL.md §6）：
  - 实时流游标 = 桥接分配的不透明事件 ID `<ns>:<seq>`（环内稳定）。
  - 完整历史基线 = 经 pi rpc `get_entries`（since=entry id 游标）；
    `get_entries()` 发命令并等待配对响应（监督员单写者，id 原样往返）。
    响应行照常入环但瘦身（entries 列表替换为条数），避免巨型 SSE 负载。

鉴权不在这里做——由 w 服务的全局 BasicAuth（~/.auth/passwd）承担。
命令拦截面沿用：`switch_session`/`set_session_name` 不透传（评审 S3）。
会话路径校验与解析见 `proc.resolve_session_path`（站内任意 `.jsonl`，
锁定 ~/m 内；注册表键 = 解析后绝对路径）。仅 python3 标准库。
"""
import json
import os
import threading
import time
import uuid
from collections import deque

from ext.sessiond import proc as _proc

RING_MAX = int(os.environ.get("SESSIOND_BRIDGE_RING", "2000"))
MAX_STREAMS_PER_BRIDGE = int(os.environ.get("SESSIOND_BRIDGE_MAXSTREAMS", "5"))
ENTRIES_TIMEOUT = 20.0      # get_entries 往返超时（秒）

# 会话面命令拦截（评审 0829-1327-7ca3 S3）：这些命令会改绑 session_file/会话名，
# 使监督登记失准（会话名是 agentd 通知门控依据，铁律 0828-1618-zru9）。
BLOCKED_COMMANDS = {"switch_session", "set_session_name"}
DIALOG_METHODS = {"select", "confirm", "input", "editor"}


class Bridge:
    """单个会话路径的进程内事件环 + 命令通道（按路径注册，见 get_bridge）。"""

    def __init__(self, session_path):
        # 站内路径（如 /assistant/foo.jsonl）；解析/校验在 Supervisor 内。
        self._site_path = session_path
        self.session = None      # 展示名，待监督员解析后回填
        self.lock = threading.Lock()
        self.cond = threading.Condition(self.lock)
        self.ring = deque(maxlen=RING_MAX)   # (seq, eid, obj)
        self.seq = 0
        self.ns = uuid.uuid4().hex[:8]
        self.pending = {}                    # req id -> waiter dict（get_entries）
        self._req_seq = 0
        # 挂起 dialog：id -> 完整 extension_ui_request 对象（应答校验 + 基线
        # 重建，0830-0956-vk20 bug2：前端刷新/重连后经 attach 文档重建对话框）
        self.pending_dialogs = {}
        self.subscribers = 0                 # 当前 SSE 流订阅数
        self.sup = _proc.Supervisor(self._site_path, on_event=self._ingest)
        self.session = self.sup.name         # 展示名以监督员解析为准

    # ---- 监督侧事件入口 ----

    def _ingest(self, obj):
        """监督员回调：生命周期帧与 pi 原始输出行统一入环。"""
        if isinstance(obj, dict):
            t = obj.get("type")
            if t == "extension_ui_request" and obj.get("method") in DIALOG_METHODS:
                with self.lock:
                    self.pending_dialogs[obj.get("id")] = obj
            elif t == "response" and isinstance(obj.get("id"), str):
                waiter = None
                with self.lock:
                    waiter = self.pending.pop(obj["id"], None)
                if waiter is not None and obj.get("command") == "get_entries":
                    # 环内瘦身：巨型 entries 列表不进 SSE 环（完整响应只交给等待者）
                    data = obj.get("data") if isinstance(obj.get("data"), dict) else {}
                    slim = dict(obj)
                    slim["data"] = {"entries": "<%d entries omitted>"
                                    % len(data.get("entries") or []),
                                    "leafId": data.get("leafId")}
                    _, eid = self._append(slim)
                    waiter["resp"] = obj      # 完整响应交给等待者
                    waiter["eid"] = eid       # 响应行的事件 ID = 水位
                    waiter["event"].set()
                    return
        self._append(obj)

    def _append(self, obj):
        with self.cond:
            self.seq += 1
            eid = "%s:%d" % (self.ns, self.seq)
            self.ring.append((self.seq, eid, obj))
            self.cond.notify_all()
            return self.seq, eid

    # ---- 上行 ----

    def send(self, cmd_obj):
        """向会话发一条命令。返回错误串或 None。"""
        t = cmd_obj.get("type")
        if t in BLOCKED_COMMANDS:
            return ("command %r is blocked: it would desync the session "
                    "registry (session_file/name)" % (t,))
        if t == "extension_ui_response":
            rid = cmd_obj.get("id")
            with self.lock:
                if rid not in self.pending_dialogs:
                    return "no pending dialog with id %r" % (rid,)
                self.pending_dialogs.pop(rid, None)
        if t == "abort":
            # 0830-0956-vk20 bug1：会话阻塞于 ask_user 时 /abort 不生效——pi rpc
            # 的 dialog 是裸 Promise（仅 response/signal/timeout 可解），ask_user
            # 不传 signal，session.abort() 触不到它。这里把所有悬空 dialog 以
            # cancelled 代答（pi 端按取消结算 → 工具返回后回合解锁，abort 状态
            # 随即收尾），pi 端不悬空。先发 abort 再发代答，顺序串行入 stdin。
            with self.lock:
                stale = list(self.pending_dialogs.keys())
                self.pending_dialogs.clear()
            for rid in stale:
                self.sup.send({"type": "extension_ui_response", "id": rid,
                               "cancelled": True})
        if not self.sup.send(cmd_obj):
            return "session process stdin unavailable (state=%s)" \
                   % self.sup.state
        return None

    def pending_dialog_list(self):
        """当前悬空 dialog 请求体列表（0830-0956-vk20 bug2）：随 attach/entries
        基线下发，前端刷新/重连后据此重建 pending dialog。"""
        with self.lock:
            return list(self.pending_dialogs.values())

    # ---- 基线：get_entries ----

    def get_entries(self, since=None, timeout=ENTRIES_TIMEOUT):
        """发 pi rpc get_entries 并等待配对响应。

        返回 {"ok": True, "resp": <pi response>, "watermark": <响应行 eid>,
        "gen": <会话进程世代>}；失败返回 {"ok": False, "error": ...}。
        """
        self.sup.ensure_started()
        if not self.sup.wait_ready():
            return {"ok": False,
                    "error": "session process not ready in time (state=%s)"
                             % self.sup.state}
        with self.lock:
            self._req_seq += 1
            rid = "wbge-%d-%d" % (os.getpid(), self._req_seq)
            waiter = {"event": threading.Event(), "resp": None}
            self.pending[rid] = waiter
        cmd = {"type": "get_entries", "id": rid}
        if since:
            cmd["since"] = since
        if not self.sup.send(cmd):
            with self.lock:
                self.pending.pop(rid, None)
            return {"ok": False,
                    "error": "session process unavailable (state=%s)"
                             % self.sup.state}
        if not waiter["event"].wait(timeout):
            with self.lock:
                self.pending.pop(rid, None)
            return {"ok": False,
                    "error": "get_entries timed out after %.0fs" % timeout}
        if waiter["resp"] is None:
            return {"ok": False, "error": "no response received"}
        return {"ok": True, "resp": waiter["resp"],
                "watermark": waiter.get("eid") or "",
                "gen": self.sup.gen}

    # ---- 游标解析与流订阅 ----

    def resolve_since(self, since_eid):
        """事件 ID → 环内位置。返回 (start_seq, gap)。"""
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
        while True:
            batch = []
            with self.cond:
                while True:
                    batch = [e for e in self.ring if e[0] > sent]
                    if batch:
                        break
                    if not self.cond.wait(timeout=15):
                        break           # 超时 → keep-alive
            if not batch:
                yield None              # keep-alive
                continue
            for seq, eid, obj in batch:
                sent = seq
                yield (eid, obj)


_BRIDGES = {}                     # resolved path -> Bridge（按需多会话）
_BRIDGES_LOCK = threading.Lock()


def get_bridge(session_path):
    """按路径取桥接；首次访问创建并拉起该会话的监督员（懒拉起）。
    注册表键 = 解析后的绝对路径（`/./x` 等变体归一）。
    非法路径抛 ValueError（调用方回 400）。"""
    key = _proc.resolve_session_path(session_path)
    with _BRIDGES_LOCK:
        b = _BRIDGES.get(key)
        if b is None:
            b = Bridge(session_path)
            _BRIDGES[key] = b
            b.sup.ensure_started()
        return b
