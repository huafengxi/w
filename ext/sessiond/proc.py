# -*- type=script -*-
# proc.py — 按路径会话监督员（任务 0829-1958-od0t；R1 路径路由；R2 零文件）。
#
# web 进程内的守护线程按会话路径各监督一个 `pi --mode rpc` 子进程（stdin/stdout
# 管道，进程内直连）。监督口径沿用单会话版（0829-1854-vkbv，原 sessiond
# 0829-1327-8n06）：崩溃退避 2^n（上限 30s）、熔断（窗口内连续崩溃超限）、
# 稳定期重置计数；会话进程 `start_new_session=True`（独立会话组，web 被整组杀
# 时不连带杀会话）。
#
# URL 路径路由（用户拍板 inform p4th）：会话 = ~/m 下任意 `.jsonl` 路径，
# `/assistant/<name>.jsonl?v=chat`、`/foo/bar/x.jsonl?v=chat` 均可；不同目录
# 的同名文件 = 不同会话，可并存。路径校验锁定 ~/m 内（防穿越/逃逸）。
# 会话工作目录（任务 fw2ll1 cwd/dir 拆分）：缺省 = 其 jsonl 所在目录（.jsonl 直开，
# 用户拍板 inform cwd1）；.agent 会话由调用方（rpc/api.py op=agent）显式传入，
# 可与会话文件所在目录不同（cwd 决定 pi 加载哪个工作区的 AGENTS/扩展）。
#
# 零文件（用户拍板 inform n0fx/p4id/s4fn）：除会话 jsonl 本身外不引入任何
# 辅助文件——无账本、无进程 stderr 文件。
#   - 子进程 stderr 继承 web 进程（并入 run/logs/web.log）。
#   - 旧宿主识别 = environ 标记扫描 + 宿主身份交叉校验：spawn 注入
#     `SESSIOND_SESSION_FILE=<jsonl 绝对路径>`；处置旧宿主时扫
#     /proc/*/environ 精确匹配（用户 ask 拍板：pi 会 setproctitle 把 argv
#     重写为裸 "pi"，cmdline 匹配不可行），且命中进程还须 comm=='pi'（改写后
#     title）且 /proc/<pid>/exe 指向 node 二进制——仅凭 environ 标记会把带标记的
#     旁观进程误杀（2026-08-31 scheduler 误杀事故；纵深防御任务 p0h6fk，口径经
#     dispatcher inform p6fk/p6f2 两轮修正定稿）。命中即旧宿主（无 starttime 校验，
#     s4fn）：等 stdin-EOF 自然退出，宽限后 SIGTERM→SIGKILL。
# `dispatcher` 名允许（用户拍板 0829-1958-od0t）。
#
# web 重启行为（替代 fd handoff）：pi rpc 模式 stdin EOF 即优雅退出（会话持久体
# 已 flush）→ web 死后会话自行退出；新 web 首次访问某会话时按 environ 标记扫
# 出仍存的旧宿主，等其自然退出（宽限后杀之——防同一 jsonl 双宿主），随后从
# 该 .jsonl resume 重拉。聊天历史不丢。仅 python3 标准库。
import grp
import json
import logging
import os
import posixpath
import re
import shutil
import signal
import socket
import subprocess
import threading
import time

log = logging.getLogger("sessiond-proc")

WS = os.path.expanduser("~/m")
WS_REAL = os.path.realpath(WS)

# 子进程 environ 标记（零文件旧宿主识别，R2）
HOST_MARKER = "SESSIOND_SESSION_FILE"

# 探针扩展（任务 s0f1la）：对所有会话子进程注入 `-e`，提供内部命令
# `sessiond-inspect`（系统提示词+工具清单转储，供 op=inspect 编排；见同目录
# probe.ts 与 bridge.py:inspect）。路径运行期解析（= 本文件所在目录），勿硬编码。
# 容错口径（实测）：pi 对 -e 扩展的**加载（语法）错误是致命的**（启动直接退出，
# 与自动发现扩展的 errors 收集不同）；运行期异常（命令/钩子）只发 extension_error
# 不崩进程。故探针保持极小面（只注册一个命令、无钩子），_spawn 只在文件存在时
# 注入（文件丢失不拖垮会话）。语法回归由发版前的会话实测闸门拦截。
PROBE_EXT = os.path.join(os.path.dirname(os.path.realpath(__file__)), "probe.ts")


def resolve_session_path(path):
    """会话路径校验与解析（路径安全红线）：
    - 必须 `/` 开头的站内路径、`.jsonl` 后缀；
    - normpath 折叠 `..`/`.` 后与 ~/m 拼接，再 realpath 前缀校验（双保险，
      防符号链接逃逸；白名单根 = ~/m + run 运行时区）；
    返回解析后的绝对路径；非法抛 ValueError（调用方回 400）。"""
    if not isinstance(path, str) or "\x00" in path:
        raise ValueError("invalid session path %r" % (path,))
    if not path.startswith("/"):
        raise ValueError("session path must be absolute (start with '/')")
    p = posixpath.normpath(path)
    if not p.endswith(".jsonl") or p == "/" or p.endswith("/"):
        raise ValueError("session path must end with .jsonl: %r" % (path,))
    real = os.path.realpath(os.path.join(WS_REAL, p[1:]))
    roots = [WS_REAL, os.path.realpath(os.path.join(WS, "run"))]
    if not any(real == r or real.startswith(r + os.sep) for r in roots):
        raise ValueError("session path escapes workspace: %r" % (path,))
    return real


