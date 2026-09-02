# -*- type=script -*-
# resident.py — resident 会话启动扫描（任务 02mi7r，票 su068s）。
#
# 背景：sessiond 会话原本只懒拉起（首次访问建桥接时 spawn），夜间无人开页则主调度员
# 会话进程不存在 → 扩展 receiver 不运行 → bot/dispatcher/inbox 通知积压到用户
# 主动发消息才处理。resident 机制 = .agent 声明 "resident": true 且 host == 本机规范名
# 时，web 启动完成（服务 ready 钩子，见 core/wsgi.py fork 之后）即无 attach 直接建桥拉起；
# 进程退出后由监督员既有崩溃退避重拉机制自拉起（非用户操作语义不变：本无 stop 操作，
# reload/clear 杀后重拉照旧）。
#
# 守卫双保险：① 本扫描只在 host == 本机（env/host-id 查表，_self_host）时入选——
# 他机 web 启动扫到同一 .agent（~/m 跨机同步）也不拉起；② 建桥时仍过
# proc.host_guard 唯一守卫点（声明者 host ≠ 本机拒绝），双宿主红线不变。
# 本机身份不可得（配置错误）→ 整体跳过不猜测（与 host_guard 口径一致）。
#
# 非 resident .agent 与 .jsonl 直开零波及：本模块只触达声明 resident:true 的声明者，
# 懒拉起链路与语义完全不变。仅 python3 标准库。
import json
import logging
import os
import posixpath
import re

from ext.sessiond import bridge as _b
from ext.sessiond import proc as _proc

log = logging.getLogger("sessiond-resident")

_FSTAB = os.path.join(_proc.WS, "w", "stores", "fstab")
# 与 stores/store.py build_root_store 同口径的 fstab 行解析。
_FSTAB_RE = re.compile(r"(?m)^([^# \t]+)\s+(\w+)(.*)\n")
# 有本地文件系统、值得扫 .agent 的挂载类型（Cmd=命令管道、WebDav=远端，无本地目录）。
_FS_STORE_TYPES = {"Dir", "Enc"}
# walk 剪枝：重/无关目录（不跟随符号链接另保 run 软链不被穿透）。
_PRUNE = {".git", "node_modules", "__pycache__"}

_bootstrapped = False


def _mount_roots():
    """fstab 挂载的本地目录根集合（去重、剔除被包含的子根）。
    相对目标按 ~/m 解析（同 server.py chdir 后的口径）；不存在/逃逸的根跳过。"""
    roots = set()
    try:
        with open(_FSTAB) as f:
            content = "\n" + f.read()
    except OSError as e:
        log.error("cannot read fstab %s: %s", _FSTAB, e)
        return []
    for mpoint, stype, arg in _FSTAB_RE.findall(content):
        if stype not in _FS_STORE_TYPES:
            continue
        target = arg.strip().split()[0] if arg.strip() else ""
        if not target:
            continue
        real = os.path.realpath(os.path.join(_proc.WS, target))
        roots.add(real)
    # 剔除被其它根包含的子根（如 /sessiond → w/ext/sessiond ⊂ / → .）
    minimal = []
    for r in sorted(roots):
        if not any(r != o and r.startswith(o + os.sep) for o in roots):
            minimal.append(r)
    return minimal


def _agent_starts():
    """扫各挂载根下全部 .agent 文件，yield (agent名, spec, agent文件所在目录)。
    坏文件/非 dict 宽容跳过。不跟随符号链接，剪枝重目录。"""
    seen = set()
    for root in _mount_roots():
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _PRUNE]
            for fn in filenames:
                if not fn.endswith(".agent"):
                    continue
                path = os.path.join(dirpath, fn)
                if path in seen:
                    continue
                seen.add(path)
                try:
                    with open(path) as f:
                        spec = json.load(f)
                except (OSError, ValueError):
                    continue
                if not isinstance(spec, dict):
                    continue
                yield fn[:-len(".agent")], spec, dirpath


