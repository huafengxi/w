# -*- type=script -*-
# sessiond 控制面 + 上行命令端点（任务 0829-1958-od0t；R1：路径路由）。
# 会话按路径管理（`session` 参数必填 = 站内 .jsonl 路径，如 /foo/bar/x.jsonl；
# 校验锁定 ~/m 内，见 proc.resolve_session_path），由 web 进程内各会话监督员
# （ext/sessiond/proc.py）直接监督，无管理面：
#   op=status  该会话状态（state/pid/gen/restarts/cwd/session_file）；另附斜杠状态行第 4 行
#              可见性三值（任务 bjhzvj；任务 8r0tww 补 socket 会话）：host（本机规范名，
#              env/host-id 查表）/sessionDir（= session_file dirname）/workDir（普通会话 =
#              会话进程真实在跑时的拉起 cwd，未拉起 → null；socket 会话 = 参与方
#              spec.json 声明的 workdir，读失败 → null。前端显 `?`，不猜测）
#   op=attach  建桥 + 经 pi rpc get_entries 返回消息基线（全量）
#   op=cmd     上行单条 pi 命令
#   op=commands 该会话实际加载的可发现命令清单（任务 a16jpj：pi rpc get_commands 转发；
#              extension 斜杠命令 / prompt 模板 / skill；仅注册了斜杠命令的 extension 出现）
#   op=inspect  探针转储（任务 s0f1la）：经 -e 注入的探针扩展命令 sessiond-inspect
#              取当前系统提示词全文 + 工具清单（侧车文件握手，见 bridge.py:inspect）；
#              不进事件环、不进 jsonl，payload 走本 HTTP 响应；
#              socket 会话同款支持：探针由封装脚本 pi-wrap/pi-rpc-wrap.py -e 注入
#   op=reload  进程级重载（杀会话进程并从该 .jsonl resume 重拉）
#   op=clear   保留会话路径、清空全部内容（杀会话进程→截断 jsonl 到 0+去 replica 标记→立即重拉，
#              0829-2238-atnj，4l3de8 翻案改截断；返回 {ok, gen, pid}）
#   op=agent   .agent 文件类型（任务 kcywpy；任务 fw2ll1 cwd/sessionDir 拆分）：
#              session = 站内 /…*.agent 路径；读规格 JSON（host + 可选 cwd/sessionDir/profile）→ 校验 →
#              cwd = 显式 `cwd` 字段（缺省 = .agent 文件所在目录），会话目录 = sessionDir（缺省 = .agent 所在目录，不存在自动创建）→
#              返回会话 jsonl 站内路径（= <sessionDir>/<name>.jsonl）与 cwd/sessionDir。
#              启动参数解析单点，host v1 仅保存/可见、不跨机拉起（见 design.md .agent 小节）。
# 指挥中心临时会话（任务 z293ql，设计稿 §4.2；消费方 = dash/sessions.md 工作区页与
# dash/sessions.py 端点）——声明者与 jsonl 都落宿主本地运行时区 `run/sessiond/`，
# 生命周期全在本层（⛔ 不经 agentd，web 自有 Supervisor spawn）：
#   op=create_session  新建：只收 `cwd`（相对 WS 的项目路径）→ 服务端枚举白名单成员校验
#              + 既有逃逸校验 → 现场写 .agent（host/cwd/sessionDir/profile/name/createdAt
#              全部服务端恒定或自动生成）→ 返回 {name, agent, session, chatUrl}
#   op=list_sessions   只读：本机 run/sessiond/*.agent 全量（自动标题/最近活动/运行态/
#              项目分组）+ 项目清单（新建表单的 <select> 选项）
#   op=rename_session  改标题 override（落 .agent 的 title 字段；空标题 = 清除回落自动标题）
#   op=delete_session  删除（需 confirm=1，轻确认一次、⛔ 不做 nonce，设计稿已裁）：
#              杀 Supervisor 进程 + rm .agent + rm jsonl（realpath 前缀断言 + 删前身份证据 + 删后复核）
#   op=session_tabs    只读：活跃会话的 tab 行数据（设计稿 §4.3 的 @dynamic rpc 数据源；
#              机器前缀按 _self_host() 派生，空态吐零行，⛔ 不标运行态）
# 鉴权由 w 全局 BasicAuth 承担；路径校验非法 → 400。
import json as _json
import logging as _logging
import os as _os
import posixpath as _posixpath
from ext.sessiond import bridge as _b
from ext.sessiond import proc as _proc

_log = _logging.getLogger("sessiond-agent")


def _j(obj, status='200 OK'):
    return dict(type='application/json', http_status=status), \
        _json.dumps(obj, ensure_ascii=False)


