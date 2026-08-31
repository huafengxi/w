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
    """递归扫 assistant/**/*.agent，yield (agent名, host, 推导会话文件 realpath)。
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
                os.path.join(base, name + ".jsonl"))


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
    for name, host, derived in _scan_agent_declarations():
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
        self.name = display_name(self.session_file)
        host_guard(self.session_file)   # 跨宿主守卫唯一守卫点（任务 8nherl）
        # cwd 由调用方显式传入（任务 fw2ll1：.agent cwd/dir 拆分，经 bridge 转交）；
        # 缺省 = jsonl 所在目录（.jsonl 直开路径行为不变，cwd1）。
        self.cwd = resolve_cwd(cwd) if cwd else os.path.dirname(self.session_file)
        self.on_event = on_event
        self.proc = None
        self.pid = None
        self.gen = 0
        self.restarts = 0
        self.state = "idle"         # idle/starting/running/restarting/disabled
        self.disabled = False       # 熔断标记（reload 解除）
        self._reloading = False     # 主动 reload：退出不计崩溃/不排退避
        self._clearing = False      # 主动 clear（0829-2238-atnj）：重拉前先把 jsonl 截断到 0
        self._clear_error = None    # clear 截断文件失败信息（由监督循环回填）
        self._crash_ts = []
        self._spawned_at = None
        self._stdin_lock = threading.Lock()
        self._cond = threading.Condition()
        self._started = False
        self._thread = None

    # ---- 启动（幂等，首次调用拉起监督线程） ----

    def ensure_started(self):
        with self._cond:
            if self._started:
                return
            self._started = True
            self._thread = threading.Thread(
                target=self._run_loop,
                name="supervisor-" + self.name, daemon=True)
            self._thread.start()

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
                self._broadcast({"type": "sessiond.session_restarting",
                                 "session": self.name,
                                 "delay": BACKOFF_MAX})
                time.sleep(BACKOFF_MAX)
                continue
            rc = self.proc.wait()
            with self._cond:
                reloading = self._reloading
                self._reloading = False
                self.proc = None
                self.pid = None
                self.state = "restarting"
            log.warning("pi process gone rc=%s gen=%d session=%s file=%s",
                        rc, self.gen, self.name, self.session_file)
            if reloading:
                continue                # 主动 reload：立即重拉（无退避）
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

    def _spawn(self):
        os.makedirs(self.cwd, exist_ok=True)
        # 接管 replica 文件（任务 4l3de8）：不经 clear 直接托管的会话文件可能是被远端
        # pull 复活的副本（组=replica，如 2026-08-31 mac-dispatcher 事故现场）——宿主是
        # 单写者，拉起前升格本机原件，否则活文件会被远端旧副本覆盖、本机写入也不进 push。
        if os.path.exists(self.session_file):
            _claim_origin(self.session_file)
        # 稳定期重置（上轮存活超 STABLE_RESET → 崩溃计数清零）
        if (self._spawned_at and time.monotonic() - self._spawned_at
                > STABLE_RESET):
            self._crash_ts = []
            self.restarts = 0
        with self._cond:
            self.state = "starting"
        env = clean_env()
        env[HOST_MARKER] = self.session_file        # 零文件旧宿主识别标记
        self.proc = subprocess.Popen(
            [pi_binary(), "--mode", "rpc", "--session", self.session_file,
             "--name", self.name],
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
