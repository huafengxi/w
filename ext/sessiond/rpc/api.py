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
# 鉴权由 w 全局 BasicAuth 承担；路径校验非法 → 400。
import json as _json
from ext.sessiond import bridge as _b


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
            "leafId": data.get("leafId"),
            "watermark": r.get("watermark")}


def interp(store, op='', session='', cmd='', **kw):
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