def _bridge_or_err(session):
    """按路径取桥接；缺失/非法路径返回错误响应元组，否则返回 (bridge, None)。
    前置 agentd 族入口归一（任务 60grqq）：spec.json 声明者入口 → 归一为会话产物路径；
    产物路径直开 → 400 + 指引（入口唯一化，用户拍板）。"""
    session, entry_err = _proc.agentd_entry(session)
    if entry_err:
        return None, _j({"ok": False, "error": entry_err}, '400 Bad Request')
    if not session:
        return None, _j({"ok": False,
                         "error": "missing required param: session"},
                        '400 Bad Request')
    try:
        return _b.get_bridge(session), None
    except ValueError as e:
        return None, _j({"ok": False, "error": str(e)}, '400 Bad Request')


def _baseline_doc(b, r):
    """把 get_entries 结果整理成基线响应文档。
    附 pendingDialogs（0830-0956-vk20 bug2）：尚悬空的 extension_ui_request
    请求体——extension UI 请求不在会话 entries 里，前端刷新/重连后仅凭基线
    无法重建 pending dialog，由后端权威补发。"""
    resp = r["resp"]
    if not resp.get("success"):
        return None
    data = resp.get("data") or {}
    entries = data.get("entries") or []
    return {"ok": True, "session": b.session,
            "gen": r.get("gen"), "entries": entries,
            "pendingDialogs": b.pending_dialog_list(),
            "queue": b.last_queue_state(),
            "leafId": data.get("leafId"),
            "watermark": r.get("watermark")}


# ---------------- agentd 控制面代理（任务 dsuqbi） ----------------
# socket-mode（agentd_route）会话的生命周期属 agentd runner，sessiond 无杀权：
# 会话级 clear/reload 改道为向参与方 control/ 写控制请求（与 stop/pause/restart 同族
# 协议，见 agentd/agent-file-protocol.md §4.3/§5.2）：写请求 → 轮询等回执 → 返回结果。
#   op=clear  → control/clear（弃历史换代：杀进程→备份会话文件→截断→空白新代）
#   op=reload → control/restart（保历史换代）
# 跨机维持拒绝：建桥咽喉（agentd_route）已拒跨机会话，此处再按 spec.host 纵深防御一道。

AGENTD_CONTROL_TIMEOUT = 30.0   # 等回执超时（杀进程+备份+换代通常秒级）
AGENTD_CONTROL_POLL = 0.2


def _agentd_control_proxy(b, action, reason):
    """socket 会话代理 control 动词：写 <adir>/control/<id>.req → 轮询 control/ack/<id>。
    返回响应元组：{ok, session, gen?, outcome?, detail?}。"""
    sup = b.sup
    pid_ = getattr(sup, "participant_id", None)
    if not pid_ or pid_.count("/") != 1:
        return _j({"ok": False,
                   "error": "socket session without participant id; cannot "
                            "proxy control/%s" % action}, '500 Internal Server Error')
    family, name = pid_.split("/", 1)
    adir = _os.path.join(_proc.WS, "agents", family, name)
    # 跨机纵深防御（拉起/生命周期归登记机；建桥咽喉已拦一道）
    try:
        with open(_os.path.join(adir, "spec.json")) as f:
            host = (_json.load(f) or {}).get("host")
    except (OSError, ValueError):
        host = None
    me = _proc._self_host()
    if isinstance(host, str) and host.strip() and me is not None \
            and host.strip() != me:
        return _j({"ok": False,
                   "error": "该会话宿主=%s，控制面请通过 %s 的服务发起（本机=%s）"
                            % (host.strip(), host.strip(), me)}, '403 Forbidden')
    # 不依赖 agentd/proto（w 仓独立）：自拼信封文件名与原子落盘（口径同协议 §4.3/§5.1）
    import random as _random
    import string as _string
    import time as _time
    ts = _time.strftime("%Y-%m-%d-%H-%M-%S", _time.localtime()) + \
        (".%03d" % (int(_time.time() * 1000) % 1000))
    rand = "".join(_random.choices(_string.ascii_lowercase + _string.digits, k=4))
    rid = ts + "-web.sessiond-" + rand
    cdir = _os.path.join(adir, "control")
    req = {"id": rid, "from": "web.sessiond", "ts": ts,
           "action": action, "reason": reason}
    try:
        _os.makedirs(cdir, exist_ok=True)
        tmp = _os.path.join(cdir, ".tmp-%d-%s" % (_os.getpid(), rand))
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(_json.dumps(req, ensure_ascii=False, indent=1) + "\n")
            f.flush()
            _os.fsync(f.fileno())
        _os.rename(tmp, _os.path.join(cdir, rid + ".req"))
    except OSError as e:
        return _j({"ok": False,
                   "error": "write control/%s request failed: %s" % (action, e)},
                  '500 Internal Server Error')
    _log.info("agentd control proxy: %s %s -> %s", pid_, action, rid)
    ack_path = _os.path.join(cdir, "ack", rid)
    deadline = _time.monotonic() + AGENTD_CONTROL_TIMEOUT
    ack = None
    while _time.monotonic() < deadline:
        try:
            with open(ack_path) as f:
                ack = _json.load(f)
            break
        except OSError:
            _time.sleep(AGENTD_CONTROL_POLL)
        except ValueError:
            ack = None
            _time.sleep(AGENTD_CONTROL_POLL)  # 半截文件下轮重读（协议 §11.6 同口径）
    if ack is None:
        return _j({"ok": False,
                   "error": "control/%s ack not received within %.0fs "
                            "(req %s)" % (action, AGENTD_CONTROL_TIMEOUT, rid)},
                  '504 Gateway Timeout')
    outcome = ack.get("outcome")
    detail = ack.get("detail", "")
    if outcome == "rejected":
        return _j({"ok": False, "session": b.session, "outcome": outcome,
                   "error": "control/%s rejected: %s" % (action, detail)},
                  '409 Conflict')
    gen = None
    try:
        with open(_os.path.join(adir, "pid.json")) as f:
            gen = (_json.load(f) or {}).get("gen")
    except (OSError, ValueError):
        pass
    return _j({"ok": True, "session": b.session, "gen": gen,
               "outcome": outcome, "detail": detail,
               "controlReq": rid})