def display_name(real_path):
    """会话展示名 / pi --name：basename 去 .jsonl 后缀。"""
    return os.path.basename(real_path)[:-len(".jsonl")]


def declared_display_name(session_file):
    """.agent 声明者指定的展示名（bot 布局第 4 期，任务 ocfidc，plan §4.10
    显示名退路）：声明者带非空 `name` 字段且推导会话文件 == 目标 → 返回该名字；
    否则 None（回落 display_name 的 basename 规则）。对外会话标识（如
    `bot/dev-dispatcher`）由此带族前缀，与会话文件所在族目录口径一致；
    pi --name 是显示名，不受 session id 字符约束。"""
    for _name, _host, derived, spec in _scan_agent_declarations():
        if derived != session_file:
            continue
        declared = spec.get("name")
        if isinstance(declared, str) and declared.strip():
            return declared.strip()
        return None
    return None


def resolve_cwd(cwd):
    """显式 cwd 校验（任务 fw2ll1）：与会话路径同一根集（~/m + run 运行时区），
    realpath 前缀校验防穿越/符号链接逃逸；非法抛 ValueError（调用方回 400）。
    返回解析后的绝对路径（目录可以尚不存在，_spawn 时 makedirs）。"""
    if not isinstance(cwd, str) or not cwd.strip() or "\x00" in cwd:
        raise ValueError("invalid cwd %r" % (cwd,))
    real = os.path.realpath(os.path.expanduser(cwd.strip()))
    roots = [WS_REAL, os.path.realpath(os.path.join(WS, "run"))]
    if not any(real == r or real.startswith(r + os.sep) for r in roots):
        raise ValueError("cwd escapes workspace: %r" % (cwd,))
    return real


BACKOFF_MAX = 30.0          # 崩溃重启退避上限（秒）
FAIL_LIMIT = 6              # 熔断：窗口内连续崩溃次数上限
FAIL_WINDOW = 300.0         # 熔断窗口（秒）
STABLE_RESET = 120.0        # 存活超过该秒数 → 重置崩溃计数
LINE_LIMIT = 16 * 1024 * 1024  # 单行 JSONL 上限，超限丢弃该行（口径沿用）
ORPHAN_GRACE = 15.0         # 等待旧宿主自然退出的宽限（秒）

# 剥除调度/任务身份与上游会话身份（PI_* 经 make 链泄漏会投毒子进程的会话归属，
# 实测案例：任务环境 PI_SESSION_FILE 漏进会话进程；p0h6fk 起整族 PI_ 前缀剥除，
# 口径同 svc/svc.py）。
ENV_SCRUB_EXACT = {"AGENTD_TASK", "AGENT_SELF", "AGENT_HOME", "AGENT_ROOT",
                   "DISPATCH_TASK_ID"}
ENV_SCRUB_PREFIXES = ("DISPATCH_TASK_", "PI_")


def clean_env():
    """剥除调度/任务身份环境变量（口径同 svc/svc.py clean_env）。"""
    return {k: v for k, v in os.environ.items()
            if k not in ENV_SCRUB_EXACT
            and not any(k.startswith(p) for p in ENV_SCRUB_PREFIXES)}


def _claim_origin(path):
    """去 replica 标记、升格本机原件（任务 4l3de8）：agents-sync 属组闸门下，
    replica 组文件不受 pull 黑名单保护——本机若把它当活文件继续写，会被远端旧副本
    随时覆盖（且本机改动也不会进 push 白名单）。宿主 = 单写者，认领会话文件时若发现
    它是 replica（被 pull 落盘的副本），chown 回本进程 uid/gid 去标记升格为本机原件，
    此后 push 会把本机内容收敛到远端。本机无 `replica` 组（grp.getgrnam KeyError）或
    文件不存在/非 replica → 跳过。"""
    try:
        gr = grp.getgrnam("replica")
    except KeyError:
        return
    try:
        st = os.stat(path)
    except OSError:
        return
    if st.st_gid != gr.gr_gid:
        return
    try:
        os.chown(path, os.getuid(), os.getgid())
        log.info("claimed origin (dropped replica tag) for %s", path)
    except OSError as e:
        log.error("claim origin failed for %s: %s", path, e)


# ---- 跨宿主守卫（任务 8nherl，最小版）----
# .agent 声明 host 的会话只能被声明机实体化（建桥接/拉起进程）。唯一守卫点 =
# Supervisor.__init__（桥接创建即监督员创建，是所有会话进程在本机被拉起的必经处，
# 见 bridge.get_bridge 单一建桥入口）。判定口径与扩展侧/replica-tag 同源：
# 声明者扫描面 = assistant/** 递归（唯一约定位置），推导会话文件 =
# <sessionDir realpath>/<agent名>.jsonl（sessionDir 缺省 = .agent 所在目录），
# 与 rpc/api.py _resolve_agent 的推导口径一致。本机身份 = env/host-id 查表；
# 缺失/未命中 = 配置错误，对命中声明者会话拒绝放行（不猜测），无声明者会话零波及。


