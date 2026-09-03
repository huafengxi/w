#!/usr/bin/env python3
"""bridge.py — web ↔ 按路径多会话桥接（任务 0829-1958-od0t；R1：路径路由）。

web 进程内的常驻状态层：每会话路径一个 `Bridge`（持有事件环）+ 各自的
`proc.Supervisor`（直接监督一个 `pi --mode rpc` 会话，进程内直连，无 unix
socket）。首次访问某路径时创建并拉起会话进程，之后复用；不同路径（含不同
目录的同名文件）互不干扰。本模块为同目录 `rpc/api.py`（命令/控制面）与
`rpc/stream.py`（SSE 事件流）提供统一接口。

事件 ID 游标与基线口径沿用（0829-1609-8dwt，原见 sessiond/PROTOCOL.md §6）：
  - 实时流游标 = 桥接分配的不透明事件 ID `<ns>:<seq>`（环内稳定）。
  - 完整历史基线 = 经 pi rpc `get_entries`（全量）；
    `get_entries()` 发命令并等待配对响应（监督员单写者，id 原样往返）。
    响应行照常入环但瘦身（entries 列表替换为条数），避免巨型 SSE 负载。

鉴权不在这里做——由 w 服务的全局 BasicAuth（~/.auth/passwd）承担。
命令拦截面沿用：`switch_session`/`set_session_name` 不透传（评审 S3）。
会话路径校验与解析见 `proc.resolve_session_path`（站内任意 `.jsonl`，
锁定 ~/m 内；注册表键 = 解析后绝对路径）。仅 python3 标准库。
"""
import json
import logging
import os
import threading
import time
import uuid
from collections import deque

from ext.sessiond import proc as _proc

log = logging.getLogger("sessiond-bridge")

RING_MAX = int(os.environ.get("SESSIOND_BRIDGE_RING", "2000"))
MAX_STREAMS_PER_BRIDGE = int(os.environ.get("SESSIOND_BRIDGE_MAXSTREAMS", "5"))
# 去保活（任务 ja0vr7，设计 du44uj §1.2）：
# PRESENCE_GRACE = 在场判定的活动宽限（attach 是一次性 POST，防「刚 attach 还没开流」误判）；
# IDLE_TIMEOUT = 空闲回收阈值（无订阅 ∧ 空闲超阈 → 杀进程转懒态；秒，0=关，测试可调小）。
PRESENCE_GRACE = float(os.environ.get("SESSIOND_PRESENCE_GRACE", "300"))
IDLE_TIMEOUT = float(os.environ.get("SESSIOND_IDLE_TIMEOUT", "900"))
IDLE_SCAN_INTERVAL = float(os.environ.get("SESSIOND_IDLE_SCAN", "60"))
ENTRIES_TIMEOUT = 20.0      # get_entries 往返超时（秒）
COMMANDS_TIMEOUT = 10.0     # get_commands 往返超时（秒，任务 a16jpj）
INSPECT_TIMEOUT = 15.0      # 探针转储往返超时（秒，任务 s0f1la）

# 探针侧车目录（任务 s0f1la）：与 probe.ts 同口径 = ~/m/run/sessiond-inspect
# （宿主本地运行时区，不入 git；文件即用即删）。
INSPECT_DIR = os.path.join(os.path.expanduser("~/m"), "run",
                           "sessiond-inspect")

# 会话面命令拦截（评审 0829-1327-7ca3 S3）：这些命令会改绑 session_file/会话名，
# 使监督登记失准（会话名是 agentd 通知门控依据，铁律 0828-1618-zru9）。
BLOCKED_COMMANDS = {"switch_session", "set_session_name"}
DIALOG_METHODS = {"select", "confirm", "input", "editor"}