# ---------------- .agent 文件类型（任务 kcywpy；fw2ll1 cwd/sessionDir 拆分） ----------------
# xxx.agent = agent 规格 JSON（字段参考 ~/m/agents/ 任务 spec.json 风格，多余字段宽容）。
# 访问 /xxx.agent → 聊天视图，会话启动参数改从该 JSON 读取（用户拍板 2026-08-31）：
#   - 字段 = 必填 `host` + 可选 `cwd`/`sessionDir`/`name`/`profile`/`title`（后两枚 = 任务
#     z293ql 增，语义见 design.md「.agent 文件类型」节）；旧 `workdir` 不再识别（读到忽略并日志提示）。
#   - 会话进程 cwd = 显式 `cwd` 字段（~/ 展开，安全红线同 sessionDir；2026-08-31 用户拍板，
#     票 7t0ufv）；缺省回退 = .agent 文件所在目录。cwd 决定 pi 加载哪个工作区的**项目级**
#     资源（该 cwd 的 AGENTS.md 与 <cwd>/.pi/extensions）；⚠ agentd 的工具面**不靠 cwd**——
#     该扩展已迁全局装载面 pi-core/agent/extensions/agentd/（自动发现、cwd 无关），可用性由
#     profile 的工具白名单 gate（现行例子 = run/sessiond/<name>.agent 的 cwd 指向某个项目目录，
#     见 op=create_session 与 ARCHITECTURE.md §12）。
#   - sessionDir = 会话目录（会话 jsonl 落盘处，宿主本地运行时状态，*.jsonl 全局 gitignore；
#     经 2026-08-31 票 7t0ufv 拍板不再共享参与方同步目录）；缺省 = .agent 文件所在目录。
#   - 会话 jsonl = <sessionDir>/<agent名>.jsonl（每 agent 一会话、可重连续聊，如
#     nv1-dispatcher.agent → participant/dispatcher/nv1-dispatcher.jsonl，任务 7pwnpa）。
#     .jsonl 直开路径不走本约定（cwd 仍 = dirname）。
# 本函数 = 启动参数解析单点，将来加 host 跨机路由（反向通道/各机 8080 代理）只改这里。
# host v1 边界：仅持久化保存（存于 .agent 文件）+ 随响应返回 + 聊天页可见；
# 会话一律在本 8080 实例所在机器本地拉起（sessiond 现状即本地监督），不做跨机拉起。
# 警示：跨机打开他人 .agent 会在本机另起会话写同一同步文件，host 路由落地前勿在
# 其它机器打开非本机 host 的 .agent（见 design.md）。


