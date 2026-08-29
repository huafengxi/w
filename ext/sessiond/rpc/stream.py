# -*- type=script -*-
# sessiond SSE 事件流端点（w/ext/sessiond，任务 0829-1510-w25l；
# 事件 ID 游标 + 缺口标记：任务 0829-1609-8dwt）。
# 把会话挂接的下行 JSONL 事件以 SSE（text/event-stream）流式下发；不设 content_len，
# wsgiserver 走 HTTP/1.1 chunked 分块传输（与 ext/shell/rpc/sh.py 同机制）。
# 游标 = 桥接分配的不透明事件 ID（`e<seq>`，SSE 帧 `id:`）；断线重连带
# `since=<上次事件 ID>` 重新请求。since 不在环内（已淘汰/来自旧桥/未知）→
# 先发一帧 {"type":"webgw.gap","gap":true,...} 缺口标记，再从最旧可用位续流；
# 前端据此走 entries 基线自愈（PROTOCOL.md §6.3）。
import json as _json
from ext.sessiond import bridge as _b


def _j(obj, status='200 OK'):
    return dict(type='application/json', http_status=status), \
        _json.dumps(obj, ensure_ascii=False)


def interp(store, session='', since='', **kw):
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
    start_seq, gap = b.resolve_since(since)
    with b.cond:
        oldest_eid = b.ring[0][1] if b.ring else None

    def gen():
        try:
            yield b": stream open\n\n"
            if gap:
                yield ("data: %s\n\n" % _json.dumps(
                    {"type": "webgw.gap", "gap": True, "since": since,
                     "oldest": oldest_eid, "session": session},
                    ensure_ascii=False)).encode("utf-8")
            for item in b.iter_events(start_seq):
                if item is None:
                    yield b": ping\n\n"          # keep-alive 注释
                else:
                    eid, obj = item
                    yield ("id: %s\ndata: %s\n\n"
                           % (eid, _json.dumps(obj, ensure_ascii=False))
                           ).encode("utf-8")
        except GeneratorExit:
            return

    return dict(type='text/event-stream'), gen()
