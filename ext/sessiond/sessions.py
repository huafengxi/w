# -*- type=script -*-
"""Session file operations, workspace/profile validation and child configuration.

No process scanning, signaling, socket client or runtime factory replacement.
Registered CRUD is coordinated by registry.Registry, not these file helpers.
"""
import grp
import json
import logging
import os
import posixpath
import random
import re
import shutil
import socket
import string
import sys
import time
import uuid

log = logging.getLogger("sessiond-sessions")

from core.config import workspace, path as config_path
WS = workspace()
WS_REAL = os.path.realpath(WS)

# 子进程 environ 标记（零文件旧宿主识别，R2）
HOST_MARKER = "SESSIOND_SESSION_FILE"

# Optional pi-side adapter for inspect, chat state and same-file regeneration.
PI_EXTENSION = os.path.join(os.path.dirname(os.path.realpath(__file__)), "pi-extension.ts")


def resolve_session_path(path):
    """会话路径校验与解析（路径安全红线）：
    - 必须 `/` 开头的站内路径、`.jsonl` 后缀；
    - normpath 折叠 `..`/`.` 后与 workspace 拼接，再 realpath 前缀校验（双保险，
      防符号链接逃逸；白名单根 = workspace + run 运行时区）；
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
    """显式 cwd 校验（任务 fw2ll1）：与会话路径同一根集（workspace + run 运行时区），
    realpath 前缀校验防穿越/符号链接逃逸；非法抛 ValueError（调用方回 400）。
    返回解析后的绝对路径（目录可以尚不存在，_spawn 时 makedirs）。"""
    if not isinstance(cwd, str) or not cwd.strip() or "\x00" in cwd:
        raise ValueError("invalid cwd %r" % (cwd,))
    real = os.path.realpath(os.path.expanduser(cwd.strip()))
    roots = [WS_REAL, os.path.realpath(os.path.join(WS, "run"))]
    if not any(real == r or real.startswith(r + os.sep) for r in roots):
        raise ValueError("cwd escapes workspace: %r" % (cwd,))
    return real


LINE_LIMIT = 16 * 1024 * 1024  # 单行 JSONL 上限，超限丢弃该行（口径沿用）

# 剥除调度/任务身份与上游会话身份（PI_* 经 make 链泄漏会投毒子进程的会话归属，
# 实测案例：任务环境 PI_SESSION_FILE 漏进会话进程）。名单单一事实源 =
# agentd/envscrub.py（任务 fsmkvy 收敛：原本处独立拷贝漂移，改与服务执行层/runner
# 同源共享；keep 空集、不剥第三方 API key 族——会话进程用钥走自身配置路径）。
sys.path.insert(0, config_path('W_AGENTD_DIR', required=True))
import envscrub  # noqa: E402


def clean_env():
    """剥除调度/任务身份环境变量（口径单点 = agentd/envscrub.py）。"""
    return envscrub.scrub_env()


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


def _self_host():
    """本机规范名：env/host-id 查表（格式同 agentd/agentctl.py
    local_canonical_host：每行 `<hostname> <空白> <规范名>`，# 注释）。
    文件缺失/不可读/本机 hostname 未命中 → None（调用方报配置错误，不猜测）。"""
    try:
        with open(config_path('W_HOST_ID_FILE', required=True)) as f:
            lines = f.read().splitlines()
    except (OSError, ValueError):
        return None
    hn = socket.gethostname()
    for line in lines:
        parts = line.strip().split()
        if len(parts) > 1 and parts[0] == hn and not parts[0].startswith("#"):
            return parts[1]
    return None


def pi_binary():
    # Caller can pin a dedicated compatible runtime without replacing global pi.
    return os.path.expanduser(os.environ.get("W_SESSION_PI_BIN") or shutil.which("pi") or "pi")


# ----------------------------------------------------------------
# 指挥中心临时会话（任务 z293ql，设计稿 §4.2「按需创建会话」）
#
# 形态 = 宿主本地运行时区 `run/sessiond/` 下的一枚 `.agent` 声明者 + 它的会话 jsonl：
#   - `.agent` 是普通会话入口，由客户端连接独立 P，不经 agentd；
#   - `run/` 已被主仓 .gitignore 覆盖 ∧ 不进 agents-sync ⇒ **ephemeral**：`run/` 被清 =
#     临时会话丢失（设计稿已接受），删除 = 直接 rm（⛔ 不走 `agents-sync/gc.py` 铁律，
#     那条的射程是 `agents/**` 的参与方目录）；
#   - 字段集全部**服务端恒定 ∨ 自动生成**，客户端只控 `profile`（且必须是 `form: interactive`
#     的清单名）；`cwd` 由该清单的 `cwd` 字段声明、经项目白名单校验（⛔ 客户端不控）；
#     ⛔ 无 `command` 键（spawn argv 单点在 `pi-host LaunchConfig`）。
# 生命周期/删除协调归 registry 与 lifecycle；本层只管文件系统面与校验。

SESSIONS_SUBDIR = ("run", "sessiond")   # 宿主本地运行时区（⛔ 不入 git / 不同步）
CC_PROFILE = os.environ.get('W_SESSION_PROFILE')  # caller-owned default; validated as interactive
PROFILE_CWD_DEFAULT = "."               # 清单未声明 `cwd` 时的缺省（= 工作区根）
SESSION_NAME_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
SESSION_NAME_ALPHABET = string.ascii_lowercase + string.digits
SESSION_NAME_TRIES = 8            # 撞名重取上界（36^4 空间 ⇒ 常态一次即中）
PROFILE_NAME_OK = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
TITLE_MAX = 80              # rename 的标题上限（字符）
AUTO_TITLE_MAX = 48         # 自动标题截断长度（首条 user 消息的首个非空行）
TITLE_SCAN_LINES = 200      # 派生自动标题时最多扫多少行 jsonl（有界读）
TITLE_SCAN_BYTES = 1 << 20  # 同上：最多读多少字节


def sessions_dir():
    """临时会话目录（`<WS>/run/sessiond`）。⛔ 不缓存 realpath：测试会 monkeypatch WS。"""
    return os.path.join(WS, *SESSIONS_SUBDIR)


def _ws_roots():
    """会话/cwd 的白名单根集（与 `resolve_session_path`/`resolve_cwd` 同口径）。"""
    return [WS_REAL, os.path.realpath(os.path.join(WS, "run"))]


def _within_roots(real):
    return any(real == r or real.startswith(r + os.sep) for r in _ws_roots())


def site_path_of(real_path):
    """realpath → 站内路径（两锚定，与 `rpc/api.py:_resolve_agent` 同口径）：先 WS 锚定，
    逃逸则 run 软链锚定（还原为 `/run/…`）；两者都逃逸 → None（调用方报 500，⛔ 不猜）。"""
    rel = os.path.relpath(real_path, WS)
    if not (rel == os.pardir or rel.startswith(os.pardir + os.sep)):
        return "/" + posixpath.normpath(rel.replace(os.sep, "/"))
    run_real = os.path.realpath(os.path.join(WS, "run"))
    rel_run = os.path.relpath(real_path, run_real)
    if rel_run == os.pardir or rel_run.startswith(os.pardir + os.sep):
        return None
    return "/" + posixpath.normpath(
        posixpath.join("run", rel_run.replace(os.sep, "/")))


def list_projects():
    """**服务端枚举**可选项目目录（cwd 白名单；设计稿 §8⑧ 的「甲」档、用户拍板）：
    workspace 自身 + 顶层含 `.git` 的子目录。返回 [(相对 WS 的路径, realpath)]，相对路径
    `"."` = WS 自身（居首），其余按名字排序。只扫一层（`os.scandir` + 每项一次 `.git`
    存在性判断）⇒ 零递归成本；⛔ 无静态清单可漂（新子仓 clone 到顶层即自动出现）。
    realpath 逃出根集的项**不入白名单**（防顶层符号链接把清单变成逃逸口）。"""
    items = [(".", WS_REAL)]
    try:
        entries = sorted(os.scandir(WS), key=lambda e: e.name)
    except OSError:
        return items
    for e in entries:
        try:
            if not e.is_dir(follow_symlinks=False):
                continue
        except OSError:
            continue
        if e.name.startswith("."):
            continue
        git = os.path.join(e.path, ".git")
        if not (os.path.isdir(git) or os.path.isfile(git)):
            continue
        real = os.path.realpath(e.path)
        if not _within_roots(real):
            log.warning("project enumeration: %s resolves outside the workspace "
                        "roots (%s); excluded", e.path, real)
            continue
        items.append((e.name, real))
    return items


def resolve_project_cwd(raw):
    """客户端提交的 cwd → 项目白名单成员校验 + 既有逃逸校验（双保险）。
    入参形态 = **相对 WS 的路径**（表单 `<select>` 的 value：`.` ∨ `w` ∨ `lore`…）。
    非法抛 ValueError（调用方回 400）：绝对路径 / 含 `..` 段 / 不在枚举集内 /
    逃出根集。返回 realpath。"""
    if not isinstance(raw, str) or not raw.strip() or "\x00" in raw:
        raise ValueError("invalid cwd %r" % (raw,))
    rel = raw.strip()
    if os.path.isabs(rel) or rel.startswith("~"):
        raise ValueError("cwd must be a workspace-relative path (got an absolute "
                         "or ~-form path): %r" % (raw,))
    parts = [p for p in rel.replace(os.sep, "/").split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise ValueError("cwd must not contain '..' segments: %r" % (raw,))
    real = os.path.realpath(os.path.join(WS_REAL, *parts) if parts else WS_REAL)
    allowed = {r for _rel, r in list_projects()}
    if real not in allowed:
        raise ValueError("cwd not in the server-side project whitelist: %r "
                         "(resolved %s; whitelist = workspace + top-level git repos)"
                         % (raw, real))
    return resolve_cwd(real)      # 既有根集 + realpath 前缀校验（纵深防御第二道）


def validate_profile(name):
    """`.agent` 的 `profile` 字段白名单校验（任务 z293ql；登记方追加判据）：
    ① 名字形态（小写字母数字与连字符）；② `bots/profiles/<名>.json` 在场且是合法 JSON；
    ③ 该清单声明 **`form: interactive`** —— 只放行交互档：给交互会话配 `form: task`
    的清单会得到「无基线能力 + `ask_user` 被排除」的**静默形态**（结构上可达、零 WARN），
    配 `form: resident` 则拿不到 interactive 档的 ask_user 保留 ⇒ 两类都是配错形态即静默失能。
    非法抛 ValueError（调用方回 400）。返回归一后的名字（判据本体 = `profile_spec`）。"""
    return profile_spec(name)[0]


def profile_spec(name):
    """profile 名 → (归一名, 清单 doc)。三道校验的实现单点（`validate_profile` 与
    `list_interactive_profiles` 共用；读盘也只在此一处）：① 名字形态（小写字母数字与
    连字符）；② `bots/profiles/<名>.json` 在场且是合法 JSON 对象；③ **`form: interactive`**
    （理由见 `validate_profile`）。非法抛 ValueError（调用方回 400）。"""
    if not isinstance(name, str) or not PROFILE_NAME_OK.match(name.strip()):
        raise ValueError("profile name invalid: %r (expect ^[a-z0-9][a-z0-9-]{0,63}$)"
                         % (name,))
    n = name.strip()
    manifest = os.path.join(WS, "bots", "profiles", n + ".json")
    try:
        with open(manifest) as f:
            doc = json.load(f)
    except OSError:
        raise ValueError("profile not found: %s (no %s)" % (n, manifest))
    except ValueError:
        raise ValueError("profile manifest is not valid JSON: %s" % manifest)
    if not isinstance(doc, dict):
        raise ValueError("profile manifest is not a JSON object: %s" % manifest)
    form = doc.get("form")
    if form != "interactive":
        raise ValueError("profile %s: form=%r ≠ interactive（交互会话只放行声明 "
                         "form: interactive 的清单；task/resident/缺 form 一律拒）" % (n, form))
    return n, doc


def profile_cwd(name, doc=None):
    """profile 清单声明的**会话工作目录** → 项目白名单校验后的 realpath。
    `cwd` 是 profile 级运行环境字段（口径 = `bots/README.md`「人格资产」节）：新建交互会话时
    客户端只控 profile、cwd 从该清单读 ⇒ 客户端结构上控不到工作目录，而声明值仍走
    `resolve_project_cwd` 的白名单成员校验 + 逃逸校验（清单写错 = 拒建，⛔ 不回落）。"""
    if doc is None:
        name, doc = profile_spec(name)
    raw = doc.get("cwd", PROFILE_CWD_DEFAULT)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raw = PROFILE_CWD_DEFAULT
    try:
        return resolve_project_cwd(raw)
    except ValueError as e:
        raise ValueError("profile %s declares an unusable cwd: %s" % (name, e))


def list_interactive_profiles():
    """枚举 `form: interactive` 的 profile 清单 = **新建面的可选集**（页面链接与 CLI 的
    现值面；判据与 `profile_spec` 同源，⛔ 不另写一份）。返回
    `[{name, summary, cwd}]`（`cwd` = 清单声明原文，缺省 `"."`），按名排序；
    坏文件宽容跳过（记 WARN，⛔ 不整页失败）。只扫一层、零递归成本。"""
    pdir = os.path.join(WS, "bots", "profiles")
    out = []
    try:
        names = sorted(fn for fn in os.listdir(pdir) if fn.endswith(".json"))
    except OSError:
        return out
    for fn in names:
        path = os.path.join(pdir, fn)
        try:
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError) as e:
            log.warning("list_interactive_profiles: skip unreadable/invalid %s: %s",
                        path, e)
            continue
        if not isinstance(doc, dict) or doc.get("form") != "interactive":
            continue
        name = doc.get("name") or fn[:-len(".json")]
        if not isinstance(name, str) or not PROFILE_NAME_OK.match(name.strip()):
            log.warning("list_interactive_profiles: skip %s (invalid name %r)",
                        path, name)
            continue
        cwd = doc.get("cwd", PROFILE_CWD_DEFAULT)
        out.append({"name": name.strip(),
                    "summary": (doc.get("summary") or "") if isinstance(
                        doc.get("summary"), str) else "",
                    "cwd": cwd if isinstance(cwd, str) and cwd.strip()
                    else PROFILE_CWD_DEFAULT})
    return out


def new_session_name():
    """会话名（服务端自动生成，⛔ 客户端不控）：4 位小写字母/数字随机串（短名，
    用户拍板 2026-10-04：会话名是 tab bar 与列表页的**回落标题** ⇒ ⛔ 不编时间戳，
    时刻面 = `.agent` 的 `createdAt` 与列表的「最近活动」列）。
    撞名由 `create_files` 的有界重取吸收（⛔ 不覆盖）。⛔ 不复用
    `agentd/proto.auto_name()`：`w/` 是独立公开子仓、不 import 工作区其它仓的实现体。"""
    return "".join(random.choices(SESSION_NAME_ALPHABET, k=4))


def create_files(profile=CC_PROFILE, *, name_factory=None):
    """Create a private temporary session directory and its declaration.

    Creation reserves the session NAME, not a process launch. No host state or
    launch marker is manufactured. Only profile is caller-selected.
    """
    prof, pdoc = profile_spec(profile)
    cwd = profile_cwd(prof, pdoc)
    host = _self_host()
    if not host:
        raise ValueError('cannot determine local host (env/host-id missing or unmapped)')
    root = os.path.realpath(sessions_dir())
    if not _within_roots(root):
        raise ValueError('sessions dir escapes the workspace roots: %s' % root)
    os.makedirs(root, mode=0o700, exist_ok=True)
    for _ in range(SESSION_NAME_TRIES):
        name = (name_factory or new_session_name)()
        if not SESSION_NAME_OK.fullmatch(name):
            raise ValueError('invalid generated session name')
        # A legacy flat declaration/history still owns its name until an explicit
        # offline cutover. Do not launch another pi behind the old owner's back.
        if any(os.path.lexists(os.path.join(root, name + s)) for s in ('.agent', '.jsonl')):
            continue
        sdir = os.path.join(root, name)
        try:
            os.mkdir(sdir, mode=0o700)
            break
        except FileExistsError:
            continue
    else:
        raise ValueError('cannot allocate a free session name in bounded tries')
    agent_path = os.path.join(sdir, name + '.agent')
    jsonl_path = os.path.join(sdir, name + '.jsonl')
    doc = dict(host=host, cwd=cwd, sessionDir=sdir, profile=prof, name=name,
               createdAt=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
    tmp = os.path.join(sdir, '.tmp-' + uuid.uuid4().hex + '.agent')
    try:
        with open(tmp, 'x', encoding='utf-8') as f:
            os.chmod(tmp, 0o600)
            f.write(json.dumps(doc, ensure_ascii=False, indent=1) + '\n')
            f.flush()
            os.fsync(f.fileno())
        os.link(tmp, agent_path)
    finally:
        os.unlink(tmp)
    agent_site, session_site = site_path_of(agent_path), site_path_of(jsonl_path)
    if not agent_site or not session_site:
        raise ValueError('derived session paths escape site root')
    return dict(name=name, profile=prof, cwd=cwd, host=host, agent_site=agent_site,
                session_site=session_site, agent_path=agent_path, session_path=jsonl_path)


def session_title(jsonl_path, fallback):
    """自动标题（设计稿 §1 决策 4）：读配对 jsonl 的**首条 user 消息**，取其首个非空行
    截断到 `AUTO_TITLE_MAX`。两级回落（验收面）：① 无配对 jsonl ∨ 读不出 → `fallback`；
    ② jsonl 在场但扫不到 user 消息（新会话/只跑过系统帧）→ `fallback`。
    有界读：最多 `TITLE_SCAN_LINES` 行 ∧ `TITLE_SCAN_BYTES` 字节（大历史不拖垮列表页）。"""
    try:
        with open(jsonl_path, "rb") as f:
            head = f.read(TITLE_SCAN_BYTES)
    except OSError:
        return fallback                      # 回落①：无配对 jsonl
    seen = 0
    for raw in head.split(b"\n"):
        if not raw.strip():
            continue
        seen += 1
        if seen > TITLE_SCAN_LINES:
            break
        try:
            obj = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            continue
        if not isinstance(obj, dict) or obj.get("type") != "message":
            continue
        msg = obj.get("message")
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        text = _first_user_text(msg.get("content"))
        if text:
            return text[:AUTO_TITLE_MAX]
    return fallback                          # 回落②：无 user 消息


def _first_user_text(content):
    """user 消息 content 的首个非空文本行（content 可为 str ∨ [{type:text,text:…}]）。"""
    cands = []
    if isinstance(content, str):
        cands.append(content)
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                cands.append(part["text"])
    for c in cands:
        for line in c.splitlines():
            line = line.strip()
            if line:
                return line
    return ""


def list_files():
    """枚举本机 `run/sessiond/<name>/<name>.agent`（未删即列）→ 列表页/tab rpc 共用的数据面。
    每项 = {name, title, titleSource, project, cwd, state, pids, mtime, agent_site,
    session_site, profile, host}；坏文件宽容跳过（记 WARN，⛔ 不整页失败）。
    本层只枚举磁盘元数据；运行态由 Registry 的实例事实补齐，不猜测进程。
    排序 = 最近活动（jsonl ∨ .agent 的 mtime）降序。"""
    sdir = sessions_dir()
    try:
        names = sorted({fn[:-len(".agent")] if fn.endswith(".agent") else fn
                        for fn in os.listdir(sdir)
                        if fn.endswith(".agent") or (not os.path.islink(os.path.join(sdir, fn))
                        and os.path.isfile(os.path.join(sdir, fn, fn + '.agent')))})
    except OSError:
        return []
    rows = []
    for name in names:
        if not SESSION_NAME_OK.fullmatch(name):
            continue
        parent = os.path.join(sdir, name)
        if not os.path.isfile(os.path.join(parent, name + ".agent")):
            parent = sdir  # read-only legacy listing; launching requires migration
        agent_path = os.path.join(parent, name + ".agent")
        try:
            with open(agent_path, encoding="utf-8") as f:
                spec = json.load(f)
        except (OSError, ValueError) as e:
            log.warning("list_cc_sessions: skip unreadable/invalid %s: %s",
                        agent_path, e)
            continue
        if not isinstance(spec, dict):
            log.warning("list_cc_sessions: skip non-object %s", agent_path)
            continue
        jsonl_path = os.path.join(parent, name + ".jsonl")
        cwd = spec.get("cwd")
        rows.append({
            "name": name,
            "titleOverride": (spec.get("title").strip()
                              if isinstance(spec.get("title"), str)
                              and spec.get("title").strip() else None),
            "cwd": cwd if isinstance(cwd, str) else None,
            "project": _project_label(cwd),
            "profile": spec.get("profile"),
            "host": spec.get("host"),
            "jsonl": jsonl_path,
            "agent_site": site_path_of(os.path.realpath(agent_path)),
            "session_site": site_path_of(jsonl_path),
            "mtime": _mtime(jsonl_path) or _mtime(agent_path) or 0.0,
        })
    
    out = []
    for r in rows:
        pids = []
        title = r.pop("titleOverride")
        if title:
            src = "override"
        else:
            title = session_title(r["jsonl"], r["name"])
            src = "auto" if title != r["name"] else "name"
        r.update({"title": title, "titleSource": src,
                  "state": "unknown", "pids": pids})
        out.append(r)
    out.sort(key=lambda r: (-r["mtime"], r["name"]))
    return out


def _mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def _project_label(cwd):
    """分组键（列表页按项目分组）：cwd 相对 WS 的路径；WS 自身 → workspace 绝对路径；
    WS 外（手写的 .agent）→ realpath 原样，⛔ 不猜。"""
    if not isinstance(cwd, str) or not cwd:
        return "(unknown)"
    real = os.path.realpath(cwd)
    if real == WS_REAL:
        return WS
    if real.startswith(WS_REAL + os.sep):
        return WS.rstrip(os.sep) + '/' + os.path.relpath(real, WS_REAL).replace(os.sep, "/")
    return real


def session_files(name):
    """Exact managed declaration/history paths; reject aliased directories."""
    if not isinstance(name, str) or not SESSION_NAME_OK.fullmatch(name):
        raise ValueError('invalid session name')
    root = os.path.realpath(sessions_dir())
    if root == WS_REAL or WS_REAL.startswith(root + os.sep):
        raise ValueError('refusing workspace root as session directory')
    lexical = os.path.join(root, name)
    base = os.path.realpath(lexical)
    if base != lexical or not base.startswith(root + os.sep):
        raise ValueError('session directory is aliased or escapes root')
    out = []
    for suffix in ('.agent', '.jsonl'):
        p = os.path.realpath(os.path.join(base, name + suffix))
        if p != os.path.join(base, name + suffix):
            raise ValueError('session file is aliased or escapes directory')
        out.append(p)
    return out[0], out[1], base


def rename_files(name, title):
    """把标题 override 写进 `.agent` 的 `title` 字段（单一文件 = 单一事实源，⛔ 不加旁挂
    sidecar：delete 面维持「rm 两个文件」，且不会出现声明者已删而 sidecar 残留的孤儿）。
    空标题 = 清除 override（回落自动标题）。返回 {name, title, auto}。"""
    agent_path, jsonl_path, _base = session_files(name)
    if not os.path.exists(agent_path):
        raise ValueError("no such session: %s" % name)
    if not isinstance(title, str):
        raise ValueError("title must be a string")
    t = title.strip()
    if len(t) > TITLE_MAX:
        raise ValueError("title too long: %d chars (max %d)" % (len(t), TITLE_MAX))
    if any(ord(ch) < 0x20 for ch in t):
        raise ValueError("title must not contain control characters")
    try:
        with open(agent_path, encoding="utf-8") as f:
            spec = json.load(f)
    except ValueError:
        raise ValueError("agent declaration is not valid JSON: %s" % agent_path)
    if not isinstance(spec, dict):
        raise ValueError("agent declaration is not a JSON object: %s" % agent_path)
    if t:
        spec["title"] = t
    else:
        spec.pop("title", None)
    tmp = agent_path + ".tmp-%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(spec, ensure_ascii=False, indent=1) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, agent_path)
    auto = session_title(jsonl_path, name)
    log.info("renamed session %s: title=%r (auto=%r)", name, t or None, auto)
    return {"name": name, "title": t or auto, "auto": auto,
            "override": t or None}


def delete_files(name):
    """删除一枚临时会话的**文件系统面**：rm `.agent` + rm jsonl。
    只由 Registry 在核验从未启动或确认停止后调用；本函数不判断进程是否退出。
    删除纪律（全局红线「删除类操作」）：删前给身份证据（stat 三要素 + realpath）、
    逐个文件走 `session_files` 的 realpath 前缀断言（⛔ 不接受符号链接逃逸）、
    ⛔ 不用 `ignore_errors`、删后复核 `exists` 为假并把读数回给调用方。"""
    agent_path, jsonl_path, base = session_files(name)
    evidence = {}
    for label, p in (("agent", agent_path), ("jsonl", jsonl_path)):
        try:
            st = os.stat(p)
            evidence[label] = {"path": p, "realpath": os.path.realpath(p),
                               "dev": st.st_dev, "ino": st.st_ino,
                               "size": st.st_size}
        except OSError:
            evidence[label] = {"path": p, "realpath": os.path.realpath(p),
                               "absent": True}
    log.info("deleting session %s (dir=%s): %s", name, base,
             {k: {kk: vv for kk, vv in v.items() if kk != "path"}
              for k, v in evidence.items()})
    removed = []
    for label, p in (("agent", agent_path), ("jsonl", jsonl_path)):
        if evidence[label].get("absent"):
            continue
        os.unlink(p)                     # 失败即抛（⛔ 不吞、不 ignore_errors）
        removed.append(label)
    after = {"agent": os.path.exists(agent_path), "jsonl": os.path.exists(jsonl_path)}
    if any(after.values()):
        raise ValueError("post-delete check failed, still exists: %s"
                         % [k for k, v in after.items() if v])
    return {"name": name, "removed": removed, "evidence": evidence,
            "afterExists": after}




def sync_header_cwd(session_file, cwd):
    """会话文件首帧 cwd 对齐（任务 53yjuc，receiver-load-debug）：pi resume 时
    项目资源（项目级 `.pi/extensions` 扩展与项目 `AGENTS.md`）发现用会话文件**首帧
    记录的 cwd**（创建时值，SessionManager.open 无 cwdOverride：pi
    dist/core/session-manager.js open()；项目级扩展发现只查 <cwd>/.pi 无向上回溯：
    dist/core/package-manager.js projectBaseDir）——首帧 cwd 与宿主拉起 cwd 不一致时
    （典型：文件由旧代码在子目录创建、.agent 后改显式 cwd）项目资源整个不加载。
    本对齐保持项目 `AGENTS.md` 与项目级扩展的加载一致性。
    拉起前把首帧 cwd 改写为 cwd（其余行不动，
    临时文件+rename 原子落盘）。调用时机 = 串行启动操作内、旧宿主已清场、本线程独占，
    无并发写者。.jsonl 直开（缺省 cwd=dirname）首帧本就等于 dirname，无行为变化。"""
    try:
        with open(session_file, "rb") as f:
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
    if header.get("cwd") == cwd:
        return
    old = header.get("cwd")
    header["cwd"] = cwd
    tmp = session_file + ".hdr-tmp"
    try:
        with open(tmp, "wb") as f:
            f.write((json.dumps(header, ensure_ascii=False) + "\n").encode("utf-8"))
            f.write(rest)
        os.replace(tmp, session_file)
    except OSError as e:
        log.error("header cwd sync failed for %s: %s", session_file, e)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return
    log.info("header cwd synced %r -> %r for %s", old, cwd,
             session_file)


def session_name_from_site(session):
    """Resolve only /run/sessiond/<name>/<name>.agent|jsonl managed entries."""
    if not isinstance(session, str) or '\x00' in session or not session.startswith('/'):
        raise ValueError('session must be an absolute site path')
    p = posixpath.normpath(session)
    fn = posixpath.basename(p)
    name = next((fn[:-len(s)] for s in ('.jsonl', '.agent') if fn.endswith(s)), None)
    if not name or not SESSION_NAME_OK.fullmatch(name):
        raise ValueError('invalid managed session filename')
    root = site_path_of(os.path.realpath(sessions_dir()))
    if posixpath.dirname(p) != root + '/' + name:
        raise ValueError('not a managed session path; legacy flat sessions require offline migration')
    return name