def _resolve_agent(store, session):
    """解析 .agent 规格。成功返回响应元组（200 + 规格文档），失败返回错误元组：
    字段约定（任务 fw2ll1，用户拍板 2026-08-31；2026-08-31 票 7t0ufv 加显式 `cwd`）：
    只留 `host` + 可选 `cwd`/`sessionDir`；
    cwd = 显式 `cwd` 字段（~/ 展开，不得逃逸 ~/m + run 运行时区），缺省回退 = .agent 文件
    所在目录（决定 pi 加载哪个工作区的 AGENTS/扩展）；
    sessionDir = 会话目录（jsonl 落盘处，宿主本地运行时状态，缺省 = .agent 文件所在目录）；旧 `workdir`
    字段不再识别（读到忽略并日志提示）。成功返回响应元组（200 + 规格文档），
    失败返回错误元组：文件缺失 → 404；路径非法/坏 JSON/缺 host/sessionDir 非法或逃逸 ~/m → 400
    （对齐 w 既有缺失/错误处理口径）。"""
    if not session:
        return _j({"ok": False, "error": "missing required param: session"},
                  '400 Bad Request')
    p = _posixpath.normpath(session) if isinstance(session, str) else ""
    if not p.startswith("/") or not p.endswith(".agent") or p == "/":
        return _j({"ok": False,
                   "error": "agent path must be /<name>.agent, got %r" % (session,)},
                  '400 Bad Request')
    name = _os.path.basename(p)[:-len(".agent")]
    if not name:
        return _j({"ok": False, "error": "empty agent name in %r" % (session,)},
                  '400 Bad Request')
    try:
        raw = store.read(p)
    except (OSError, ValueError) as e:
        return _j({"ok": False,
                   "error": "agent file not found or unreadable: %s (%s)" % (p, e)},
                  '404 Not Found')
    if raw is None:
        return _j({"ok": False,
                   "error": "agent file not found: %s" % p},
                  '404 Not Found')
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    try:
        spec = _json.loads(raw)
    except ValueError as e:
        return _j({"ok": False,
                   "error": "agent file %s is not valid JSON: %s" % (p, e)},
                  '400 Bad Request')
    if not isinstance(spec, dict):
        return _j({"ok": False,
                   "error": "agent file %s: top-level must be a JSON object" % p},
                  '400 Bad Request')
    missing = [k for k in ("host",)
               if not isinstance(spec.get(k), str) or not spec.get(k).strip()]
    if missing:
        return _j({"ok": False,
                   "error": "agent file %s: missing or empty field(s): %s"
                            % (p, ", ".join(missing))},
                  '400 Bad Request')
    host = spec["host"].strip()
    # 旧 workdir 字段（kcywpy v1）不再识别：忽略并日志提示，不报错（用户拍板）。
    if "workdir" in spec:
        _log.info("agent %s: legacy 'workdir' field ignored "
                  "(cwd = 显式 cwd 字段，缺省 .agent 所在目录)", name)
    # cwd：优先显式 `cwd` 字段（2026-08-31 用户拍板，票 7t0ufv：cwd 不再靠文件位置隐含表达；
    # 支持 ~/ 展开；安全红线同 sessionDir = 不得逃出 ~/m + run 运行时区）；
    # 缺省回退 = .agent 文件所在目录（站内路径已校验在 ~/m 内；resolve_cwd 再过一道）。
    cwd_val = spec.get("cwd")
    if cwd_val is not None:
        if not isinstance(cwd_val, str) or not cwd_val.strip():
            return _j({"ok": False,
                       "error": "agent file %s: 'cwd' must be a non-empty "
                                "string when present" % p},
                      '400 Bad Request')
        cwd_src = _os.path.realpath(_os.path.expanduser(cwd_val.strip()))
        roots = [_proc.WS_REAL,
                 _os.path.realpath(_os.path.join(_proc.WS, "run"))]
        if not any(cwd_src == r or cwd_src.startswith(r + _os.sep) for r in roots):
            return _j({"ok": False,
                       "error": "agent file %s: cwd escapes workspace: %s"
                                % (p, cwd_val)},
                      '400 Bad Request')
    else:
        cwd_site_dir = _posixpath.dirname(p)
        cwd_src = (_proc.WS if cwd_site_dir in ("", "/")
                   else _os.path.join(_proc.WS, cwd_site_dir.lstrip("/")))
    try:
        cwd = _proc.resolve_cwd(cwd_src)
    except ValueError as e:
        return _j({"ok": False,
                   "error": "agent file %s: cwd rejected: %s"
                            % (p, e)},
                  '400 Bad Request')
    # profile（任务 z293ql，设计稿 §7.3）：可选字段 = 会话人格输入（spawn 时回注
    # DISPATCH_PROFILE，由全局 profile-loader 扩展兼现人格/model/form 档）。严格白名单：
    # 名字形态 + `bots/profiles/<名>.json` 在场 + **只放行 form: interactive**
    #（proc.validate_profile；给交互会话配错形态 = 静默失能）。缺省 = 不注 ⇒ 裸 pi 会话
    #（存量 .agent 逐字不变）。
    prof_val = spec.get("profile")
    profile = None
    if prof_val is not None:
        try:
            profile = _proc.validate_profile(prof_val)
        except ValueError as e:
            return _j({"ok": False,
                       "error": "agent file %s: %s" % (p, e)},
                      '400 Bad Request')
    # sessionDir = 会话目录：可选字段；缺省 = .agent 文件所在目录（2026-08-31 用户拍板，
    # 票 7t0ufv：会话 jsonl = 宿主本地运行时状态，落 agent 自家目录，不共享 cwd 工作区/
    # 参与方同步目录）。
    dir_val = spec.get("sessionDir")
    if dir_val is None:
        agent_site_dir = _posixpath.dirname(p)
        sess_dir_src = (_proc.WS if agent_site_dir in ("", "/")
                        else _os.path.join(_proc.WS, agent_site_dir.lstrip("/")))
        sess_dir = _os.path.realpath(sess_dir_src)
    else:
        if not isinstance(dir_val, str) or not dir_val.strip():
            return _j({"ok": False,
                       "error": "agent file %s: 'sessionDir' must be a non-empty "
                                "string when present" % p},
                      '400 Bad Request')
        sess_dir = _os.path.expanduser(dir_val.strip())
        # sessionDir 安全红线：与会话路径同一根集（~/m + run 运行时区），防穿越/逃逸。
        real = _os.path.realpath(sess_dir)
        roots = [_proc.WS_REAL,
                 _os.path.realpath(_os.path.join(_proc.WS, "run"))]
        if not any(real == r or real.startswith(r + _os.sep) for r in roots):
            return _j({"ok": False,
                       "error": "agent file %s: sessionDir escapes workspace: %s"
                                % (p, dir_val)},
                      '400 Bad Request')
        sess_dir = real
    # sessionDir 不存在 → 自动创建（含 participant/ 中间层），创建行为记日志。
    if not _os.path.isdir(sess_dir):
        try:
            _os.makedirs(sess_dir, exist_ok=True)
        except OSError as e:
            return _j({"ok": False,
                       "error": "agent file %s: cannot create sessionDir %s: %s"
                                % (p, sess_dir, e)},
                      '500 Internal Server Error')
        _log.info("agent %s: auto-created sessionDir %s", name, sess_dir)
    # 会话 = <sessionDir>/<name>.jsonl（每 agent 一会话）；站内路径经既有校验再过一道。
    # 站内相对路径推导（任务 kqhweh，ticket.gwj1xr ②）：sess_dir 已是 realpath，若 .agent/
    # sessionDir 经 ~/m/run 软链（→ /data/…/run）落在运行时区，relpath(sess_dir, WS) 会产出
    # `../…` 畸形站内路径（旧代码未拦，session_file 解析成不存在位置）。先试 WS 锚定，逃逸则
    # 回退 run 软链锚定（还原为 /run/… 站内路径）；两者都逃逸 → 400。
    rel_dir = _os.path.relpath(sess_dir, _proc.WS)
    if rel_dir == ".." or rel_dir.startswith(".." + _os.sep):
        run_real = _os.path.realpath(_os.path.join(_proc.WS, "run"))
        rel_run = _os.path.relpath(sess_dir, run_real)
        if rel_run == ".." or rel_run.startswith(".." + _os.sep):
            return _j({"ok": False,
                       "error": "agent file %s: sessionDir %s escapes the site root "
                                "(rel to %s = %s)" % (p, sess_dir, _proc.WS, rel_dir)},
                      '400 Bad Request')
        rel_dir = _posixpath.normpath(_posixpath.join("run", rel_run))
    site_jsonl = "/" + _posixpath.normpath(_posixpath.join(rel_dir, name + ".jsonl"))
    try:
        session_file = _proc.resolve_session_path(site_jsonl)
    except ValueError as e:
        return _j({"ok": False,
                   "error": "agent file %s: derived session path rejected: %s"
                            % (p, e)},
                  '400 Bad Request')
    # 登记显式拉起参数（任务 fw2ll1 cwd；任务 z293ql profile）：该会话路径懒建桥接时
    # 按此 cwd/profile 拉起，不再恒等于 dirname(session_file) / 裸会话。
    _b.set_session_cwd(site_jsonl, cwd, profile)
    return _j({"ok": True, "name": name, "host": host, "cwd": cwd,
               "profile": profile,
               "sessionDir": sess_dir, "session": site_jsonl,
               "session_file": session_file,
               "hostRouting": "v1: session spawns locally on this 8080 host; "
                              "host is stored for future cross-host routing"})