def _scan_agent_declarations():
    """递归扫 assistant/**/*.agent，yield (agent名, host, 推导会话文件 realpath, spec)。
    坏文件/缺 host 宽容跳过（守卫只拦可判定的声明者）。"""
    for dirpath, _dirnames, filenames in os.walk(os.path.join(WS, "assistant")):
        for fn in filenames:
            if not fn.endswith(".agent"):
                continue
            try:
                with open(os.path.join(dirpath, fn)) as f:
                    spec = json.load(f)
            except (OSError, ValueError):
                continue
            if not isinstance(spec, dict):
                continue
            host = spec.get("host")
            if not isinstance(host, str) or not host.strip():
                continue
            name = fn[:-len(".agent")]
            sd = spec.get("sessionDir")
            base = (os.path.realpath(os.path.expanduser(sd.strip()))
                    if isinstance(sd, str) and sd.strip() else dirpath)
            yield name, host.strip(), os.path.realpath(
                os.path.join(base, name + ".jsonl")), spec


def _self_host():
    """本机规范名：env/host-id 查表（格式同 agentd/agentctl.py
    local_canonical_host：每行 `<hostname> <空白> <规范名>`，# 注释）。
    文件缺失/不可读/本机 hostname 未命中 → None（调用方报配置错误，不猜测）。"""
    try:
        with open(os.path.join(WS, "env", "host-id")) as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    hn = socket.gethostname()
    for line in lines:
        parts = line.strip().split()
        if len(parts) > 1 and parts[0] == hn and not parts[0].startswith("#"):
            return parts[1]
    return None


def host_guard(session_file):
    """跨宿主守卫（唯一守卫点）：目标会话文件若命中某 .agent 声明者推导的会话文件，
    且声明者 host ≠ 本机 → ValueError 拒绝（消息含声明机访问指引）；命中声明者但本机
    身份不可得 → ValueError 配置错误；无声明者（普通会话）→ 放行，零影响。"""
    for name, host, derived, _spec in _scan_agent_declarations():
        if derived != session_file:
            continue
        me = _self_host()
        if me is None:
            raise ValueError(
                "host guard: env/host-id missing or no entry for this hostname; "
                "cannot determine local host, refusing to materialize session %r "
                "(declared host=%s). Fix env/host-id." % (name, host))
        if host != me:
            raise ValueError(
                "该会话声明者宿主=%s，请通过 %s 的服务访问（本机=%s）"
                % (host, host, me))
        return


def pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _proc_starttime(pid):
    """读 /proc/<pid>/stat 第 22 字段 starttime（自开机时钟节拍，不受系统时钟调整影响）。
    同口径 agentd/proto.py proc_starttime（§5.5 杀纪律）：非 Linux/进程不存在/读解析失败 → None。"""
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
    if expected_start is None:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    live = _proc_starttime(pid)
    return live is not None and live == expected_start


def is_pi_host(pid):
    """宿主身份交叉校验（纵深防御，任务 p0h6fk）：environ 标记单独不构成宿主证据——
    任何旁观进程只要 environ 带标记（如从带标记 shell 继承启动的 scheduler，
    2026-08-31 事故）就会被误杀。真宿主还须同时满足：
      ① comm == 'pi'（pi setproctitle 把 argv 重写为裸 'pi'，cmdline 不可用，
         但 /proc/<pid>/comm 反映改写后 title）；
      ② readlink /proc/<pid>/exe 指向 node 二进制（pi 是 node CLI）。
    任一读取失败 → 拒绝认宿主（宁可漏杀不可误杀；合法孤儿宿主是 web 亲自
    拉起的同用户进程，两项读取必然成功）。"""
    try:
        with open("/proc/%d/comm" % pid, "rb") as f:
            if f.read().strip() != b"pi":
                return False
        exe = os.readlink("/proc/%d/exe" % pid)
        return os.path.basename(exe) == "node"
    except OSError:
        return False


def find_hosts(session_file):
    """零文件旧宿主识别（R2）：扫 /proc/*/environ，精确匹配
    `SESSIOND_SESSION_FILE=<session_file>` 标记、且经 is_pi_host 交叉校验的进程
    → pid 列表。带标记的旁观进程（comm != 'pi' 或 exe 非 node）一律不认宿主。"""
    marker = ("%s=%s" % (HOST_MARKER, session_file)).encode("utf-8")
    hosts = []
    self_pid = os.getpid()
    try:
        entries = os.listdir("/proc")
    except OSError:
        return hosts
    for entry in entries:
        if not entry.isdigit():
            continue
        pid = int(entry)
        if pid == self_pid:
            continue
        try:
            with open("/proc/%s/environ" % entry, "rb") as f:
                env = f.read()
        except OSError:
            continue
        if marker in env.split(b"\x00") and is_pi_host(pid):
            hosts.append(pid)
    return hosts


def pi_binary():
    return shutil.which("pi") or "pi"


