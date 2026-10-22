"""Agentd socket client, route validation and control-request proxy. No pi spawn.
"""
import json
import logging
import os
import posixpath
import re
import socket
import sys
import threading
import time

from ext.sessiond import sessions as S

from execution import metadata as agent_metadata, state_view as agent_state

def agent_workspace():
    return os.environ["W_AGENT_WORKSPACE"]

log = logging.getLogger("sessiond-agent-client")

def _proc_starttime(pid):
    """Kernel start identity, matching A on Linux and Darwin."""
    if sys.platform == 'darwin':
        from ext.sessiond.lifecycle import identity
        actual = identity(pid)
        return int(actual['start']) if actual else None
    try:
        with open("/proc/%d/stat" % pid) as f:
            data = f.read()
    except OSError:
        return None
    # comm（第 2 字段）可含空格与括号：取最后一个 ')' 之后的部分 = 第 3..N 字段
    try:
        return int(data.rsplit(")", 1)[1].split()[22 - 3])  # 第 22 字段 → rest[19]
    except (IndexError, ValueError):
        return None


def pid_identity_ok(pid, expected_start):
    """(pid, procStart) 双件判活（同口径 agentd/proto.py pid_identity_ok，§5.5 杀纪律，
    评审 vdckko 攒批②）：pid 只是可被操作系统随时复用的编号，不是身份；身份 =
    (pid, procStart) 二元组，procStart = spawn 时记录的内核启动时刻。
    True = 身份一致（视为存活）；False = 目标已消失（/proc 不存在或 pid 被复用）。
    无记录身份（expected_start 缺失，非 Linux/遗留档案）时回退裸 kill(pid,0) 探测
    （已知取舍：不防 pid 复用，非 Linux 无更优手段，与 agentd 同口径）。"""
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        expected_start = int(expected_start)
    except (TypeError, ValueError):
        expected_start = None
    if expected_start is None: return False
    live = _proc_starttime(pid)
    return live is not None and live == expected_start