# ---------------- 指挥中心临时会话（任务 z293ql，设计稿 §4.2/§4.3） ----------------
# 实现面（枚举/校验/写盘/删除纪律）单点在 proc.py；进程面（杀 Supervisor）在
# bridge.drop_session；本层只做参数面、编排与响应形状。消费方 = dash/sessions.py
# （工作区页的表单与列表）与 dash/session-tabs.py（@dynamic 的 itab 行 rpc）。

# 服务端恒定/自动生成的字段：客户端传任一 ⇒ 400 显式拒绝（⛔ 不只是忽略）。
# `command` 在列 = create_bot 时代的硬约束同族：spawn argv 的单点是
# proc.Supervisor._spawn，声明者不可控（故 .agent 字段集里根本没有这个键）。
_SERVER_FIXED_FIELDS = ("command", "profile", "sessionDir", "name", "host")


def _fixed_field_rejection(kw):
    """客户端传了服务端恒定字段 → 400 响应元组；都没传 → None。"""
    hit = sorted(k for k in _SERVER_FIXED_FIELDS
                 if kw.get(k) not in (None, ""))
    if not hit:
        return None
    return _j({"ok": False,
               "error": "field(s) %s are server-side fixed or auto-generated for "
                        "command-center sessions; the client controls only 'cwd'"
                        % ", ".join(hit)},
              '400 Bad Request')