class Bridge:
    """单个会话路径的进程内事件环 + 命令通道（按路径注册，见 get_bridge）。"""

    def __init__(self, session_path, cwd=None, sock_path=None, participant_id=None):
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
        # dialog 超时 deadline（monotonic 秒，仅请求带 timeout 字段者有；
        # 0830-1104-eji4：pi 端超时是 rpc-mode 内部 setTimeout，不发任何事件，
        # 桥接不清理会让 attach 基线向新 tab 重建已死 dialog → 按 deadline 清理）
        self._dialog_deadline = {}
        self.subscribers = 0                 # 当前 SSE 流订阅数
        self.last_activity = time.monotonic()  # 最近一次活动（在场判定/空闲回收，去保活）
        # 监督员选择（任务 ybzvbn，票 hegipc）：sock_path 在场 = rpc 封装形态任务，
        # SocketSupervisor 连接透传 socket（无杀权、不重拉）；否则普通 Supervisor spawn。
        if sock_path:
            self.sup = _proc.SocketSupervisor(self._site_path,
                                              on_event=self._ingest,
                                              sock_path=sock_path,
                                              participant_id=participant_id)
            self.terminated = False          # 断连终结标记（iter_events 停流依据）
            self.sup.on_lost = self._socket_lost
        else:
            self.sup = _proc.Supervisor(self._site_path, on_event=self._ingest,
                                        cwd=cwd)
            # 去保活（任务 ja0vr7）：在场判定数据由桥接供给（崩溃无订阅不重拉/空闲回收）。
            self.sup.presence_check = self.has_presence
        self.session = self.sup.name         # 展示名以监督员解析为准

    def note_activity(self):
        """记录会话活动时刻（去保活）：调用点 = attach 基线/上行命令/commands/inspect（本模块）
        与 reload/clear（rpc/api.py 补记）。在场判定与空闲回收共享。"""
        with self.lock:
            self.last_activity = time.monotonic()

    def has_presence(self):
        """在场判定（去保活，设计 du44uj §1.2.1）：① 当前 SSE 订阅数 > 0，或② 距
        最近一次活动 < PRESENCE_GRACE（防「刚 attach 还没开流」窗口误判）。"""
        with self.lock:
            if self.subscribers > 0:
                return True
            return time.monotonic() - self.last_activity < PRESENCE_GRACE

    def _socket_lost(self):
        """透传连接断（任务收敛/被杀，任务 ybzvbn）：标记终结（iter_events 停流 →
        前端重连）+ 摘除注册表（后续访问重新路由：终态任务落入普通复活路径，
        任务会话结束无缝转普通会话）。由 SocketSupervisor.on_lost 回调。"""
        self.terminated = True
        key = None
        try:
            key = _proc.resolve_session_path(self._site_path)
        except ValueError:
            pass
        if key is not None:
            with _BRIDGES_LOCK:
                if _BRIDGES.get(key) is self:
                    del _BRIDGES[key]

    # ---- 监督侧事件入口 ----

    def _ingest(self, obj):
        """监督员回调：生命周期帧与 pi 原始输出行统一入环。"""
        expired = []
        if isinstance(obj, dict):
            t = obj.get("type")
            if t == "extension_ui_request" and obj.get("method") in DIALOG_METHODS:
                with self.lock:
                    expired = self._sweep_expired_dialogs_locked()
                    self.pending_dialogs[obj.get("id")] = obj
                    to = obj.get("timeout")
                    if isinstance(to, (int, float)) and to > 0:
                        self._dialog_deadline[obj.get("id")] = \
                            time.monotonic() + to / 1000.0
            elif t == "sessiond.session_restarted":
                # 会话进程重启：pi 侧 dialog Promise 全部失效，清表并广播结算，
                # 否则 attach 基线会向各端重建已死 dialog（0830-1104-eji4）。
                with self.lock:
                    stale = list(self.pending_dialogs.keys())
                    self.pending_dialogs.clear()
                    self._dialog_deadline.clear()
                if stale:
                    self._broadcast_dialog_resolved(stale, restarted=True)
            elif self._dialog_deadline:
                # 任意 pi 事件都顺带清理超时 dialog（0830-1104-eji4）：pi 超时
                # 后必然有后续事件（工具结果/续写）流经这里，无需额外定时器。
                with self.lock:
                    expired = self._sweep_expired_dialogs_locked()
            if t == "response" and isinstance(obj.get("id"), str):
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
                if waiter is not None and obj.get("command") == "get_commands":
                    # 任务 a16jpj：配对等待同 get_entries；清单可能很长（user 级
                    # skill 16+），环内瘦身为条数计数，完整响应只交给等待者。
                    data = obj.get("data") if isinstance(obj.get("data"), dict) else {}
                    slim = dict(obj)
                    slim["data"] = {"commands": "<%d commands omitted>"
                                    % len(data.get("commands") or [])}
                    self._append(slim)
                    waiter["resp"] = obj
                    waiter["event"].set()
                    return
                if waiter is not None:
                    # 任务 s0f1la：配对等待泛化——其它命令（本场景 = 探针命令的
                    # prompt 回执）也回填等待者。回执是小对象，照常入环不瘦身。
                    self._append(obj)
                    waiter["resp"] = obj
                    waiter["event"].set()
                    return
        if expired:
            self._broadcast_dialog_resolved(expired, timeout=True)
        self._append(obj)

    def _sweep_expired_dialogs_locked(self):
        """清理超时 dialog（须持锁调用），返回被清理的 rid 列表。0830-1104-eji4：
        pi rpc-mode 的 dialog 超时是内部 setTimeout、不发事件；桥接必须自己按
        请求自带的 timeout 字段（毫秒）跟踪 deadline，否则 pending_dialogs 永不清理，
        attach 基线（0830-0956-vk20 bug2）会向新/刷新端重建已死 dialog。"""
        if not self._dialog_deadline:
            return []
        now = time.monotonic()
        expired = [rid for rid, dl in self._dialog_deadline.items()
                   if rid in self.pending_dialogs and now >= dl]
        for rid in expired:
            self.pending_dialogs.pop(rid, None)
            self._dialog_deadline.pop(rid, None)
        return expired

    def _broadcast_dialog_resolved(self, rids, **extra):
        """向全部 SSE 订阅者广播 dialog 结算（0830-1104-eji4）：多 tab 关框同步。
        含应答端自身——其前端已本地 closeDialog 删表，handler 查无 pendingDialogs[id]
        幂等 no-op，安全。事件入环，晚到的订阅者（since 回放）也能收到。"""
        for rid in rids:
            evt = {"type": "sessiond.dialog_resolved", "id": rid}
            evt.update(extra)
            self._append(evt)

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
        self.note_activity()
        self.sup.ensure_started()   # 懒态复活：去保活后崩溃转懒态的会话经此拉起（幂等）
        t = cmd_obj.get("type")
        if t in BLOCKED_COMMANDS:
            return ("command %r is blocked: it would desync the session "
                    "registry (session_file/name)" % (t,))
        if t == "extension_ui_response":
            rid = cmd_obj.get("id")
            with self.lock:
                expired = self._sweep_expired_dialogs_locked()
                hit = self.pending_dialogs.pop(rid, None)
                self._dialog_deadline.pop(rid, None)
            if expired:
                self._broadcast_dialog_resolved(expired, timeout=True)
            if hit is None:
                return "no pending dialog with id %r" % (rid,)
        if t == "abort":
            # 0830-0956-vk20 bug1：会话阻塞于 ask_user 时 /abort 不生效——pi rpc
            # 的 dialog 是裸 Promise（仅 response/signal/timeout 可解），ask_user
            # 不传 signal，session.abort() 触不到它。这里把所有悬空 dialog 以
            # cancelled 代答（pi 端按取消结算 → 工具返回后回合解锁，abort 状态
            # 随即收尾），pi 端不悬空。顺序：先发代答再发 abort（下方循环逐个发
            # 代答，之后才 self.sup.send(cmd_obj) 发 abort；0830-0956-pnqp 评审建议修①：
            # 原注释「先发 abort 再发代答」与代码不符，改描述防误读；功能不受顺序影响）。
            # 两者串行入 stdin。
            with self.lock:
                expired = self._sweep_expired_dialogs_locked()
                stale = list(self.pending_dialogs.keys())
                self.pending_dialogs.clear()
                self._dialog_deadline.clear()
            if expired:
                self._broadcast_dialog_resolved(expired, timeout=True)
            for rid in stale:
                self.sup.send({"type": "extension_ui_response", "id": rid,
                               "cancelled": True})
            if stale:
                self._broadcast_dialog_resolved(stale, cancelled=True)
        if not self.sup.send(cmd_obj):
            return "session process stdin unavailable (state=%s)" \
                   % self.sup.state
        if t == "extension_ui_response":
            # 转发成功后广播结算（0830-1104-eji4）：其它 tab 的 handler
            # （view/index.html `sessiond.dialog_resolved`）据此关框。
            self._broadcast_dialog_resolved([rid])
        return None

    def pending_dialog_list(self):
        """当前悬空 dialog 请求体列表（0830-0956-vk20 bug2）：随 attach/entries
        基线下发，前端刷新/重连后据此重建 pending dialog。"""
        with self.lock:
            expired = self._sweep_expired_dialogs_locked()
            res = list(self.pending_dialogs.values())
        if expired:
            self._broadcast_dialog_resolved(expired, timeout=True)
        return res

    def last_queue_state(self):
        """环内最近一次 queue_update 快照（0830-1457-a57d）：attach 基线补发，
        前端刷新后队列面板直出。pi 只在队列变化时发 queue_update 实时事件，基线不
        含快照会导致面板空到下一次变更；事件环倒序取最新一条即权威当前态。"""
        with self.lock:
            for _seq, _eid, obj in reversed(self.ring):
                if isinstance(obj, dict) and obj.get("type") == "queue_update":
                    return {"steering": list(obj.get("steering") or []),
                            "followUp": list(obj.get("followUp") or [])}
        return {"steering": [], "followUp": []}

    # ---- 基线：get_entries ----

    def get_entries(self, timeout=ENTRIES_TIMEOUT):
        """发 pi rpc get_entries（全量）并等待配对响应。

        返回 {"ok": True, "resp": <pi response>, "watermark": <响应行 eid>,
        "gen": <会话进程世代>}；失败返回 {"ok": False, "error": ...}。
        """
        self.note_activity()
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

    # ---- 只读查询：get_commands（任务 a16jpj） ----

    def get_commands(self, timeout=COMMANDS_TIMEOUT):
        """发 pi rpc get_commands 并等待配对响应（结构镜像 get_entries）。

        返回该会话实际加载的可发现命令清单（extension 注册的斜杠命令 / prompt 模板 /
        skill）；仅列出注册了斜杠命令的 extension（只注册工具/事件钩子的不出现）。
        成功 {"ok": True, "commands": [...]}；失败 {"ok": False, "error": ...}。
        """
        self.note_activity()
        self.sup.ensure_started()
        if not self.sup.wait_ready():
            return {"ok": False,
                    "error": "session process not ready in time (state=%s)"
                             % self.sup.state}
        with self.lock:
            self._req_seq += 1
            rid = "wbgc-%d-%d" % (os.getpid(), self._req_seq)
            waiter = {"event": threading.Event(), "resp": None}
            self.pending[rid] = waiter
        cmd = {"type": "get_commands", "id": rid}
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
                    "error": "get_commands timed out after %.0fs" % timeout}
        resp = waiter["resp"]
        if resp is None:
            return {"ok": False, "error": "no response received"}
        if not resp.get("success"):
            return {"ok": False,
                    "error": "get_commands rejected: %s"
                             % (resp.get("error") or "?")}
        data = resp.get("data") or {}
        return {"ok": True, "commands": data.get("commands") or []}

    # ---- 探针：系统提示词+工具清单转储（任务 s0f1la） ----

    def inspect(self, timeout=INSPECT_TIMEOUT):
        """探针转储编排：经会话内探针扩展（同目录 probe.ts，proc.py:_spawn
        以 -e 注入）取当前系统提示词全文 + 工具清单。

        握手：生成随机 nonce → 下发 prompt `/sessiond-inspect <nonce>`（pi 对
        extension 命令即时执行、即使流式中，命令完成后才发回执）→ 按既有配对等待
        机制等该回执 → 读侧车文件 INSPECT_DIR/<nonce>.json（短轮询兜底）→ 读后删。
        侧车文件不进事件环、不进 jsonl、不进 LLM 上下文，零会话污染；几十 KB 的
        转储走 HTTP 响应（rpc/api.py op=inspect）。

        成功 {"ok": True, "doc": <转储文档>}；失败 {"ok": False, "error": ...}。
        转储文档内 ok:False = 探针 handler 内部异常（带 error 字段）。
        """
        self.note_activity()
        self.sup.ensure_started()
        if not self.sup.wait_ready():
            return {"ok": False,
                    "error": "session process not ready in time (state=%s)"
                             % self.sup.state}
        nonce = uuid.uuid4().hex
        with self.lock:
            self._req_seq += 1
            rid = "wbin-%d-%d" % (os.getpid(), self._req_seq)
            waiter = {"event": threading.Event(), "resp": None}
            self.pending[rid] = waiter
        cmd = {"type": "prompt", "id": rid,
               "message": "/sessiond-inspect %s" % nonce}
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
                    "error": "inspect timed out after %.0fs "
                             "(probe command not acked)" % timeout}
        resp = waiter["resp"]
        if resp is None:
            return {"ok": False, "error": "no response received"}
        if not resp.get("success"):
            return {"ok": False,
                    "error": "probe command rejected: %s"
                             % (resp.get("error") or "?")}
        # 读侧车文件：回执在探针 handler 完成后才发，文件通常已就位；
        # 短轮询兜底跨文件系统可见性毛刺。读后即删。
        path = os.path.join(INSPECT_DIR, nonce + ".json")
        deadline = time.monotonic() + 2.0
        while True:
            try:
                with open(path) as f:
                    doc = json.load(f)
                try:
                    os.unlink(path)
                except OSError:
                    pass
                return {"ok": True, "doc": doc}
            except (OSError, ValueError):
                if time.monotonic() >= deadline:
                    return {"ok": False,
                            "error": "probe dump file missing: %s "
                                     "(extension not loaded or handler failed)"
                                     % path}
                time.sleep(0.1)

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
        """流订阅生成器：先补发 start_seq 之后的缓冲，再实时跟随。锁外 yield。
        桥接终结（任务透传断连，任务 ybzvbn）：发完环内余帧即停流，前端自动重连后
        重新路由（终态任务转复活）。"""
        sent = start_seq
        while True:
            batch = []
            with self.cond:
                while True:
                    batch = [e for e in self.ring if e[0] > sent]
                    if batch:
                        break
                    if getattr(self, "terminated", False):
                        return
                    if not self.cond.wait(timeout=15):
                        break           # 超时 → keep-alive
            if not batch:
                if getattr(self, "terminated", False):
                    return
                yield None              # keep-alive
                continue
            for seq, eid, obj in batch:
                sent = seq
                yield (eid, obj)
            if getattr(self, "terminated", False):
                return


