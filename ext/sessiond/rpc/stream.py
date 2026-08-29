# -*- type=script -*-
# sessiond SSE 事件流端点（w/ext/sessiond，任务 0829-1510-w25l）。
# 把会话挂接的下行 JSONL 事件以 SSE（text/event-stream）流式下发；不设 content_len，
# wsgiserver 走 HTTP/1.1 chunked 分块传输（与 ext/shell/rpc/sh.py 同机制）。
# 断线重连：前端带 since=<上次游标> 重新请求，从 Bridge 环形缓冲补齐；游标超出
# 新桥（如守护重启后重建）时归零，由 sessiond 回放最近 500 行兜底。
import json as _json
from ext.sessiond import bridge as _b


def _j(obj, status='200 OK'):
    return dict(type='application/json', http_status=status), \
        _json.dumps(obj, ensure_ascii=False)


def interp(store, session='', since='0', **kw):
    _b.gc_idle_bridges()
    if not session or '/' in session or session.startswith('.'):
        return _j({"ok": False, "error": "missing/bad session"}, '400 Bad Request')
    try:
        b = _b.get_bridge(session, create=True)
    except FileNotFoundError as e:
        return _j({"ok": False, "error": str(e)}, '404 Not Found')
    except OSError as e:
        return _j({"ok": False, "error": "attach failed: %s" % e},
                  '502 Bad Gateway')
    try:
        since_i = int(since)
    except (ValueError, TypeError):
        since_i = 0
    with b.cond:
        if since_i > b.seq:      # 新桥：旧游标作废归零（回放由 sessiond 兜底）
            since_i = 0

    def gen():
        try:
            yield b": stream open\n\n"
            for item in b.iter_from(since_i):
                if item is None:
                    yield b": ping\n\n"          # keep-alive 注释
                else:
                    seq, obj = item
                    yield ("id: %d\ndata: %s\n\n"
                           % (seq, _json.dumps(obj, ensure_ascii=False))
                           ).encode("utf-8")
        except GeneratorExit:
            return

    return dict(type='text/event-stream'), gen()
