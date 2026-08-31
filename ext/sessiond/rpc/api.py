# -*- type=script -*-
# sessiond 控制面 + 上行命令端点（任务 0829-1958-od0t；R1：路径路由）。
# 会话按路径管理（`session` 参数必填 = 站内 .jsonl 路径，如 /assistant/foo.jsonl；
# 校验锁定 ~/m 内，见 proc.resolve_session_path），由 web 进程内各会话监督员
# （ext/sessiond/proc.py）直接监督，无管理面：
#   op=status  该会话状态（state/pid/gen/restarts/cwd/session_file）
#   op=attach  建桥 + 经 pi rpc get_entries 返回消息基线（全量）
#   op=cmd     上行单条 pi 命令
#   op=reload  进程级重载（杀会话进程并从该 .jsonl resume 重拉）
#   op=clear   保留会话路径、清空全部内容（杀会话进程→删 jsonl→立即重拉，
#              0829-2238-atnj；返回 {ok, gen, pid}）
#   op=agent   .agent 文件类型（任务 kcywpy；任务 fw2ll1 cwd/dir 拆分）：
#              session = 站内 /…*.agent 路径；读规格 JSON（host + 可选 dir）→ 校验 →
#              cwd = .agent 文件所在目录，会话目录 = dir（缺省 = cwd 目录，不存在自动创建）→
#              返回会话 jsonl 站内路径（= <dir>/<name>.jsonl）与 cwd/dir。
#              启动参数解析单点，host v1 仅保存/可见、不跨机拉起（见 design.md .agent 小节）。
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
    """按路径取桥接；缺失/非法路径返回错误响应元组，否则返回 (bridge, None)。"""
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


# ---------------- .agent 文件类型（任务 kcywpy；fw2ll1 cwd/dir 拆分） ----------------
# xxx.agent = agent 规格 JSON（字段参考 ~/m/agents/ 任务 spec.json 风格，多余字段宽容）。
# 访问 /xxx.agent → 聊天视图，会话启动参数改从该 JSON 读取（用户拍板 2026-08-31）：
#   - 字段只留 `host` + 可选 `dir`；旧 `workdir` 不再识别（读到忽略并日志提示）。
#   - 会话进程 cwd = .agent 文件所在目录（决定 pi 加载哪个工作区的 AGENTS/扩展，
#     如 ~/m/assistant/*.agent → cwd=~/m/assistant → 命中 assistant/.pi 全套扩展）。
#   - dir = 会话目录（会话 jsonl 落盘处，也是跨机可观测的 participant 目录，经
#     agents-sync 四机同步）；缺省 = cwd 目录（会话文件落在 .agent 旁边）。
#   - 会话 jsonl = <dir>/<agent名>.jsonl（每 agent 一会话、可重连续聊，类比
#     /assistant/dispatcher.jsonl 的组织）。.jsonl 直开路径不走本约定（cwd 仍 = dirname）。
# 本函数 = 启动参数解析单点，将来加 host 跨机路由（反向通道/各机 8080 代理）只改这里。
# host v1 边界：仅持久化保存（存于 .agent 文件）+ 随响应返回 + 聊天页可见；
# 会话一律在本 8080 实例所在机器本地拉起（sessiond 现状即本地监督），不做跨机拉起。
# 警示：跨机打开他人 .agent 会在本机另起会话写同一同步文件，host 路由落地前勿在
# 其它机器打开非本机 host 的 .agent（见 design.md）。


