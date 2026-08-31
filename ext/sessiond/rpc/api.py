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
#   op=agent   .agent 文件类型（任务 kcywpy）：session = 站内 /…*.agent 路径；
#              读 agent 规格 JSON（host + workdir）→ 校验 → workdir 不存在则自动创建 →
#              返回会话 jsonl 站内路径（= workdir/<name>.jsonl）。启动参数解析单点，
#              host v1 仅保存/可见、不跨机拉起（见 design.md .agent 小节）。
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


# ---------------- .agent 文件类型（任务 kcywpy） ----------------
# xxx.agent = agent 规格 JSON（字段参考 ~/m/agents/ 任务 spec.json 风格，最小集 =
# host + workdir，多余字段宽容）。访问 /xxx.agent → 聊天视图，会话启动参数改从该
# JSON 读取：会话工作目录 = workdir，会话 jsonl = workdir/<agent名>.jsonl（每 agent
# 一会话、可重连续聊，类比 /assistant/dispatcher.jsonl 的组织）。本函数 = 启动参数
# 解析单点：将来加 host 跨机路由（反向通道/各机 8080 代理）只改这里。
# host v1 边界：仅持久化保存（存于 .agent 文件）+ 随响应返回 + 聊天页可见；
# 会话一律在本 8080 实例本地拉起（sessiond 现状即本地监督），不做跨机拉起。


def _resolve_agent(store, session):
    """解析 .agent 规格。成功返回响应元组（200 + 规格文档），失败返回错误元组：
    文件缺失 → 404；路径非法/坏 JSON/缺字段/workdir 逃逸 ~/m → 400（对齐 w 既有
    缺失/错误处理口径）。"""
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
    missing = [k for k in ("host", "workdir")
               if not isinstance(spec.get(k), str) or not spec.get(k).strip()]
    if missing:
        return _j({"ok": False,
                   "error": "agent file %s: missing or empty field(s): %s"
                            % (p, ", ".join(missing))},
                  '400 Bad Request')
    host = spec["host"].strip()
    workdir = _os.path.expanduser(spec["workdir"].strip())
    # workdir 安全红线：与会话路径同一根集（~/m + run 运行时区），防穿越/逃逸。
    real = _os.path.realpath(workdir)
    roots = [_proc.WS_REAL,
             _os.path.realpath(_os.path.join(_proc.WS, "run"))]
    if not any(real == r or real.startswith(r + _os.sep) for r in roots):
        return _j({"ok": False,
                   "error": "agent file %s: workdir escapes workspace: %s"
                            % (p, spec["workdir"])},
                  '400 Bad Request')
    # workdir 不存在 → 自动创建（含 participant/ 中间层），创建行为记日志。
    if not _os.path.isdir(workdir):
        try:
            _os.makedirs(workdir, exist_ok=True)
        except OSError as e:
            return _j({"ok": False,
                       "error": "agent file %s: cannot create workdir %s: %s"
                                % (p, workdir, e)},
                      '500 Internal Server Error')
        _log.info("agent %s: auto-created workdir %s", name, workdir)
    # 会话 = workdir/<name>.jsonl（每 agent 一会话）；站内路径经既有校验再过一道。
    rel_dir = _os.path.relpath(workdir, _proc.WS)
    site_jsonl = "/" + _posixpath.normpath(_posixpath.join(rel_dir, name + ".jsonl"))
    try:
        session_file = _proc.resolve_session_path(site_jsonl)
    except ValueError as e:
        return _j({"ok": False,
                   "error": "agent file %s: derived session path rejected: %s"
                            % (p, e)},
                  '400 Bad Request')
    return _j({"ok": True, "name": name, "host": host, "workdir": workdir,
               "session": site_jsonl, "session_file": session_file,
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
