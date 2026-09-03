# -*- type=script -*-
# sessiond SSE 事件流端点（任务 0829-1958-od0t；R1：路径路由）。
# 按路径会话（`session` 参数 = 站内 .jsonl 路径，如 /assistant/foo.jsonl）的
# 下行 JSONL 事件以 SSE（text/event-stream）流式下发；不设 content_len，
# wsgiserver 走 HTTP/1.1 chunked 分块传输。游标 = 桥接分配的不透明事件 ID
# （`<ns>:<seq>`，SSE 帧 `id:`）；断线重连带 `since=<上次事件 ID>`。
# since 不在环内（已淘汰/未知）→ 先发一帧 {"type":"webgw.gap",...} 缺口标记，
# 再从最旧可用位续流；前端据此走 entries 基线自愈。
import json as _json
from ext.sessiond import bridge as _b
from ext.sessiond import proc as _proc


def _j(obj, status='200 OK'):
    return dict(type='application/json', http_status=status), \
        _json.dumps(obj, ensure_ascii=False)


def interp(store, session='', since='', **kw):
    if not session:
        return _j({"ok": False, "error": "missing required param: session"},
                  '400 Bad Request')
    # agentd 族入口归一（任务 60grqq）：spec.json 声明者入口 → 会话产物路径；
    # 产物路径直开 → 400 + 指引（与 rpc/api.py 同口径单点，_proc.agentd_entry）。
    session, entry_err = _proc.agentd_entry(session)
    if entry_err:
        return _j({"ok": False, "error": entry_err}, '400 Bad Request')
    try:
        b = _b.get_bridge(session)
    except ValueError as e:
        return _j({"ok": False, "error": str(e)}, '400 Bad Request')
    # 订阅上限：防多页签/忘关页签耗尽 wsgiserver 线程池波及 8080 其它服务。
    # 查上限+占位同锁原子。
    with b.cond:
        if b.subscribers >= _b.MAX_STREAMS_PER_BRIDGE:
            return _j({"ok": False,
                       "error": "stream limit (%d) reached for session %r"
                                % (_b.MAX_STREAMS_PER_BRIDGE, b.session)},
                      '429 Too Many Requests')
        b.subscribers += 1
    start_seq, gap = b.resolve_since(since)
    with b.cond:
        oldest_eid = b.ring[0][1] if b.ring else None

    def gen():
        try:
            yield b": stream open\n\n"
            if gap:
                yield ("data: %s\n\n" % _json.dumps(
                    {"type": "webgw.gap", "gap": True, "since": since,
                     "oldest": oldest_eid, "session": b.session},
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
        finally:
            with b.cond:
                b.subscribers -= 1

    return dict(type='text/event-stream'), gen()