class AgentClient:
    """连接透传 socket 的任务会话监督者（接口面对齐普通客户端的桥接消费面）。"""

    CONNECT_WINDOW = 25.0    # 首次连接等待窗（对齐 wait_ready 缺省口径）
    CONNECT_RETRY = 0.3

    def __init__(self, session_path, on_event, sock_path, participant_id=None):
        # 路径处理与普通客户端同口径（兼容站内路径与已解析绝对路径）。
        if os.path.isabs(session_path) and session_path.endswith(".jsonl"):
            rp = os.path.realpath(session_path)
            roots = [S.WS_REAL, os.path.realpath(os.path.join(S.WS, "run"))]
            if any(rp == r or rp.startswith(r + os.sep) for r in roots):
                self.session_file = rp
            else:
                self.session_file = S.resolve_session_path(session_path)
        else:
            self.session_file = S.resolve_session_path(session_path)
        self.name = S.display_name(self.session_file)
        self.cwd = os.path.dirname(self.session_file)
        self.sock_path = sock_path
        # agentd 参与方路径式 id（如 task/<id>，agentd_route 载荷透传，任务 dsuqbi）：
        # 控制面代理（op=clear/reload → control/clear|restart）写请求/等回执的寻址依据。
        self.participant_id = participant_id
        self.on_event = on_event
        self.on_lost = None          # 断连回调（桥接注入：摘注册表 + 终结标记）
        self.gen = 1                 # 单世代（无重拉概念）
        self.state = "idle"          # idle/starting/running/ended
        self._cond = threading.Condition()
        self._started = False
        self._thread = None
        self._sock = None
        self._write_lock = threading.Lock()

    # ---- 启动（幂等） ----

    def ensure_started(self):
        with self._cond:
            if self._started:
                return
            self._started = True
            self._thread = threading.Thread(
                target=self._run, name="socksup-" + self.name, daemon=True)
            self._thread.start()

    def wait_ready(self, timeout=25.0):
        deadline = time.monotonic() + timeout
        with self._cond:
            while self.state not in ("running",):
                if self.state == "ended":
                    return False
                remain = deadline - time.monotonic()
                if remain <= 0:
                    return False
                self._cond.wait(min(remain, 0.5))
            return True

    def send(self, obj):
        """向会话写一条命令（单写者串行；透传给封装脚本 → pi stdin）。"""
        with self._cond:
            s = self._sock
        if s is None:
            return False
        try:
            with self._write_lock:
                s.sendall(json.dumps(obj, ensure_ascii=False).encode("utf-8")
                          + b"\n")
            return True
        except OSError:
            return False

    def status_doc(self):
        with self._cond:
            return {"session": self.name, "state": self.state,
                    "pid": None, "gen": self.gen, "session_file": self.session_file,
                    "cwd": self.cwd, "mode": "socket", "sock": self.sock_path}

    # ---- 无杀权：生命周期属 agentd runner（reload/clear 由 rpc/api.py 改道代理
    # control 动词，不落到本类；保留拒绝返回作为纵深防御，任务 dsuqbi） ----

    def reload(self, timeout=20.0):
        return {"ok": False,
                "error": "socket-mode (agentd task) session: lifecycle owned by "
                         "agentd runner; reload not available"}

    def clear(self, timeout=20.0):
        return {"ok": False,
                "error": "socket-mode (agentd task) session: lifecycle owned by "
                         "agentd runner; clear not available"}

    # ---- 连接主循环 ----

    def _run(self):
        with self._cond:
            self.state = "starting"
        sock = None
        deadline = time.monotonic() + self.CONNECT_WINDOW
        while time.monotonic() < deadline:
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.settimeout(self.CONNECT_RETRY)
                s.connect(self.sock_path)
                s.settimeout(None)
                sock = s
                break
            except OSError:
                time.sleep(self.CONNECT_RETRY)
        if sock is None:
            log.error("socket supervisor: connect failed within %.0fs: %s",
                      self.CONNECT_WINDOW, self.sock_path)
            self._finish("connect_failed")
            return
        with self._cond:
            self._sock = sock
            self.state = "running"
        log.info("socket supervisor connected: %s (session=%s)",
                 self.sock_path, self.name)
        buf = b""
        while True:
            try:
                d = sock.recv(262144)
            except OSError:
                break
            if not d:
                break
            buf += d
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if len(line) > S.LINE_LIMIT:
                    log.warning("socket line exceeds %d bytes; dropping",
                                S.LINE_LIMIT)
                    continue
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                self._broadcast(obj)
        try:
            sock.close()
        except OSError:
            pass
        self._finish("disconnected")

    def _finish(self, reason):
        with self._cond:
            self.state = "ended"
            self._sock = None
        log.info("socket supervisor ended (%s): %s", reason, self.name)
        # 先入环广播再摘桥：订阅者能看到终态帧，之后流自然终结（前端重连后
        # 重新核验登记——终态任务拒绝普通复活）。
        self._broadcast({"type": "agentd.task_ended", "session": self.name,
                         "reason": reason})
        if self.on_lost is not None:
            try:
                self.on_lost()
            except Exception:
                log.exception("on_lost failed")

    def _broadcast(self, obj):
        try:
            self.on_event(obj)
        except Exception:
            log.exception("on_event failed")


# ---- agentd 登记会话路由判定（任务 ybzvbn 泛化，去保活二期 ja0vr7） ----
#
# 打开 /agents/(task|bot)/<名>/session/session.jsonl 时按 pid.json 判路由；
# 用户硬约束（2026-09-03）：凡 agentd 登记者（有 spec.json）web/sessiond **零启动**——
# 只桥接透传、永不 spawn，三态均不启动普通宿主：
#   活 socket 在场（sock 字段 ∧ (pid,procStart) 身份一致 ∧ 可连） → AgentClient 直播
#   已终态（final）                              → 拒绝并指引（生命周期归 agentd）
#   缺席（无 pid.json ∨ 进程身份消失 ∧ 无 sock）  → 等待提示（拉起权单点 = agentd）
#   sock 在场但进程身份消失（封装已退出未落终态） → 拒绝并等待（等终态落盘/自动拉起）
#   进程活但无 sock（旧形态运行中）              → 拒绝（双宿主风险，协议 §11.12）
#   status=paused                                → 拒绝并提示 control restart
# 跨机：spec.host ≠ 本机规范名 → 拒绝 + 指引。
# 注：路径命中但目录下无 spec.json（如测试/临时目录）时仍按登记会话口径三态判定，
# 不回落裸 jsonl 懒拉起（裸 jsonl 废除，.agent 才是唯一拉起入口）。