_BRIDGES = {}                     # resolved path -> Bridge（按需多会话）
_BRIDGES_LOCK = threading.Lock()

# 会话显式 cwd 登记（任务 fw2ll1：.agent cwd/dir 拆分）：
# op=agent 解析成功后先登记「会话文件 → 显式 cwd（= .agent 文件所在目录）」，
# 之后该会话路径懒建桥接时按登记把 cwd 显式传给 Supervisor（不再恒等于
# dirname(session_file)）。web 重启后登记随进程消失——前端打开 .agent 页必先经
# op=agent 解析再 attach，登记自然重建；.jsonl 直开无登记 → cwd 缺省 dirname。
_CWD_OVERRIDES = {}               # resolved session_file -> cwd
_CWD_OVERRIDES_LOCK = threading.Lock()


def set_session_cwd(session_path, cwd):
    """登记会话的显式 cwd（任务 fw2ll1，调用方 = rpc/api.py op=agent）。
    session_path 经会话路径校验，cwd 经 proc.resolve_cwd 校验；非法抛 ValueError
    （调用方回 400）。重复登记以最后一次为准；已存在桥接不受影响（桥接生命周期内
    cwd 不变）。"""
    key = _proc.resolve_session_path(session_path)
    _CWD_OVERRIDES_LOCK.acquire()
    try:
        _CWD_OVERRIDES[key] = _proc.resolve_cwd(cwd)
    finally:
        _CWD_OVERRIDES_LOCK.release()


