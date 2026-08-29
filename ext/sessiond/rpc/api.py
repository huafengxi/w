# -*- type=script -*-
# sessiond 控制面 + 上行命令端点（w/ext/sessiond，任务 0829-1510-w25l）。
# op=status 透传 ctl.sock status；op=attach/detach 管理 Bridge；op=cmd 把单条
# pi 命令透传进会话挂接（响应经 stream.py 的 SSE 流回传）。鉴权由 w 全局 BasicAuth 承担。
import json as _json
from ext.sessiond import bridge as _b


def _j(obj, status='200 OK'):
    return dict(type='application/json', http_status=status), \
        _json.dumps(obj, ensure_ascii=False)


def interp(store, op='', session='', cmd='', **kw):
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
            _b.get_bridge(session, create=True)
        except FileNotFoundError as e:
            return _j({"ok": False, "error": str(e)}, '404 Not Found')
        except OSError as e:
            return _j({"ok": False, "error": "attach failed: %s" % e},
                      '502 Bad Gateway')
        return _j({"ok": True, "session": session})
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
