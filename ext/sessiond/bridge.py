"""Per-session command channel, bounded event log and browser subscriptions."""
from contextlib import nullcontext
import base64
import json
import logging
import os
import threading
import time
import uuid
from collections import deque

from ext.sessiond import sessions as S
from ext.sessiond import agent_client as A
from ext.sessiond.declaration import resolve_agent

log = logging.getLogger("sessiond-bridge")

RING_MAX = int(os.environ.get("SESSIOND_BRIDGE_RING", "2000"))
MAX_STREAMS_PER_BRIDGE = int(os.environ.get("SESSIOND_BRIDGE_MAXSTREAMS", "5"))
ENTRIES_TIMEOUT = 20.0      # get_entries 往返超时（秒）
COMMANDS_TIMEOUT = 10.0     # get_commands 往返超时（秒，任务 a16jpj）
INSPECT_TIMEOUT = 15.0      # 探针转储往返超时（秒，任务 s0f1la）

# 探针侧车目录：与 pi-extension.ts 共用调用方 workspace/run/sessiond-inspect
# （宿主本地运行时区，不入 git；文件即用即删）。
from core.config import workspace
INSPECT_DIR = os.path.join(workspace(), "run",
                           "sessiond-inspect")

# 会话面命令拦截（评审 0829-1327-7ca3 S3）：这些命令会改绑 session_file/会话名，
# 使监督登记失准（会话名是 agentd 通知门控依据，铁律 0828-1618-zru9）。
BLOCKED_COMMANDS = {"switch_session", "set_session_name"}
DIALOG_METHODS = {"select", "confirm", "input", "editor"}