def get_bridge(session_path):
    """按路径取桥接；首次访问创建桥接并拉起监督员。
    注册表键 = 解析后的绝对路径（`/./x` 等变体归一）。

    拉起权单点（去保活二期 ja0vr7，用户硬约束①②③）：
    - agentd 登记会话（/agents/(task|bot)/<名>/session/session.jsonl）按 pid.json 判定
      （_proc.agentd_route）：活 socket → SocketSupervisor 透传直播（只连接不 spawn）；
      终态/缺席/暂停/跨机 → ValueError 拒绝 + 指引/等待提示，**永不 spawn**；
    - 其余路径：仅当 _CWD_OVERRIDES 有登记（= 经 op=agent 解析的 .agent 声明者）才建桥拉起；
      未登记的裸 jsonl 直开 → ValueError（裸 jsonl 拉起废除：.agent 是 web 唯一拉起入口）。
    非法路径抛 ValueError（调用方回 400）。"""
    key = _proc.resolve_session_path(session_path)
    with _BRIDGES_LOCK:
        b = _BRIDGES.get(key)
        if b is None:
            sock_path = None
            route = _proc.agentd_route(session_path)
            if route is not None:
                mode, payload = route
                if mode == "reject":
                    raise ValueError(payload)
                sock_path = payload["sock"]    # live 唯一放行态：只透传不 spawn（硬约束②）
                b = Bridge(session_path, sock_path=sock_path,
                           participant_id=payload.get("participant_id"))
            else:
                with _CWD_OVERRIDES_LOCK:
                    cwd = _CWD_OVERRIDES.get(key)
                if cwd is None:
                    raise ValueError(
                        "裸 jsonl 拉起已废除（任务 ja0vr7）：直开该路径不再拉起宿主；"
                        "浏览器会话入口 = .agent 声明者（唯一），agentd 登记会话的拉起/"
                        "复活归 agentd 监督（本路径经 socket 透传）")
                b = Bridge(session_path, cwd=cwd)
            _BRIDGES[key] = b
            b.sup.ensure_started()
            _ensure_idle_reaper()
        return b