class Supervisor:
    """按路径会话监督员：一个 `pi --mode rpc` 子进程 + 崩溃恢复。

    session_path = 站内 URL 路径（如 `/assistant/foo.jsonl`）或已解析绝对路径。
    on_event(obj) 由监督线程回调（进程退出/重拉等生命周期帧 + pi 原始输出行），
    调用方（bridge）负责入环/多播。
    """

    def __init__(self, session_path, on_event, cwd=None):
        # 兼容站内路径（如 `/assistant/foo.jsonl`）与已解析绝对路径传入。
        if os.path.isabs(session_path) and session_path.endswith(".jsonl"):
            rp = os.path.realpath(session_path)
            roots = [WS_REAL, os.path.realpath(os.path.join(WS, "run"))]
            if any(rp == r or rp.startswith(r + os.sep) for r in roots):
                self.session_file = rp
            else:
                self.session_file = resolve_session_path(session_path)
        else:
            self.session_file = resolve_session_path(session_path)
        self.name = (declared_display_name(self.session_file)
                     or display_name(self.session_file))
        host_guard(self.session_file)   # 跨宿主守卫唯一守卫点（任务 8nherl）
        # cwd 由调用方显式传入（任务 fw2ll1：.agent cwd/dir 拆分，经 bridge 转交）；
        # 缺省 = jsonl 所在目录（.jsonl 直开路径行为不变，cwd1）。
        self.cwd = resolve_cwd(cwd) if cwd else os.path.dirname(self.session_file)
        self.on_event = on_event
        self.proc = None
        self.pid = None
        self.gen = 0
        self.restarts = 0
        self.state = "idle"         # idle/starting/running/restarting/disabled/dormant
        self.disabled = False       # 熔断标记（reload 解除）
        self._reaping = False       # 空闲回收杀：退出不计崩溃、转懒态不重拉（去保活，任务 ja0vr7）
        self._reloading = False     # 主动 reload：退出不计崩溃/不排退避
        self._clearing = False      # 主动 clear（0829-2238-atnj）：重拉前先把 jsonl 截断到 0
        self._clear_error = None    # clear 截断文件失败信息（由监督循环回填）
        self._crash_ts = []
        self._spawned_at = None
        self._stdin_lock = threading.Lock()
        self._cond = threading.Condition()
        self._started = False
        self._thread = None
        # 在场判定钩子（去保活，任务 ja0vr7）：由 bridge 注入 has_presence；
        # None = 保守按有在场（无桥接消费方时行为与旧版逐字一致）。
        self.presence_check = None

    # ---- 启动（幂等，首次调用拉起监督线程） ----

    def ensure_started(self):
        with self._cond:
            thread_alive = (self._thread is not None
                            and self._thread.is_alive())
            if self._started and thread_alive:
                return
            if self._started:
                # 监督循环已退出：熔断态（disabled）须经 reload 解锁，不在这里复活；
                # 懒态（dormant，去保活转态）→ 重起监督循环即复活，语义回到懒拉起。
                if self.disabled:
                    return
            self._started = True
            self._thread = threading.Thread(
                target=self._run_loop,
                name="supervisor-" + self.name, daemon=True)
            self._thread.start()

    def _has_presence(self):
        """在场判定（去保活，任务 ja0vr7）：桥接提供数据（SSE 订阅数>0 ∨ 距最近一次
        attach/cmd/reload/clear 活动 < PRESENCE_GRACE）。钩子缺失/异常 → 保守按有在场。"""
        fn = self.presence_check
        if fn is None:
            return True
        try:
            return bool(fn())
        except Exception:
            return True

    def _go_dormant(self, reason):
        """转懒态（去保活）：广播休眠帧、置 dormant、监督循环随后退出。
        复活路径 = ensure_started（下次访问自然拉起）。"""
        with self._cond:
            self.proc = None
            self.pid = None
            self.state = "dormant"
        log.info("session dormant (%s): %s file=%s", reason, self.name,
                 self.session_file)
        self._broadcast({"type": "sessiond.session_dormant",
                         "session": self.name, "reason": reason})

    def _idle_reap(self):
        """空闲回收执行体（去保活，任务 ja0vr7）：同 _kill_and_restart 的优雅杀但**不重拉**——
        置 _reaping 标志后杀进程，监督循环观察到退出即转懒态（不计崩溃）。"""
        with self._cond:
            if self.state != "running":
                return
            self._reaping = True
            p = self.proc
        log.info("idle reap: killing %s (no subscribers, idle beyond timeout)",
                 self.name)
        if p is not None and p.poll() is None:
            try:
                p.terminate()
            except OSError:
                pass
            try:
                p.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                try:
                    p.kill()
                except OSError:
                    pass

    # ---- 对外 ----

    def wait_ready(self, timeout=25.0):
        """等待会话进程就位（state=running）。首次访问时监督线程刚拉起，
        spawn 需毫秒~秒级（+ 旧宿主清场宽限）；基线路径等待而非快速失败。
        上限取值（0830-0956-vk20 毛刺3）：清场旧宿主最坏 = ORPHAN_GRACE 15s
        自然退出宽限 + SIGTERM 宽限 3s（+SIGKILL）+ spawn；原 10s 覆盖不住→
        首个 attach 504，放宽到 25 = 15+3+7（余量）。"""
        deadline = time.monotonic() + timeout
        with self._cond:
            while self.state != "running" or self.proc is None:
                if self.disabled:
                    return False
                remain = deadline - time.monotonic()
                if remain <= 0:
                    return False
                self._cond.wait(min(remain, 0.5))
            return True

    def send(self, obj):
        """向会话进程写一条命令（监督员是唯一写者 → 天然串行化）。"""
        p = self.proc
        if p is None or p.stdin is None:
            return False
        try:
            with self._stdin_lock:
                p.stdin.write(json.dumps(obj, ensure_ascii=False).encode("utf-8")
                              + b"\n")
                p.stdin.flush()
            return True
        except (OSError, ValueError):
            return False

    def _kill_and_restart(self, extra_flags, timeout):
        """杀当前会话进程（SIGTERM 宽限→SIGKILL，经自有 Popen 句柄，无 pid
        复用风险；不计崩溃/不排退避）并等监督循环重拉；返回 {ok, gen, pid}。
        extra_flags 在杀前随锁置位（如 _reloading/_clearing），监督循环据此决定
        重拉前是否截断 jsonl。reload/clear 共用。"""
        with self._cond:
            p = self.proc
            gen0 = self.gen               # 先记世代：重拉极快，杀后读会读到新值导致永等
            self._reloading = True
            for k in extra_flags:
                setattr(self, k, True)
            self.disabled = False           # 熔断也经 reload 解除（口径沿用）
            self._crash_ts = []
            loop_alive = self._thread is not None and self._thread.is_alive()
        if not loop_alive:
            # 监督循环已退出（熔断态）：重起循环负责重拉
            self._thread = threading.Thread(
                target=self._run_loop,
                name="supervisor-" + self.name, daemon=True)
            self._thread.start()
        if p is not None and p.poll() is None:
            try:
                p.terminate()
            except OSError:
                pass
            try:
                p.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                try:
                    p.kill()
                except OSError:
                    pass
        # 等监督循环完成重拉（gen 变化即新进程就位）
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._cond:
                if self.gen != gen0 and self.state == "running":
                    return {"ok": True, "gen": self.gen, "pid": self.pid,
                            "clear_error": self._clear_error}
            time.sleep(0.2)
        return {"ok": False, "error": "restart timed out waiting for respawn"}

    def reload(self, timeout=20.0):
        """进程级 reload：优雅停（同 _kill_and_restart）→ 监督循环立即从该
        .jsonl resume 重拉（不排退避，不计崩溃）。"""
        return self._kill_and_restart((), timeout)

    def clear(self, timeout=20.0):
        """/clear（0829-2238-atnj；4l3de8 翻案改截断）：保留会话路径、清空全部内容。
        顺序 = ① 杀当前会话进程（同 reload 口径）→ ② 把该会话 jsonl **截断到 0**
        （不存在则创建空文件）并去 replica 标记（监督循环内、旧进程死透后、重拉前执行，
        同线程无竞态，防旧进程把内存历史回写）→ ③ 立即重拉（pi≥0.84.1 对已存在的空文件走
        _setSessionFile size==0 分支：newSession + _rewriteFile('w') + flushed=true，
        不碰 openSync(wx)，安全）。翻案：旧口径「必须删除而非截断（pdop EEXIST）」是旧版
        pi 的踩坑，已失效——删除在跨机同步树下有复活竞态：agents-sync 不开 --delete，
        远端旧副本会被 pull 秒级拉回（带 replica 组）→ pi 判新会话首写 wx 撞 EEXIST
        （2026-08-31 mac-dispatcher 事故实证）。截断 + 去标记后本机升格原件，push 收敛
        远端 replica，无竞态。environ 标记 SESSIOND_SESSION_FILE 不变（路径不变）。
        返回 {ok, gen, pid}。"""
        with self._cond:
            self._clear_error = None
        r = self._kill_and_restart(("_clearing",), timeout)
        if r.get("ok") and r.get("clear_error"):
            with self._cond:
                self._clear_error = None
            return {"ok": False,
                    "error": "session respawned but jsonl truncate failed: %s"
                             % r["clear_error"]}
        if r.get("ok"):
            return {"ok": True, "gen": r["gen"], "pid": r["pid"]}
        return r

    def status_doc(self):
        with self._cond:
            return {"session": self.name, "state": self.state,
                    "pid": self.pid, "gen": self.gen,
                    "restarts": self.restarts, "disabled": self.disabled,
                    "session_file": self.session_file, "cwd": self.cwd}

    # ---- 监督主循环 ----

    def _run_loop(self):
        self._clear_stale_proc()        # 首次拉起：清场旧宿主（防双宿主）
        while True:
            self._apply_clear()         # clear：重拉前先把 jsonl 截断到 0（旧进程已死透）
            try:
                self._spawn()
            except Exception:
                log.exception("spawn failed")
                if not self._has_presence():   # 去保活：无人看时不空转重拉，转懒态
                    self._go_dormant("spawn failed, no presence")
                    return
                self._broadcast({"type": "sessiond.session_restarting",
                                 "session": self.name,
                                 "delay": BACKOFF_MAX})
                time.sleep(BACKOFF_MAX)
                continue
            rc = self.proc.wait()
            with self._cond:
                reloading = self._reloading
                self._reloading = False
                reaping = self._reaping
                self._reaping = False
                self.proc = None
                self.pid = None
                self.state = "restarting"
            log.warning("pi process gone rc=%s gen=%d session=%s file=%s",
                        rc, self.gen, self.name, self.session_file)
            if reloading:
                continue                # 主动 reload：立即重拉（无退避）
            if reaping:
                self._go_dormant("idle-reaped")   # 空闲回收：转懒态不重拉
                return
            if not self._has_presence():
                # 去保活核心（du44uj §1.2.2）：崩溃时无在场订阅 → 不重拉转懒态
                #（浏览器重开经 ensure_started 复活）；有在场 → 下方退避重拉/熔断照旧。
                self._go_dormant("crashed without subscribers")
                return
            now = time.monotonic()
            self._crash_ts = [t for t in self._crash_ts
                              if now - t <= FAIL_WINDOW]
            self._crash_ts.append(now)
            self.restarts += 1
            if len(self._crash_ts) > FAIL_LIMIT:
                with self._cond:
                    self.disabled = True
                    self.state = "disabled"
                log.error("circuit breaker: %d crashes in %.0fs window (%s)",
                          len(self._crash_ts), FAIL_WINDOW, self.name)
                self._broadcast({"type": "sessiond.session_disabled",
                                 "session": self.name})
                return                  # 熔断：停监督（经 reload 重启恢复）
            delay = min(BACKOFF_MAX, 2.0 ** max(0, len(self._crash_ts) - 1))
            self._broadcast({"type": "sessiond.session_restarting",
                             "session": self.name, "delay": delay})
            log.info("respawn in %.1fs (resume from %s)", delay,
                     self.session_file)
            time.sleep(delay)

    def _apply_clear(self):
        """clear 落地（0829-2238-atnj；4l3de8 翻案改截断）：监督循环内、_spawn 之前
        执行——此刻旧进程必然已死透（proc.wait() 已返回或崩溃退避刚结束），截断 jsonl
        与重拉同线程，无竞态。落地 = 「确保文件存在、size==0、非 replica」：不存在 →
        创建空文件；存在 → truncate(0)；随后 _claim_origin 去 replica 标记升格本机原件。
        翻案：旧口径「必须删除而非截断（pi 懒落盘 openSync(wx) 撞 EEXIST，pdop 踩坑）」
        针对旧版 pi，已失效——pi≥0.84.1 对已存在空文件走 _setSessionFile size==0 分支
        （newSession + _rewriteFile(openSync 'w') + flushed=true），不碰 wx。而删除在跨机
        同步树下有复活竞态：agents-sync 不开 --delete，pull 会把远端旧副本秒级拉回 →
        pi 判新会话首写 wx 撞复活的旧文件 EEXIST（2026-08-31 mac-dispatcher 事故实证）。
        截断 + 升格原件后，本机写入随 push 收敛远端，无竞态。"""
        with self._cond:
            if not self._clearing:
                return
            self._clearing = False
        try:
            if os.path.exists(self.session_file):
                with open(self.session_file, "r+") as f:
                    f.truncate(0)
                log.info("clear: truncated session file %s to 0 before respawn",
                         self.session_file)
            else:
                open(self.session_file, "a").close()
                log.info("clear: created empty session file %s before respawn",
                         self.session_file)
            _claim_origin(self.session_file)
        except OSError as e:
            log.error("clear: failed to truncate %s: %s", self.session_file, e)
            with self._cond:
                self._clear_error = str(e)

    def _clear_stale_proc(self):
        """防双宿主清场（R2 零文件）：按 environ 标记 + is_pi_host 交叉校验扫出同一 jsonl 的旧宿主
        （典型来源：旧 web 的孤儿会话，其 stdin 已随旧 web 死亡 → pi 因 EOF
        自行优雅退出）；等待自然退出，宽限后 SIGTERM→SIGKILL。"""
        hosts = [p for p in find_hosts(self.session_file) if pid_alive(p)]
        if not hosts:
            return
        log.warning("stale host(s) %s for %s; waiting %.0fs for graceful "
                    "EOF exit", hosts, self.session_file, ORPHAN_GRACE)
        deadline = time.monotonic() + ORPHAN_GRACE
        while time.monotonic() < deadline:
            hosts = [p for p in hosts if pid_alive(p)]
            if not hosts:
                return
            time.sleep(0.5)
        hosts = [p for p in hosts if pid_alive(p)]
        log.warning("stale host(s) %s still alive; SIGTERM (%s)",
                    hosts, self.session_file)
        for p in hosts:
            try:
                os.kill(p, signal.SIGTERM)
            except OSError:
                pass
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            hosts = [p for p in hosts if pid_alive(p)]
            if not hosts:
                return
            time.sleep(0.2)
        for p in [p for p in hosts if pid_alive(p)]:
            try:
                os.kill(p, signal.SIGKILL)
            except OSError:
                pass

    def _sync_header_cwd(self):
        """会话文件首帧 cwd 对齐（任务 53yjuc，receiver-load-debug）：pi resume 时
        项目资源（含 .pi/extensions 扩展）发现用会话文件**首帧记录的 cwd**（创建时值，
        SessionManager.open 无 cwdOverride：pi dist/core/session-manager.js open()；
        扩展发现只查 <cwd>/.pi 无向上回溯：dist/core/package-manager.js projectBaseDir）——
        首帧 cwd 与宿主拉起 cwd 不一致时（典型：文件由旧代码在子目录创建、.agent 后改
        显式 cwd）扩展整个不加载（2026-09-01 dev-dispatcher 事故：agentd 不加载 →
        receiver/dispatch 工具全无）。拉起前把首帧 cwd 改写为 self.cwd（其余行不动，
        临时文件+rename 原子落盘）。调用时机 = _run_loop 内、旧宿主已清场、本线程独占，
        无并发写者。.jsonl 直开（缺省 cwd=dirname）首帧本就等于 dirname，无行为变化。"""
        try:
            with open(self.session_file, "rb") as f:
                first = f.readline()
                rest = f.read()
        except OSError:
            return
        if not first:
            return
        try:
            header = json.loads(first.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        if not isinstance(header, dict) or header.get("type") != "session":
            return
        if header.get("cwd") == self.cwd:
            return
        old = header.get("cwd")
        header["cwd"] = self.cwd
        tmp = self.session_file + ".hdr-tmp"
        try:
            with open(tmp, "wb") as f:
                f.write((json.dumps(header, ensure_ascii=False) + "\n").encode("utf-8"))
                f.write(rest)
            os.replace(tmp, self.session_file)
        except OSError as e:
            log.error("header cwd sync failed for %s: %s", self.session_file, e)
            try:
                os.unlink(tmp)
            except OSError:
                pass
            return
        log.info("header cwd synced %r -> %r for %s", old, self.cwd,
                 self.session_file)

    def _spawn(self):
        os.makedirs(self.cwd, exist_ok=True)
        # 接管 replica 文件（任务 4l3de8）：不经 clear 直接托管的会话文件可能是被远端
        # pull 复活的副本（组=replica，如 2026-08-31 mac-dispatcher 事故现场）——宿主是
        # 单写者，拉起前升格本机原件，否则活文件会被远端旧副本覆盖、本机写入也不进 push。
        if os.path.exists(self.session_file):
            _claim_origin(self.session_file)
            self._sync_header_cwd()   # 首帧 cwd 对齐（任务 53yjuc）
        # 稳定期重置（上轮存活超 STABLE_RESET → 崩溃计数清零）
        if (self._spawned_at and time.monotonic() - self._spawned_at
                > STABLE_RESET):
            self._crash_ts = []
            self.restarts = 0
        with self._cond:
            self.state = "starting"
        env = clean_env()
        env[HOST_MARKER] = self.session_file        # 零文件旧宿主识别标记
        cmd = [pi_binary(), "--mode", "rpc", "--session", self.session_file,
               "--name", self.name]
        if os.path.exists(PROBE_EXT):               # 探针扩展（任务 s0f1la）
            cmd += ["-e", PROBE_EXT]
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=None,                            # 继承 web stderr → web.log
            cwd=self.cwd, start_new_session=True, env=env)
        self.pid = self.proc.pid
        with self._cond:
            self.gen += 1
            self.state = "running"
        self._spawned_at = time.monotonic()
        log.info("spawned gen=%d pid=%d cwd=%s session=%s",
                 self.gen, self.pid, self.cwd, self.session_file)
        # stdout 读取线程（逐行 → on_event）
        threading.Thread(target=self._read_stdout, daemon=True,
                         name="reader-" + self.name).start()
        self._broadcast({"type": "sessiond.session_restarted",
                         "session": self.name, "gen": self.gen,
                         "pid": self.pid})

    def _read_stdout(self):
        proc = self.proc
        try:
            for raw in iter(lambda: proc.stdout.readline(), b""):
                if len(raw) > LINE_LIMIT:
                    log.warning("stdout single line exceeds %d bytes; dropping",
                                LINE_LIMIT)
                    continue
                raw = raw.rstrip(b"\r\n")
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                except ValueError:
                    continue
                self.on_event(obj)
        except (OSError, ValueError):
            pass

    def _broadcast(self, obj):
        try:
            self.on_event(obj)
        except Exception:
            log.exception("on_event failed")