_AGENTD_SESSION_RE = re.compile(
    r"^/agents/(task|bot)/([^/]+)/session/session\.jsonl$")

# spec.json 声明者入口（任务 60grqq，用户拍板口径收紧）：agents/ 下会话的 ?v=chat
# 入口唯一 = spec.json 声明者路径；会话产物路径（session/session.jsonl）是数据，
# 不再支持当 URL 入口直开。
_AGENTD_SPEC_RE = re.compile(
    r"^/agents/(task|bot)/([^/]+)/spec\.json$")


def _site_path(session_path):
    """站内路径归一：绝对路径还原为站内形式（/…），其余原样。"""
    p = session_path
    if isinstance(p, str) and os.path.isabs(p):
        real = os.path.realpath(p)
        if real == S.WS_REAL or real.startswith(S.WS_REAL + os.sep):
            p = "/" + os.path.relpath(real, S.WS_REAL)
    return posixpath.normpath(p) if isinstance(p, str) else ""


def _sock_live(path, timeout=2.0):
    """unix socket 可连探测（路由判定用）。"""
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            s.connect(path)
            return True
        finally:
            s.close()
    except OSError:
        return False


def agentd_route(session_path):
    """agentd 登记会话路由判定（task/bot 两族，去保活二期 ja0vr7）。
    返回 None（非登记会话路径，零影响）或 (mode, payload)：
      ("live",   {"sock": <透传端点>, "participant_id": <族/名>}) → AgentClient 直播
      ("reject", <错误消息>)   → 终态/缺席等待/暂停/跨机/通道未就绪/封装已退出等终态/
                                  旧形态运行中双宿主（调用方抛 ValueError；
                                  登记者一律不 spawn，硬约束②）
    """
    p = _site_path(session_path)
    m = _AGENTD_SESSION_RE.match(p)
    if not m:
        return None
    family, name = m.group(1), m.group(2)
    participant_id = "%s/%s" % (family, name)
    adir = os.path.join(S.WS, "agents", family, name)
    spec = agent_metadata(agent_workspace(), participant_id)
    spec_absent = spec is None or not isinstance(spec, dict)
    spec = spec if isinstance(spec, dict) else {}
    # 跨机守卫（指引用户去宿主机的服务）
    host = spec.get("host")
    if isinstance(host, str) and host.strip():
        me = S._self_host()
        if me is None:
            return ("reject",
                    "host guard: env/host-id 缺失或无本机映射，无法判定会话宿主，"
                    "拒绝打开（修复 env/host-id 后重试）")
        if host.strip() != me:
            return ("reject",
                    "该会话宿主=%s，请通过 %s 的服务访问（本机=%s）"
                    % (host.strip(), host.strip(), me))
    doc = agent_state(agent_workspace(), participant_id)
    if not isinstance(doc, dict):
        doc = None
    # 终态 → 拒绝并指引（生命周期归 agentd：换代 = control restart，web 不代拉）
    if doc is not None and doc.get("final") is True:
        return ("reject",
                "会话已终态（agentd 已收口 %s），不可打开；如需续用请经 agentd "
                "新建 Task 或重启 Service" % participant_id)
    if doc is None:
        if spec_absent:
            return ("reject",
                    "未登记（无 spec.json）：bot 族登记走命令行（agentctl bot register "
                    "写 spec.json、由 serviced 接受执行声明，执行归 "
                    "Owner），web 侧无登记入口；task 族走 dispatch（%s）"
                    % participant_id)
        return ("reject",
                "会话进程缺席（%s 尚无运行档案）：拉起权单点 = agentd，"
                "请等待 agentd 放行拉起后重试" % participant_id)
    if doc.get("status") == "paused":
        return ("reject",
                "该会话已暂停（%s），可经 agentd control restart 恢复"
                % participant_id)
    sock = doc.get("sock")
    # 判活 = (pid, procStart) 双件口径（评审 vdckko 攒批②）：裸 pid 探测有 pid 复用误判面。
    pid_ok = pid_identity_ok(doc.get("pid"), doc.get("procStart"))
    if isinstance(sock, str) and sock:
        if pid_ok:
            if _sock_live(sock):
                return ("live", {"sock": sock, "participant_id": participant_id})
            return ("reject",
                    "会话观测通道未就绪（启动中或封装进程异常），请稍后重试：%s" % sock)
        # sock 在场但进程身份已消失：封装进程已退出而 runner 尚未落终态（评审 vdckko 攒批③：
        # 旧 catch-all 文案对此场景不准）。等待终态落盘；终态后如需续用经 新建 Task 或重启 Service。
        return ("reject",
                "封装进程已退出，等待 serviced 落终态（%s）：收敛中，请稍候重试"
                "（restartPolicy=auto 会自动拉起；若已终态，续用经 agentd 新建 Task 或重启 Service）"
                % participant_id)
    if pid_ok:
        # 进程存活但无 sock = 旧形态运行中：拒绝（双宿主风险，协议 §11.12）。
        return ("reject",
                "旧形态运行中会话不可打开（%s）：打开会与任务进程双宿主写同一 jsonl；"
                "实时观测需新形态（pi-rpc-wrap）" % participant_id)
    return ("reject",
            "会话进程缺席（%s，无活跃观测通道）：拉起/复活归 agentd 监督"
            "（restartPolicy=auto 会自动拉起），请稍候重试" % participant_id)