class Bridge:
    """单个会话路径的进程内事件环 + 命令通道（按路径注册，见 get_bridge）。"""

    def __init__(self, session_path, cwd=None, sock_path=None, participant_id=None,
                 profile=None, *, client_factory=None, declaration_path=None):
        # 站内路径（如 /foo/bar/x.jsonl）；解析/校验在客户端内。
        self._site_path = session_path
        self.session = None
        self.terminated = False
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
        self.stop_requested = False
        # dialog 超时 deadline（monotonic 秒，仅请求带 timeout 字段者有；
        # 0830-1104-eji4：pi 端超时是 rpc-mode 内部 setTimeout，不发任何事件，
        # 桥接不清理会让 attach 基线向新 tab 重建已死 dialog → 按 deadline 清理）
        self._dialog_deadline = {}
        self.subscribers = 0                 # 当前 SSE 流订阅数
        self.stream_wakes = set()            # multiplex streams share event notifications
        # 监督员选择（任务 ybzvbn，票 hegipc）：sock_path 在场 = rpc 封装形态任务，
        # AgentClient 只连接 agentd socket；普通客户端由显式工厂构造。
        if sock_path:
            self.sup = A.AgentClient(self._site_path,
                                              on_event=self._ingest,
                                              sock_path=sock_path,
                                              participant_id=participant_id)
            self.terminated = False          # 断连终结标记（iter_events 停流依据）
            self.sup.on_lost = self._socket_lost
        else:
            if client_factory is None:
                raise ValueError('ordinary Bridge requires an explicit client factory')
            options = dict(declaration_path=declaration_path) if declaration_path else {}
            self.sup = client_factory(self._site_path, on_event=self._ingest,
                                      cwd=cwd, profile=profile, **options)
        self.session = self.sup.name         # 展示名以监督员解析为准

    def _socket_lost(self):
        """透传连接断（任务收敛/被杀，任务 ybzvbn）：标记终结（iter_events 停流 →
        前端重连）+ 摘除注册表（后续访问重新核验 agentd 登记，终态拒绝，不转普通会话）。
        由 AgentClient.on_lost 回调。"""
        self.terminated = True
        key = None
        try:
            key = S.resolve_session_path(self._site_path)
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
            if t == "agent_start":
                self.stop_requested = False
            if t == "extension_ui_request" and obj.get("method") in DIALOG_METHODS:
                with self.lock:
                    expired = self._sweep_expired_dialogs_locked()
                    self.pending_dialogs[obj.get("id")] = obj
                    to = obj.get("timeout")
                    if isinstance(to, (int, float)) and to > 0:
                        self._dialog_deadline[obj.get("id")] = \
                            time.monotonic() + to / 1000.0
            elif t in ("sessiond.session_restarted", "sessiond.session_dormant"):
                # 会话进程重启：pi 侧 dialog Promise 全部失效，清表并广播结算，
                # 否则 attach 基线会向各端重建已死 dialog（0830-1104-eji4）。
                with self.lock:
                    stale = list(self.pending_dialogs.keys())
                    self.pending_dialogs.clear()
                    self._dialog_deadline.clear()
                    pending = list(self.pending.values())
                    self.pending.clear()
                for waiter in pending:
                    waiter["resp"] = {"success": False, "error": "session generation ended"}
                    waiter["event"].set()
                if stale:
                    self._broadcast_dialog_resolved(stale, restarted=True)
            elif self._dialog_deadline:
                # 任意 pi 事件都顺带清理超时 dialog（0830-1104-eji4）：pi 超时
                # 后必然有后续事件（工具结果/续写）流经这里，无需额外定时器。
                with self.lock:
                    expired = self._sweep_expired_dialogs_locked()
            if expired:
                self._broadcast_dialog_resolved(expired, timeout=True)
                expired = []
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
            for wake in getattr(self, 'stream_wakes', ()):
                wake.set()
            return self.seq, eid

    def stream_closed(self):
        return self.terminated

    # ---- 上行 ----

    def send(self, cmd_obj):
        """向会话发一条命令。返回错误串或 None。"""
        self.sup.ensure_started()   # Explicit access restarts an exited generation.
        if not self.sup.wait_ready():
            # Startup is asynchronous; wait before sending the first command.
            return ("session process not ready in time (state=%s)"
                    % self.sup.state)
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
        if t == "abort":
            self.stop_requested = True
        if not self.sup.send(cmd_obj):
            if t == "abort":
                self.stop_requested = False
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

    def request(self, command, timeout=COMMANDS_TIMEOUT):
        """RPC round trip, not merely an stdin write acknowledgement."""
        # Register only after startup: session_restarted invalidates old waiters.
        self.sup.ensure_started()
        if not self.sup.wait_ready():
            return {"ok": False, "error": "session process not ready (state=%s)" % self.sup.state}
        with self.lock:
            self._req_seq += 1
            rid = "wbreq-%s-%d" % (self.ns, self._req_seq)
            waiter = {"event": threading.Event(), "resp": None}
            self.pending[rid] = waiter
        try:
            err = self.send(dict(command, id=rid))
            if err:
                return {"ok": False, "error": err}
            if not waiter["event"].wait(timeout):
                return {"ok": False, "error": "RPC timeout; delivery unknown, do not blindly retry"}
            resp = waiter["resp"] or {}
            return {"ok": bool(resp.get("success")), "data": resp.get("data"),
                    "error": None if resp.get("success") else resp.get("error") or "RPC rejected"}
        finally:
            with self.lock:
                self.pending.pop(rid, None)

    def chat_action(self, action, payload=None):
        """Public extension command: no fork/file rebinding or JSONL rewriting."""
        # Old live pi generations may lack this newly added command. Never send
        # an unknown /command as a prompt: pi would deliver it to the model.
        commands = self.get_commands()
        if not commands.get("ok"):
            return commands
        if not any(c.get("name") == "sessiond-chat" for c in commands.get("commands", [])):
            return {"ok": False, "error": "chat extension unavailable; reload this idle session to activate it"}
        nonce = uuid.uuid4().hex
        args = base64.b64encode(json.dumps(dict(payload or {}, action=action,
                                               nonce=nonce)).encode()).decode()
        r = self.request({"type": "prompt", "message": "/sessiond-chat " + args})
        if not r["ok"]:
            return r
        path = os.path.join(INSPECT_DIR, nonce + ".json")
        try:
            with open(path) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {"ok": False, "error": "chat extension unavailable; reload this idle session to activate it"}
        finally:
            if os.path.isfile(path):
                os.unlink(path)

    # ---- 只读查询：get_commands（任务 a16jpj） ----

    def get_commands(self, timeout=COMMANDS_TIMEOUT):
        """发 pi rpc get_commands 并等待配对响应（结构镜像 get_entries）。

        返回该会话实际加载的可发现命令清单（extension 注册的斜杠命令 / prompt 模板 /
        skill）；仅列出注册了斜杠命令的 extension（只注册工具/事件钩子的不出现）。
        成功 {"ok": True, "commands": [...]}；失败 {"ok": False, "error": ...}。
        """
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
        """探针转储编排：经会话内探针扩展（同目录 pi-extension.ts，client.py
        以 -e 注入）取当前系统提示词全文 + 工具清单。

        握手：生成随机 nonce → 下发 prompt `/sessiond-inspect <nonce>`（pi 对
        extension 命令即时执行、即使流式中，命令完成后才发回执）→ 按既有配对等待
        机制等该回执 → 读侧车文件 INSPECT_DIR/<nonce>.json（短轮询兜底）→ 读后删。
        侧车文件不进事件环、不进 jsonl、不进 LLM 上下文，零会话污染；几十 KB 的
        转储走 HTTP 响应（rpc/api.py op=inspect）。

        成功 {"ok": True, "doc": <转储文档>}；失败 {"ok": False, "error": ...}。
        转储文档内 ok:False = 探针 handler 内部异常（带 error 字段）。
        """
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
            return oldest - 1, True

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
                        if batch[0][0] > sent + 1:
                            seq, eid, _ = batch[0]
                            batch = [(seq - 1, "%s:%d" % (self.ns, seq - 1),
                                      {"type": "webgw.gap", "gap": True})] + batch
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