# ----------------------------------------------------------------
# 任务会话 socket 监督（任务 ybzvbn，票 hegipc，plan v3 B 路线）
#
# rpc 封装形态的任务（命令含 agentd/pi-rpc-wrap.py）由封装脚本拉起并持有，
# 经 ~/m/run/agentd/<taskId>.sock 透传 pi rpc 原生字节流。SocketSupervisor =
# 「连接而非 spawn」的监督者：生命周期所有权在 agentd runner，本类**无杀权**
# （kill/reload/clear 一律拒绝），不与 Supervisor 共享生命周期机制（崩溃重拉/
# 旧宿主识别/代际/首帧 cwd 改写），仅共享 bridge 的 ingest/事件环。
# 断连（任务收敛/被杀）= 监督终结：广播 agentd.task_ended 后由桥接摘除自身，
# 后续访问重新路由（终态任务转入普通复活路径）。

class SocketSupervisor:
    """连接透传 socket 的任务会话监督者（接口面对齐 Supervisor 的桥接消费面）。"""

    CONNECT_WINDOW = 25.0    # 首次连接等待窗（对齐 wait_ready 缺省口径）
    CONNECT_RETRY = 0.3

    def __init__(self, session_path, on_event, sock_path, participant_id=None):
        # 路径处理与 Supervisor 同口径（兼容站内路径与已解析绝对路径）。
        if os.path.isabs(session_path) and session_path.endswith(".jsonl"):
            rp = os.path.realpath(session_path)
            roots = [WS_REAL, os.path.realpath(os.path.join(WS, "run"))]
            if any(rp == r or rp.startswith(r + os.sep) for r in roots):
                self.session_file = rp
            else:
                self.session_file = resolve_session_path(session_path)
        else:
            self.session_file = resolve_session_path(session_path)
        self.name = display_name(self.session_file)
        self.cwd = os.path.dirname(self.session_file)
        self.sock_path = sock_path
        # agentd 参与方路径式 id（如 task/<id>，agentd_route 载荷透传，任务 dsuqbi）：
        # 控制面代理（op=clear/reload → control/clear|restart）写请求/等回执的寻址依据。
        self.participant_id = participant_id
        self.on_event = on_event
        self.on_lost = None          # 断连回调（桥接注入：摘注册表 + 终结标记）
        self.gen = 1                 # 单世代（无重拉概念）
        self.restarts = 0
        self.disabled = False
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
                if self.state in ("ended", "disabled"):
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
                    "pid": None, "gen": self.gen, "restarts": self.restarts,
                    "disabled": self.disabled, "session_file": self.session_file,
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
                if len(line) > LINE_LIMIT:
                    log.warning("socket line exceeds %d bytes; dropping",
                                LINE_LIMIT)
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
        # 重新路由——终态任务落入普通复活路径）。
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
# 只桥接透传、永不 spawn，三态全不落普通 Supervisor：
#   活 socket 在场（sock 字段 ∧ (pid,procStart) 身份一致 ∧ 可连） → SocketSupervisor 直播
#   已终态（final）                              → 拒绝并指引（生命周期归 agentd）
#   缺席（无 pid.json ∨ 进程身份消失 ∧ 无 sock）  → 等待提示（拉起权单点 = agentd）
#   sock 在场但进程身份消失（封装已退出未落终态） → 拒绝并等待（等终态落盘/自动拉起）
#   进程活但无 sock（旧形态运行中）              → 拒绝（双宿主风险，协议 §11.12）
#   status=paused                                → 拒绝并提示 control restart
# 跨机：spec.host ≠ 本机规范名 → 拒绝 + 指引（同 host_guard 口径）。
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
        if real == WS_REAL or real.startswith(WS_REAL + os.sep):
            p = "/" + os.path.relpath(real, WS_REAL)
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
      ("live",   {"sock": <透传端点>, "participant_id": <族/名>}) → SocketSupervisor 直播
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
    adir = os.path.join(WS, "agents", family, name)
    try:
        with open(os.path.join(adir, "spec.json")) as f:
            spec = json.load(f)
    except (OSError, ValueError):
        spec = None
    spec = spec if isinstance(spec, dict) else {}
    # 跨机守卫（同 host_guard 口径：指引用户去宿主机的 8080）
    host = spec.get("host")
    if isinstance(host, str) and host.strip():
        me = _self_host()
        if me is None:
            return ("reject",
                    "host guard: env/host-id 缺失或无本机映射，无法判定会话宿主，"
                    "拒绝打开（修复 env/host-id 后重试）")
        if host.strip() != me:
            return ("reject",
                    "该会话宿主=%s，请通过 %s 的服务访问（本机=%s）"
                    % (host.strip(), host.strip(), me))
    try:
        with open(os.path.join(adir, "pid.json")) as f:
            doc = json.load(f)
    except (OSError, ValueError):
        doc = None
    if not isinstance(doc, dict):
        doc = None
    # 终态 → 拒绝并指引（生命周期归 agentd：换代 = control restart，web 不代拉）
    if doc is not None and doc.get("final") is True:
        return ("reject",
                "会话已终态（agentd 已收口 %s），不可打开；如需续用请经 agentd "
                "control restart 换代" % participant_id)
    if doc is None:
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
        # 旧 catch-all 文案对此场景不准）。等待终态落盘；终态后如需续用经 control restart 换代。
        return ("reject",
                "封装进程已退出，等待 agentd 落终态（%s）：收敛中，请稍候重试"
                "（restartPolicy=auto 会自动拉起；若已终态，续用经 agentd control restart 换代）"
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
    """agentd 参与方声明的工作目录（任务 8r0tww）：读 agents/<族>/<名>/spec.json 的
    `workdir` 字段——= agentd runner 拉起会话进程的 cwd（runner 侧口径：
    os.path.expanduser(workdir)，`~/` 按执行机 $HOME 展开，绝对路径 no-op，
    agentd/runner.py 同款）。participant_id = 路径式两段 id（如 task/<id>、
    bot/<名>，agentd_route 载荷来源）。
    读取失败（id 非法/文件缺失/坏 JSON/缺字段/非字符串）一律 → None：不猜缺省，
    调用方显 `?`。"""
    if not isinstance(participant_id, str):
        return None
    parts = participant_id.split("/")
    if len(parts) != 2 or not all(parts) or any(".." in x for x in parts):
        return None
    try:
        with open(os.path.join(WS, "agents", parts[0], parts[1],
                               "spec.json")) as f:
            spec = json.load(f)
    except (OSError, ValueError):
        return None
    wd = spec.get("workdir") if isinstance(spec, dict) else None
    if not isinstance(wd, str) or not wd.strip():
        return None
    return os.path.expanduser(wd.strip())


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
