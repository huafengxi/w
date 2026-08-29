# -*- type=script -*-
# sessiond 控制面 + 上行命令端点（w/ext/sessiond，任务 0829-1510-w25l；
# 基线 get_entries / 增量补发：任务 0829-1609-8dwt）。
# op=status 透传 ctl.sock status（含守护 epoch）；op=attach 建/复用 Bridge 并经守护
# 透传 pi rpc get_entries 返回消息基线（全量）；op=entries 增量补发（since=entry id
# 游标，success:false 自动回退全量并置 gap）；op=cmd 透传单条 pi 命令；op=detach 关桥。
# 鉴权由 w 全局 BasicAuth 承担。协议见 PROTOCOL.md §6。
import json as _json
from ext.sessiond import bridge as _b


def _j(obj, status='200 OK'):
    return dict(type='application/json', http_status=status), \
        _json.dumps(obj, ensure_ascii=False)


def _baseline_doc(b, r, gap=False):
    """把 get_entries 结果整理成基线/增量响应文档。"""
    resp = r["resp"]
    if not resp.get("success"):
        return None
    data = resp.get("data") or {}
    entries = data.get("entries") or []
    return {"ok": True, "session": b.session, "gap": gap,
            "epoch": r.get("epoch"), "gen": b.gen, "entries": entries,
            "leafId": data.get("leafId"),
            "entryCursor": entries[-1].get("id") if entries else None,
            "watermark": r.get("watermark")}


def interp(store, op='', session='', cmd='', since='', **kw):
    _b.gc_idle_bridges()
    if op == 'status':
        try:
            return _j(_b.ctl_status())
        except Exception as e:
            return _j({"ok": False, "error": "ctl.sock unavailable: %s" % e},
                      '502 Bad Gateway')
    if not session or '/' in session or session.startswith('.'):
        return _j({"ok": False, "error": "missing/bad session"}, '400 Bad Request')
    if op == 'attach':
        try:
            b = _b.get_bridge(session, create=True)
        except FileNotFoundError as e:
            return _j({"ok": False, "error": str(e)}, '404 Not Found')
        except OSError as e:
            return _j({"ok": False, "error": "attach failed: %s" % e},
                      '502 Bad Gateway')
        r = b.get_entries()                      # 全量基线
        if not r["ok"]:
            return _j({"ok": False, "session": session,
                       "error": "baseline failed: %s" % r["error"]},
                      '504 Gateway Timeout')
        doc = _baseline_doc(b, r)
        if doc is None:
            return _j({"ok": False, "session": session,
                       "error": "get_entries rejected: %s"
                       % (r["resp"].get("error") or "?")}, '502 Bad Gateway')
        return _j(doc)
    if op == 'entries':
        try:
            b = _b.get_bridge(session, create=True)
        except FileNotFoundError as e:
            return _j({"ok": False, "error": str(e)}, '404 Not Found')
        except OSError as e:
            return _j({"ok": False, "error": "attach failed: %s" % e},
                      '502 Bad Gateway')
        r = b.get_entries(since=since or None)   # 增量（带 entry id 游标）
        gap = False
        if r["ok"] and not r["resp"].get("success"):
            # since 不匹配任何 entry（原生 gap 信号）→ 自动回退全量重拉
            gap = True
            r = b.get_entries()
        if not r["ok"]:
            return _j({"ok": False, "session": session,
                       "error": "entries failed: %s" % r["error"]},
                      '504 Gateway Timeout')
        doc = _baseline_doc(b, r, gap=gap)
        if doc is None:
            return _j({"ok": False, "session": session,
                       "error": "get_entries rejected: %s"
                       % (r["resp"].get("error") or "?")}, '502 Bad Gateway')
        return _j(doc)
    if op == 'detach':
        _b.close_bridge(session)
        return _j({"ok": True})
    if op == 'cmd':
        try:
            cmd_obj = _json.loads(cmd) if cmd else None
        except ValueError:
            return _j({"ok": False, "error": "cmd is not valid JSON"},
                      '400 Bad Request')
        if not isinstance(cmd_obj, dict) or not cmd_obj.get('type'):
            return _j({"ok": False, "error": "cmd must be an object with type"},
                      '400 Bad Request')
        b = _b.get_bridge(session, create=False)
        if b is None:
            return _j({"ok": False,
                       "error": "not attached (op=attach first)"},
                      '409 Conflict')
        try:
            b.send(cmd_obj)
        except (ConnectionError, OSError) as e:
            return _j({"ok": False, "error": "forward failed: %s" % e},
                      '502 Bad Gateway')
        return _j({"ok": True})
    return _j({"ok": False, "error": "unknown op %r" % (op,)}, '400 Bad Request')
