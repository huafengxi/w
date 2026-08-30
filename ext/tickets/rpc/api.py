# -*- type=script -*-
# tickets 只读查询端点（任务 0830-1649-2y1k：仪表板第三 tab「Tickets」）。
# 扫描 ~/m/agents/ 下 ticket.*/epic.* 目录（文件式 ticket 系统，设计定稿
# 0829-1243-izl9）：每票 = ticket.json（静态元数据）+ status.json（动态状态）
# + events/*.ev（逐次一文件的变迁记录）。本端点只读，绝不写 agents/ 树。
#   op=list（默认）  全部票的摘要数组：未终结在前，各按 updatedAt 倒序；
#                    附最近一条 event 摘要（有则给）
#   op=detail&id=<票 id>  单票全量：ticket.json + status.json + 全部 events
#                         （按 ts 升序），供前端展开时间线
# 健壮性：单个文件缺失/损坏只跳过该字段或该票，不抛错；目录整体不可读则
# 整票跳过。鉴权由 w 全局 BasicAuth 承担；id 白名单校验防路径穿越。
import re

import os as _os

WS = _os.path.expanduser("~/m")
AGENTS_DIR = _os.path.realpath(_os.path.join(WS, "agents"))
_ID_RE = re.compile(r"^(ticket|epic)\.[A-Za-z0-9._-]+$")
_TERMINAL = ("resolved", "cancelled")


def _j(obj, status="200 OK"):
    return dict(type="application/json", http_status=status), \
        json.dumps(obj, ensure_ascii=False)


def _load_json(path):
    """读 JSON；缺失/损坏返回 None（健壮跳过，不抛错）。"""
    try:
        with open(path, "rb") as f:
            return json.loads(f.read().decode("utf-8", "replace"))
    except Exception:
        return None


def _load_events(ev_dir):
    """events/*.ev 逐个解析（一事件一文件），损坏文件跳过；按 (ts, 文件名) 升序。"""
    out = []
    try:
        names = sorted(os.listdir(ev_dir))
    except OSError:
        return out
    for name in names:
        if not name.endswith(".ev"):
            continue
        ev = _load_json(os.path.join(ev_dir, name))
        if isinstance(ev, dict):
            ev.setdefault("_file", name)
            out.append(ev)
    out.sort(key=lambda e: (e.get("ts") or "", e.get("_file") or ""))
    return out


def _scan_one(d):
    """解析单个 ticket.*/epic.* 目录；ticket.json 缺失/非 dict 则返回 None（跳过）。"""
    ticket = _load_json(os.path.join(d, "ticket.json"))
    if not isinstance(ticket, dict):
        return None
    status = _load_json(os.path.join(d, "status.json"))
    if not isinstance(status, dict):
        status = {}
    events = _load_events(os.path.join(d, "events"))
    last_ev = events[-1] if events else None
    return {
        "id": ticket.get("id") or os.path.basename(d),
        "kind": ticket.get("kind") or "",
        "title": ticket.get("title") or "",
        "type": ticket.get("type") or "",
        "status": status.get("status") or "",
        "owner": ticket.get("owner") or "",
        "createdBy": ticket.get("createdBy") or "",
        "createdAt": ticket.get("createdAt") or "",
        "updatedAt": status.get("updatedAt") or "",
        "updatedBy": status.get("updatedBy") or "",
        "parent": ticket.get("parent"),
        "refs": ticket.get("refs") or [],
        "tasks": status.get("tasks") or [],
        "tickets": status.get("tickets") or [],
        "resolution": status.get("resolution"),
        "lastEvent": None if last_ev is None else {
            "ts": last_ev.get("ts"),
            "by": last_ev.get("by"),
            "from": last_ev.get("from"),
            "to": last_ev.get("to"),
            "note": last_ev.get("note"),
        },
    }


def _list_tickets():
    out = []
    try:
        names = os.listdir(AGENTS_DIR)
    except OSError as e:
        return None, _j({"ok": False, "error": "cannot list agents dir: %s" % e},
                        "500 Internal Server Error")
    for name in names:
        if not (name.startswith("ticket.") or name.startswith("epic.")):
            continue
        d = os.path.join(AGENTS_DIR, name)
        if not os.path.isdir(d):
            continue
        item = _scan_one(d)
        if item is not None:
            out.append(item)
    # 未终结（非 resolved/cancelled）在前；各组内按 updatedAt 倒序
    # （ISO 时间串字典序 = 时间序；缺失视为最旧排组内最后）
    out.sort(key=lambda t: (t["status"] in _TERMINAL,
                            _desc_key(t["updatedAt"])))
    return out, None


def _desc_key(ts):
    """updatedAt 倒序键：ASCII 时间串逐字符取反，缺省给最大键（排最后）。"""
    if not ts:
        return chr(127) * 32
    return "".join(chr(127 - ord(c)) if ord(c) < 128 else c for c in ts)


def _detail(ticket_id):
    if not ticket_id or not _ID_RE.match(ticket_id):
        return None, _j({"ok": False, "error": "invalid ticket id: %r" % (ticket_id,)},
                        "400 Bad Request")
    d = os.path.realpath(os.path.join(AGENTS_DIR, ticket_id))
    if d != AGENTS_DIR + os.sep + ticket_id or not os.path.isdir(d):
        return None, _j({"ok": False, "error": "no such ticket: %r" % (ticket_id,)},
                        "404 Not Found")
    item = _scan_one(d)
    if item is None:
        return None, _j({"ok": False, "error": "ticket unreadable: %r" % (ticket_id,)},
                        "404 Not Found")
    item["events"] = _load_events(os.path.join(d, "events"))
    return item, None


def interp(store, op="list", id=None, **kw):
    if op == "list":
        out, err = _list_tickets()
        return err if err else _j({"ok": True, "count": len(out), "tickets": out})
    elif op == "detail":
        out, err = _detail(id)
        return err if err else _j({"ok": True, "ticket": out})
    return _j({"ok": False, "error": "unknown op: %r (use list|detail)" % (op,)},
              "400 Bad Request")