class OrdinaryBridge(Bridge):
    # Keep each original synchronous query and its captured instance together.
    # The reader never takes this operation lock, so RPC responses can arrive.
    # AgentClient is not given ordinary control/locks.
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


_BRIDGES = {}                     # resolved path -> Bridge（按需多会话）
_BRIDGES_LOCK = threading.RLock()

def detach_session(session_path):
    """Close a local channel, never claim that detaching stopped P or pi."""
    key = S.resolve_session_path(session_path)
    with _BRIDGES_LOCK:
        channel = _BRIDGES.pop(key, None)
    if channel is None:
        return dict(found=False, killed=False)
    if isinstance(channel.sup, A.AgentClient):
        return dict(found=True, killed=False, error='agent lifecycle belongs to agentd')
    result = channel.sup.shutdown()
    return dict(result, found=True, killed=False)


def get_bridge(entry, store=None):
    from ext.sessiond.bootstrap import get_registry
    return get_registry().get_bridge(entry, store=store)


def _get_bridge_locked(entry, store=None, *, client_factory, bridge_factory):
    """Resolve the declaration on every request; share only the live event channel."""
    declaration = None
    if isinstance(entry, str) and entry.endswith(".agent"):
        if store is None:
            raise ValueError("agent declaration requires a store")
        meta, body = resolve_agent(store, entry)
        declaration = json.loads(body)
        if not declaration.get("ok"):
            raise ValueError(declaration.get("error", "invalid declaration"))
        session = declaration["session"]
    else:
        session, error = A.agentd_entry(entry)
        if error:
            raise ValueError(error)
        if session == entry:
            raise ValueError("chat entry must be an .agent declaration or agentd spec.json")
    key = S.resolve_session_path(session)
    with _BRIDGES_LOCK:
        b = _BRIDGES.get(key)
        if b is None:
            route = A.agentd_route(session)
            if route is not None:
                mode, payload = route
                if mode == "reject":
                    raise ValueError(payload)
                b = Bridge(session, sock_path=payload["sock"],
                           participant_id=payload.get("participant_id"))
            elif declaration is not None:
                options = {}
                companion = os.path.splitext(key)[0] + '.agent'
                if not os.path.isfile(companion):
                    options['declaration_path'] = declaration.get('declaration_file')
                    if not options['declaration_path']:
                        raise ValueError('ordinary runtime requires a local writable .agent declaration')
                b = bridge_factory(session, cwd=declaration["cwd"], profile=declaration["profile"],
                                   client_factory=client_factory, **options)
            else:
                raise ValueError("session is not registered with agentd")
            _BRIDGES[key] = b
            b.sup.ensure_started()
        return b