# ----------------------------------------------------------------
# 空闲回收扫描器（去保活，任务 ja0vr7，设计 du44uj §1.2.3）
#
# web 进程级单守护线程：周期扫 _BRIDGES，对「运行中 ∧ 无订阅 ∧ 空闲超阈」的普通会话
# 优雅杀进程转懒态（重开复活）；否则开过一次的会话活到 web 重启 = 变相保活。
# 排除 SocketSupervisor（socket 模式生命周期属 agentd，断连自有终结路径）。
# 双条件防误杀：距最近活动 > IDLE_TIMEOUT（含无上行 cmd）∧ jsonl mtime 老化 > IDLE_TIMEOUT
#（防杀进行中的回合）。

def _idle_scan():
    if IDLE_TIMEOUT <= 0:
        return
    now = time.monotonic()
    with _BRIDGES_LOCK:
        items = list(_BRIDGES.items())
    for _key, b in items:
        sup = b.sup
        if isinstance(sup, _proc.SocketSupervisor):
            continue
        with b.lock:
            if b.subscribers > 0:
                continue
            if now - b.last_activity < IDLE_TIMEOUT:
                continue
        try:
            # 会话仍在干活（近阈值内有写入）→ 不回收；文件尚不存在（pi 懒落盘，
            # 无任何写入）也算空闲——否则从未写过盘的会话永不被回收。
            if time.time() - os.path.getmtime(sup.session_file) < IDLE_TIMEOUT:
                continue
        except OSError:
            pass
        with sup._cond:
            if sup.state != "running" or sup.proc is None:
                continue
        log.info("idle reap candidate: %s (no subscribers, idle >%.0fs)",
                 b.session, IDLE_TIMEOUT)
        sup._idle_reap()


def _idle_reaper_loop():
    while True:
        time.sleep(IDLE_SCAN_INTERVAL)
        try:
            _idle_scan()
        except Exception:
            log.exception("idle scan failed")


_REAPER_LOCK = threading.Lock()
_REAPER_STARTED = False


def _ensure_idle_reaper():
    """幂等启动空闲回收扫描线程（首个桥接建立时拉起）。"""
    global _REAPER_STARTED
    with _REAPER_LOCK:
        if _REAPER_STARTED or IDLE_TIMEOUT <= 0:
            return
        _REAPER_STARTED = True
    threading.Thread(target=_idle_reaper_loop, name="sessiond-idle-reaper",
                     daemon=True).start()
    log.info("idle reaper started (timeout=%.0fs scan=%.0fs)",
             IDLE_TIMEOUT, IDLE_SCAN_INTERVAL)