def _derive_site_path(sess_dir, name):
    """sessionDir realpath → 站内会话路径（口径同 rpc/api.py:_resolve_agent 的任务
    kqhweh 推导：先 ~/m 锚定，逃逸则回退 run 软链锚定）。失败返回 None。"""
    rel_dir = os.path.relpath(sess_dir, _proc.WS)
    if rel_dir == ".." or rel_dir.startswith(".." + os.sep):
        run_real = os.path.realpath(os.path.join(_proc.WS, "run"))
        rel_run = os.path.relpath(sess_dir, run_real)
        if rel_run == ".." or rel_run.startswith(".." + os.sep):
            return None
        rel_dir = posixpath.normpath(posixpath.join("run", rel_run))
    return "/" + posixpath.normpath(posixpath.join(rel_dir, name + ".jsonl"))


def _resolve_start(name, spec, agent_dir):
    """解析单个 resident 声明者 → (站内会话路径, cwd)；任何字段非法返回
    (None, 原因串)。口径与 rpc/api.py:_resolve_agent 一致。"""
    # sessionDir：缺省 = .agent 所在目录；显式须非空字符串。
    sd = spec.get("sessionDir")
    if sd is None:
        sess_dir = os.path.realpath(agent_dir)
    elif not isinstance(sd, str) or not sd.strip():
        return None, "sessionDir must be a non-empty string when present"
    else:
        sess_dir = os.path.realpath(os.path.expanduser(sd.strip()))
    roots = [_proc.WS_REAL, os.path.realpath(os.path.join(_proc.WS, "run"))]
    if not any(sess_dir == r or sess_dir.startswith(r + os.sep) for r in roots):
        return None, "sessionDir escapes workspace: %r" % (sd,)
    site = _derive_site_path(sess_dir, name)
    if site is None:
        return None, "sessionDir %s escapes site root" % sess_dir
    try:
        _proc.resolve_session_path(site)
    except ValueError as e:
        return None, "derived session path rejected: %s" % e
    # cwd：显式字段优先（~/ 展开），缺省 = .agent 所在目录。
    cwd_val = spec.get("cwd")
    cwd_src = (os.path.expanduser(cwd_val.strip())
               if isinstance(cwd_val, str) and cwd_val.strip()
               else agent_dir)
    try:
        cwd = _proc.resolve_cwd(cwd_src)
    except ValueError as e:
        return None, "cwd rejected: %s" % e
    return site, cwd


def bootstrap():
    """web 启动 ready 钩子（幂等）：扫描各挂载点下声明 "resident": true 且
    host == 本机规范名的 .agent，无 attach 直接建桥拉起（建桥即过 host_guard +
    ensure_started 起监督线程 → _spawn）。单会话失败只记日志跳过，绝不抛出
    （调用方在 core/wsgi.py 另有 try/except 兜底，失败不影响服务启动）。"""
    global _bootstrapped
    if _bootstrapped:
        return
    _bootstrapped = True
    me = _proc._self_host()
    if me is None:
        log.error("resident bootstrap skipped: env/host-id missing or no entry "
                  "for this hostname (fix env/host-id)")
        return
    picked, skipped = [], 0
    for name, spec, agent_dir in _agent_starts():
        if spec.get("resident") is not True:
            continue
        host = spec.get("host")
        if not isinstance(host, str) or not host.strip():
            log.warning("resident agent %s skipped: missing host", name)
            skipped += 1
            continue
        if host.strip() != me:
            log.info("resident agent %s skipped: declared host=%s, this host=%s",
                     name, host.strip(), me)
            skipped += 1
            continue
        site, cwd = _resolve_start(name, spec, agent_dir)
        if site is None:
            log.error("resident agent %s skipped: %s", name, cwd)  # site=None 时第二位 = 原因串
            skipped += 1
            continue
        picked.append((name, site, cwd))
    if not picked:
        log.info("resident bootstrap: no resident agent for host=%s "
                 "(scanned mounts, %d resident declaration(s) skipped)",
                 me, skipped)
        return
    for name, site, cwd in picked:
        try:
            _b.set_session_cwd(site, cwd)     # 显式 cwd 登记（建桥时消费，同 op=agent）
            _b.get_bridge(site)               # 建桥（host_guard）→ 监督线程 → spawn
            log.info("resident session started: agent=%s session=%s cwd=%s",
                     name, site, cwd)
        except ValueError as e:
            log.error("resident session %s refused: %s", name, e)
            skipped += 1
        except Exception:
            log.exception("resident session %s bootstrap failed", name)
            skipped += 1
    log.info("resident bootstrap done: host=%s picked=%d skipped=%d",
             me, len(picked), skipped)