def _cc_name(session):
    """op 参数 session（站内 .jsonl ∨ .agent 路径）→ 临时会话名。
    只认 `run/sessiond/` 下的文件（与 proc.session_files 的名字白名单、realpath 断言
    组成两道）；非法抛 ValueError（调用方回 400）。"""
    if not isinstance(session, str) or "\x00" in session \
            or not session.startswith("/"):
        raise ValueError("session must be an absolute site path: %r" % (session,))
    p = _posixpath.normpath(session)
    base = _posixpath.dirname(p)
    fn = _posixpath.basename(p)
    name = None
    for suf in (".jsonl", ".agent"):
        if fn.endswith(suf):
            name = fn[:-len(suf)]
            break
    if not name:
        raise ValueError("session must end with .jsonl or .agent: %r" % (session,))
    want = _proc.site_path_of(_os.path.realpath(_proc.sessions_dir()))
    if base != want:
        raise ValueError("not a command-center session path (expect %s/<name>."
                         "jsonl|.agent, got %r)" % (want, session))
    return name


def _prefix():
    """机器前缀 = 本机规范名（env/host-id 查表，口径同 agentd/report.py:chat_url 的 host）。
    ⛔ 不从请求路径派生：代理 `strip_prefix: true`（ext/proxy/proxy.py:forward）⇒ 被请求机
    看到的 path 不含 `/dev/` 一类前缀。不可得 → None（调用方吐零行，⛔ 不猜、⛔ 不发无前缀链接）。"""
    return _proc._self_host()


def _chat_url(agent_site, host):
    return "/%s%s?v=chat" % (host, agent_site) if host and agent_site else ""


def _create_session(kw):
    rej = _fixed_field_rejection(kw)
    if rej:
        return rej
    try:
        doc = _proc.create_cc_session(kw.get("cwd"))
    except ValueError as e:
        return _j({"ok": False, "error": str(e)}, '400 Bad Request')
    host = doc["host"]
    return _j({"ok": True, "created": True, "name": doc["name"],
               "profile": doc["profile"], "cwd": doc["cwd"], "host": host,
               "sessionDir": _os.path.realpath(_proc.sessions_dir()),
               "agent": doc["agent_site"], "session": doc["session_site"],
               "chatUrl": _chat_url(doc["agent_site"], host),
               "ephemeral": "run/ 是宿主本地运行时区：被清即丢会话（设计稿已接受）"})


def _list_sessions():
    host = _prefix()
    sessions = []
    for r in _proc.list_cc_sessions():
        sessions.append({
            "name": r["name"], "title": r["title"],
            "titleSource": r["titleSource"], "project": r["project"],
            "cwd": r["cwd"], "profile": r["profile"], "host": r["host"],
            "state": r["state"], "pids": r["pids"], "mtime": r["mtime"],
            "agent": r["agent_site"], "session": r["session_site"],
            "chatUrl": _chat_url(r["agent_site"], host),
        })
    projects = [{"rel": rel, "path": real,
                 "label": ("~/m（工作区根）" if rel == "." else "~/m/" + rel)}
                for rel, real in _proc.list_projects()]
    return _j({"ok": True, "host": host, "prefix": host or "",
               "sessionsDir": _proc.site_path_of(
                   _os.path.realpath(_proc.sessions_dir())),
               "sessions": sessions, "projects": projects})