def agentd_spec_workdir(participant_id):
    spec = agent_metadata(agent_workspace(), participant_id)
    return spec.get('workdir') if spec else None


def agentd_entry(session_path):
    """agentd 族会话入口归一（任务 60grqq，用户拍板：入口唯一化）。
    HTTP 请求入口层前置（rpc/api.py + rpc/stream.py 两处单点调用）：
      /agents/(task|bot)/<名>/spec.json      → 归一为 session/session.jsonl 站内路径，
                                               其后链路（get_bridge → agentd_route 三态）
                                               与旧直开完全同一代码路径，零新路由/零新 spawn 面
      /agents/(task|bot)/<名>/session/session.jsonl 直开 → 错误消息（调用方回 400；
                                               产物路径是数据不是入口，视图内部解析目标仍是它）
      其余路径                                → 原样放行（.agent/普通 .jsonl 口径不变）
    返回 (归一路径, None) 或 (None, 错误消息)。"""
    p = _site_path(session_path)
    m = _AGENTD_SPEC_RE.match(p)
    if m:
        return "/agents/%s/%s/session/session.jsonl" % (m.group(1), m.group(2)), None
    m = _AGENTD_SESSION_RE.match(p)
    if m:
        return None, (
            "直开会话产物路径已废除（任务 60grqq）：agentd 登记会话的入口 = spec.json "
            "声明者路径（/agents/%s/%s/spec.json?v=chat）；session.jsonl 是数据，不是入口"
            % (m.group(1), m.group(2)))
    return session_path, None


def _control_result(doc, status="200 OK"):
    return doc, status


def control(b, action, reason):
    import subprocess
    participant = getattr(b.sup, 'participant_id', None)
    if not participant: return _control_result({'ok': False, 'error': 'No agent participant'}, '400 Bad Request')
    from execution import desired, service_action, clear_service_session
    try:
        if action == 'clear': clear_service_session(agent_workspace(), participant)
        elif action == 'stop': desired(agent_workspace(), participant, 'cancel')
        elif action == 'restart':
            service_action(agent_workspace(), participant, 'stop')
            service_action(agent_workspace(), participant, 'start')
        else: raise ValueError('Unsupported action: ' + action)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        return _control_result({'ok': False, 'error': str(exc)}, '409 Conflict')
    return _control_result({'ok': True, 'session': b.session, 'outcome': 'requested'})
