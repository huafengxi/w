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
# 会话工作目录 = 其 jsonl 所在目录（用户拍板 inform cwd1；cwd 决定 pi 加载
# 哪个工作区的 AGENTS/扩展——预期行为，不特判）。
#
# 零文件（用户拍板 inform n0fx/p4id/s4fn）：除会话 jsonl 本身外不引入任何
# 辅助文件——无账本、无进程 stderr 文件。
#   - 子进程 stderr 继承 web 进程（并入 run/logs/web.log）。
#   - 旧宿主识别 = environ 标记扫描：spawn 注入
#     `SESSIOND_SESSION_FILE=<jsonl 绝对路径>`；处置旧宿主时扫
#     /proc/*/environ 精确匹配（用户 ask 拍板：pi 会 setproctitle 把 argv
#     重写为裸 "pi"，cmdline 匹配不可行）。命中即旧宿主（无 starttime 校验，
#     s4fn）：等 stdin-EOF 自然退出，宽限后 SIGTERM→SIGKILL。
# `dispatcher` 名允许（用户拍板 0829-1958-od0t）。
#
# web 重启行为（替代 fd handoff）：pi rpc 模式 stdin EOF 即优雅退出（会话持久体
# 已 flush）→ web 死后会话自行退出；新 web 首次访问某会话时按 environ 标记扫
# 出仍存的旧宿主，等其自然退出（宽限后杀之——防同一 jsonl 双宿主），随后从
# 该 .jsonl resume 重拉。聊天历史不丢。仅 python3 标准库。
import json
import logging
import os
import posixpath
import shutil
import signal
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


BACKOFF_MAX = 30.0          # 崩溃重启退避上限（秒）
FAIL_LIMIT = 6              # 熔断：窗口内连续崩溃次数上限
FAIL_WINDOW = 300.0         # 熔断窗口（秒）
STABLE_RESET = 120.0        # 存活超过该秒数 → 重置崩溃计数
LINE_LIMIT = 16 * 1024 * 1024  # 单行 JSONL 上限，超限丢弃该行（口径沿用）
ORPHAN_GRACE = 15.0         # 等待旧宿主自然退出的宽限（秒）

# 剥除调度/任务身份与上游会话身份（PI_SESSION* 经 make 链泄漏会投毒子进程
# 的会话归属，实测案例：任务环境 PI_SESSION_FILE 漏进会话进程）。
ENV_SCRUB_EXACT = {"AGENTD_TASK", "AGENT_SELF", "AGENT_HOME", "AGENT_ROOT",
                   "DISPATCH_TASK_ID"}
ENV_SCRUB_PREFIXES = ("DISPATCH_TASK_", "PI_SESSION")


def clean_env():
    """剥除调度/任务身份环境变量（口径同 svc/svc.py clean_env）。"""
    return {k: v for k, v in os.environ.items()
            if k not in ENV_SCRUB_EXACT
            and not any(k.startswith(p) for p in ENV_SCRUB_PREFIXES)}


def pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def find_hosts(session_file):
    """零文件旧宿主识别（R2）：扫 /proc/*/environ，精确匹配
    `SESSIOND_SESSION_FILE=<session_file>` 标记的进程 → pid 列表。"""
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
        if marker in env.split(b"\x00"):
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

    def __init__(self, session_path, on_event):
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
        self.cwd = os.path.dirname(self.session_file)   # cwd = jsonl 所在目录（cwd1）
        self.on_event = on_event
        self.proc = None
        self.pid = None
        self.gen = 0
        self.restarts = 0
        self.state = "idle"         # idle/starting/running/restarting/disabled
        self.disabled = False       # 熔断标记（reload 解除）
        self._reloading = False     # 主动 reload：退出不计崩溃/不排退避
        self._clearing = False      # 主动 clear（0829-2238-atnj）：重拉前先删 jsonl
        self._clear_error = None    # clear 删文件失败信息（由监督循环回填）
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

    def wait_ready(self, timeout=10.0):
        """等待会话进程就位（state=running）。首次访问时监督线程刚拉起，
        spawn 需毫秒~秒级（+ 旧宿主清场宽限）；基线路径等待而非快速失败。"""
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
        重拉前是否删 jsonl。reload/clear 共用。"""
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
        """/clear（0829-2238-atnj）：保留会话路径、清空全部内容。顺序 =
        ① 杀当前会话进程（同 reload 口径）→ ② 删该会话 jsonl（监督循环内、
        旧进程死透后、重拉前执行，同线程无竞态，防旧进程把内存历史回写）→
        ③ 立即重拉（resume 空起点；文件不存在时 pi 自行新建）。必须「删除」
        而非截断成空文件：对已存在的 jsonl，pi 懒落盘走 openSync(wx) 会
        EEXIST（pdop 踩坑记录）。environ 标记 SESSIOND_SESSION_FILE 不变
        （路径不变）。返回 {ok, gen, pid}。"""
        with self._cond:
            self._clear_error = None
        r = self._kill_and_restart(("_clearing",), timeout)
        if r.get("ok") and r.get("clear_error"):
            with self._cond:
                self._clear_error = None
            return {"ok": False,
                    "error": "session respawned but jsonl removal failed: %s"
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
            self._apply_clear()         # clear：重拉前先删 jsonl（旧进程已死透）
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
        """clear 落地（0829-2238-atnj）：监督循环内、_spawn 之前执行——此刻旧进程
        必然已死透（proc.wait() 已返回或崩溃退避刚结束），删除 jsonl 与重拉同线程，
        无竞态。必须是「删除」而非截断成空文件：对已存在的 jsonl，pi 懒落盘走
        openSync(wx) 会 EEXIST（pdop 踩坑记录）；文件不存在时 pi 重拉后自行新建。"""
        with self._cond:
            if not self._clearing:
                return
            self._clearing = False
        try:
            os.remove(self.session_file)
            log.info("clear: removed session file %s before respawn",
                     self.session_file)
        except FileNotFoundError:
            log.info("clear: session file %s already absent", self.session_file)
        except OSError as e:
            log.error("clear: failed to remove %s: %s", self.session_file, e)
            with self._cond:
                self._clear_error = str(e)

    def _clear_stale_proc(self):
        """防双宿主清场（R2 零文件）：按 environ 标记扫出同一 jsonl 的旧宿主
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