def _resolve_agent(store, session):
    """解析 .agent 规格。成功返回响应元组（200 + 规格文档），失败返回错误元组：
    字段约定（任务 fw2ll1，用户拍板 2026-08-31）：只留 `host` + 可选 `dir`；
    cwd 隐含 = .agent 文件所在目录（决定 pi 加载哪个工作区的 AGENTS/扩展）；
    dir = 会话目录（jsonl 落盘处，可观测层，缺省 = cwd 目录）；旧 `workdir`
    字段不再识别（读到忽略并日志提示）。成功返回响应元组（200 + 规格文档），
    失败返回错误元组：文件缺失 → 404；路径非法/坏 JSON/缺 host/dir 非法或逃逸 ~/m → 400
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
                  "(cwd = directory containing the .agent file)", name)
    # cwd = .agent 文件所在目录（站内路径已校验在 ~/m 内；resolve_cwd 再过一道）。
    cwd_site_dir = _posixpath.dirname(p)
    cwd_src = (_proc.WS if cwd_site_dir in ("", "/")
               else _os.path.join(_proc.WS, cwd_site_dir.lstrip("/")))
    try:
        cwd = _proc.resolve_cwd(cwd_src)
    except ValueError as e:
        return _j({"ok": False,
                   "error": "agent file %s: agent dir rejected as cwd: %s"
                            % (p, e)},
                  '400 Bad Request')
    # dir = 会话目录：可选字段；缺省 = cwd 目录（会话文件落在 .agent 旁边）。
    dir_val = spec.get("dir")
    if dir_val is None:
        sess_dir = cwd
    else:
        if not isinstance(dir_val, str) or not dir_val.strip():
            return _j({"ok": False,
                       "error": "agent file %s: 'dir' must be a non-empty "
                                "string when present" % p},
                      '400 Bad Request')
        sess_dir = _os.path.expanduser(dir_val.strip())
        # dir 安全红线：与会话路径同一根集（~/m + run 运行时区），防穿越/逃逸。
        real = _os.path.realpath(sess_dir)
        roots = [_proc.WS_REAL,
                 _os.path.realpath(_os.path.join(_proc.WS, "run"))]
        if not any(real == r or real.startswith(r + _os.sep) for r in roots):
            return _j({"ok": False,
                       "error": "agent file %s: dir escapes workspace: %s"
                                % (p, dir_val)},
                      '400 Bad Request')
        sess_dir = real
    # dir 不存在 → 自动创建（含 participant/ 中间层），创建行为记日志。
    if not _os.path.isdir(sess_dir):
        try:
            _os.makedirs(sess_dir, exist_ok=True)
        except OSError as e:
            return _j({"ok": False,
                       "error": "agent file %s: cannot create dir %s: %s"
                                % (p, sess_dir, e)},
                      '500 Internal Server Error')
        _log.info("agent %s: auto-created dir %s", name, sess_dir)
    # 会话 = <dir>/<name>.jsonl（每 agent 一会话）；站内路径经既有校验再过一道。
    rel_dir = _os.path.relpath(sess_dir, _proc.WS)
    site_jsonl = "/" + _posixpath.normpath(_posixpath.join(rel_dir, name + ".jsonl"))
    try:
        session_file = _proc.resolve_session_path(site_jsonl)
    except ValueError as e:
        return _j({"ok": False,
                   "error": "agent file %s: derived session path rejected: %s"
                            % (p, e)},
                  '400 Bad Request')
    # 登记显式 cwd（任务 fw2ll1）：该会话路径懒建桥接时按此 cwd 拉起，
    # 不再恒等于 dirname(session_file)。
    _b.set_session_cwd(site_jsonl, cwd)
    return _j({"ok": True, "name": name, "host": host, "cwd": cwd,
               "dir": sess_dir, "session": site_jsonl,
               "session_file": session_file,
               "hostRouting": "v1: session spawns locally on this 8080 host; "
                              "host is stored for future cross-host routing"})


def interp(store, op='', session='', cmd='', **kw):
    if op == 'agent':
        # .agent 规格解析（任务 kcywpy）：不走会话桥接——会话路径由规格文件推导，
        # 前端拿推导结果再走正常 attach。
        return _resolve_agent(store, session)
    b, err = _bridge_or_err(session)
    if err:
        return err
    if op == 'status':
        return _j(dict(ok=True, **b.sup.status_doc()))
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
    if op == 'reload':
        r = b.sup.reload()
        if not r.get("ok"):
            return _j({"ok": False,
                       "error": "reload failed: %s" % r.get("error")},
                      '502 Bad Gateway')
        return _j({"ok": True, "session": b.session,
                   "gen": r.get("gen"), "pid": r.get("pid")})
    if op == 'clear':
        # 保留会话路径、清空全部内容：杀会话进程→删 jsonl→立即重拉（顺序在
        # Supervisor.clear 内保证：先杀透再删，防旧进程把内存历史回写）。
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