def _session_tabs():
    """设计稿 §4.3：@dynamic 的数据源。空态 = 零行；⛔ 不标运行态（tab 只管快速进，
    live/dormant 看工作区）；⛔ 不轮询（frame 加载时 fetch 一次 = 快照）。"""
    host = _prefix()
    if not host:
        _log.warning("session_tabs: local host undeterminable (env/host-id); "
                     "emitting zero rows")
        return _j({"ok": True, "prefix": None, "rows": []})
    rows = []
    for r in _proc.list_cc_sessions():
        url = _chat_url(r["agent_site"], host)
        if not url:
            continue
        rows.append({"name": r["name"], "title": r["title"], "url": url})
    return _j({"ok": True, "prefix": host, "rows": rows})


def _rename_session(session, kw):
    rej = _fixed_field_rejection(kw)
    if rej:
        return rej
    try:
        doc = _proc.rename_cc_session(_cc_name(session), kw.get("title"))
    except ValueError as e:
        return _j({"ok": False, "error": str(e)}, '400 Bad Request')
    return _j(dict(ok=True, **doc))


def _delete_session(session, kw):
    rej = _fixed_field_rejection(kw)
    if rej:
        return rej
    if str(kw.get("confirm") or "").strip() != "1":
        return _j({"ok": False,
                   "error": "missing confirm=1 (轻确认一次；删除会杀会话进程并 rm "
                            ".agent 与 jsonl，不可撤销)"},
                  '400 Bad Request')
    try:
        name = _cc_name(session)
        _agent_path, jsonl_path, _base = _proc.session_files(name)
    except ValueError as e:
        return _j({"ok": False, "error": str(e)}, '400 Bad Request')
    site = _proc.site_path_of(jsonl_path)
    if site is None:
        return _j({"ok": False,
                   "error": "session path escapes the site root: %s" % jsonl_path},
                  '400 Bad Request')
    # ① 进程面：先杀 Supervisor（bridge.drop_session 绝不建桥 ⇒ 删一枚从未打开过的
    #    会话不会反而把它拉起来）；未命中桥接时再按 environ 标记扫残留宿主兼顶。
    res = _b.drop_session(site)
    if not res.get("found"):
        stale = _proc.kill_hosts(jsonl_path)
        if stale:
            res = dict(res, killed=True, staleHosts=stale)
    # ② 文件系统面：realpath 断言 + 删前身份证据 + 删后复核全在 proc 层。
    try:
        doc = _proc.delete_cc_session(name)
    except (ValueError, OSError) as e:
        return _j({"ok": False, "error": "delete failed: %s" % e,
                   "process": res}, '500 Internal Server Error')
    _log.info("deleted command-center session %s (process=%s)", name, res)
    return _j(dict(ok=True, process=res, **doc))


def interp(store, op='', session='', cmd='', **kw):
    if op == 'agent':
        # .agent 规格解析（任务 kcywpy）：不走会话桥接——会话路径由规格文件推导，
        # 前端拿推导结果再走正常 attach。
        return _resolve_agent(store, session)
    if op in ('create_session', 'list_sessions', 'rename_session',
              'delete_session', 'session_tabs'):
        # 指挥中心临时会话（任务 z293ql）：五个 op 都**不走** _bridge_or_err —— 建桥会
        # 为「还没打开过 ∨ 即将被删」的会话拉起进程（create/list/session_tabs 无需会话，
        # rename/delete 按 session 参数定位 run/sessiond/ 下那一枚；杀进程走
        # bridge.drop_session 的「绝不建桥」查表）。
        if op == 'create_session':
            return _create_session(kw)
        if op == 'list_sessions':
            return _list_sessions()
        if op == 'session_tabs':
            return _session_tabs()
        if op == 'rename_session':
            return _rename_session(session, kw)
        return _delete_session(session, kw)
    b, err = _bridge_or_err(session)
    if err:
        return err
    if op == 'status':
        doc = dict(ok=True, **b.sup.status_doc())
        # 斜杠状态行第 4 行可见性三值（任务 bjhzvj；任务 8r0tww 补 socket 会话）：
        # 服务端既有数据，无新造——host 复用 _self_host()（env/host-id 查表，同
        # host_guard 口径）；sessionDir = session_file 的 dirname；workDir 分两路：
        # socket 会话（agentd 族）= 参与方 spec.json 声明的 workdir（= runner 拉起
        # 会话进程的 cwd，任务 8r0tww），读取失败 → null；普通会话 = 会话进程真实在跑
        # （state=running ∧ pid）时 status_doc 的拉起 cwd，未拉起 → null。缺值前端显 `?`。
        # 既有 cwd 字段原样保留（调试口径不变）。
        doc["host"] = _proc._self_host()
        sf = doc.get("session_file") or ""
        doc["sessionDir"] = _os.path.dirname(sf) if sf else None
        if isinstance(b.sup, _proc.SocketSupervisor):
            doc["workDir"] = _proc.agentd_spec_workdir(b.sup.participant_id)
        else:
            doc["workDir"] = doc.get("cwd") if (doc.get("state") == "running"
                                                and doc.get("pid")) else None
        return _j(doc)
    if op == 'attach':
        r = b.get_entries()                      # 全量基线
        if not r["ok"]:
            return _j({"ok": False, "session": b.session,
                       "error": "baseline failed: %s" % r["error"]},
                      '504 Gateway Timeout')
        doc = _baseline_doc(b, r)
        if doc is None:
            return _j({"ok": False, "session": b.session,
                       "error": "get_entries rejected: %s"
                       % (r["resp"].get("error") or "?")}, '502 Bad Gateway')
        return _j(doc)
    if op == 'commands':
        # 任务 a16jpj：pi rpc get_commands 只读查询（结构同 get_entries 配对等待，
        # 见 bridge.py:get_commands）；失败口径对齐 attach（502）。
        r = b.get_commands()
        if not r["ok"]:
            return _j({"ok": False, "session": b.session,
                       "error": "get_commands failed: %s" % r["error"]},
                      '502 Bad Gateway')
        return _j({"ok": True, "session": b.session,
                   "commands": r["commands"]})
    if op == 'inspect':
        # socket 会话（agentd 任务/常驻）：探针扩展由封装脚本 pi-wrap/pi-rpc-wrap.py
        # -e 注入（与 proc.py:_spawn 同款），握手链路复用 b.inspect()——只读转储，
        # 无生命周期动作，不再拒绝（原 403 口径废除）；探针未加载（存量旧进程）
        # 则回 502「probe dump file missing」。
        r = b.inspect()
        if not r["ok"]:
            return _j({"ok": False, "session": b.session,
                       "error": "inspect failed: %s" % r["error"]},
                      '502 Bad Gateway')
        return _j({"ok": True, "session": b.session,
                   "inspect": r["doc"]})
    if op == 'reload':
        if isinstance(b.sup, _proc.SocketSupervisor):
            # socket 会话：代理 control/restart（保历史换代，任务 dsuqbi）
            b.note_activity()
            return _agentd_control_proxy(
                b, "restart", "user /reload via socket session")
        b.note_activity()   # 去保活：主动操作计在场/空闲时钟（任务 ja0vr7）
        r = b.sup.reload()
        if not r.get("ok"):
            return _j({"ok": False,
                       "error": "reload failed: %s" % r.get("error")},
                      '502 Bad Gateway')
        return _j({"ok": True, "session": b.session,
                   "gen": r.get("gen"), "pid": r.get("pid")})
    if op == 'clear':
        if isinstance(b.sup, _proc.SocketSupervisor):
            # socket 会话：代理 control/clear（弃历史换代：杀进程→备份→截断→空白新代，
            # 任务 dsuqbi）；跨机参与者维持拒绝（建桥咽喉 + 代理内纵深校验）。
            b.note_activity()
            return _agentd_control_proxy(
                b, "clear", "user /clear via socket session")
        # 保留会话路径、清空全部内容：杀会话进程→截断 jsonl 到 0 + 去 replica 标记→立即重拉（顺序在
        # Supervisor.clear 内保证：先杀透再删，防旧进程把内存历史回写）。
        b.note_activity()   # 去保活：主动操作计在场/空闲时钟（任务 ja0vr7）
        r = b.sup.clear()
        if not r.get("ok"):
            return _j({"ok": False,
                       "error": "clear failed: %s" % r.get("error")},
                      '502 Bad Gateway')
        return _j({"ok": True, "session": b.session,
                   "gen": r.get("gen"), "pid": r.get("pid")})
    if op == 'cmd':
        try:
            cmd_obj = _json.loads(cmd) if cmd else None
        except ValueError:
            return _j({"ok": False, "error": "cmd is not valid JSON"},
                      '400 Bad Request')
        if not isinstance(cmd_obj, dict) or not cmd_obj.get('type'):
            return _j({"ok": False, "error": "cmd must be an object with type"},
                      '400 Bad Request')
        err = b.send(cmd_obj)
        if err:
            return _j({"ok": False, "error": err}, '403 Forbidden')
        return _j({"ok": True})
    return _j({"ok": False, "error": "unknown op %r" % (op,)}, '400 Bad Request')
