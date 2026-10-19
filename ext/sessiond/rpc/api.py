# -*- type=script -*-
"""Chat API: validate requests before opening a declared process channel."""
import json as _json
import logging as _logging
import os as _os
import posixpath as _posixpath
from ext.sessiond import bridge as _b
from ext.sessiond import sessions as S, agent_client as A
from ext.sessiond.bootstrap import get_registry
from ext.sessiond.declaration import resolve_agent as _resolve_agent

_log = _logging.getLogger("sessiond-agent")


def _j(obj, status='200 OK'):
    return dict(type='application/json', http_status=status), \
        _json.dumps(obj, ensure_ascii=False)


def _bridge_or_err(store, session):
    if not session:
        return None, _j({"ok": False, "error": "missing required param: session"},
                        '400 Bad Request')
    try:
        return _b.get_bridge(session, store=store), None
    except ValueError as exc:
        return None, _j({"ok": False, "error": str(exc)}, '400 Bad Request')


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

def _agentd_control_proxy(b, action, reason):
    doc, status = A.control(b, action, reason)
    return _j(doc, status)


# ---------------- 指挥中心临时会话（任务 z293ql，设计稿 §4.2/§4.3） ----------------
# 实现面（枚举/校验/写盘/删除纪律）单点在 sessions.py / registry.py；进程面（杀 Supervisor）在
# registry.delete；本层只做参数面、编排与响应形状。消费方 = `dash/session-ctl.py` 一枚
# （该文件里 **HTTP 面经本层、CLI 面子进程直调 sessions.py / registry.py** —— 子进程里 run_script 不可用）：
#   CLI `list` = 每节 widget 的表（⛔ 不经本层）；HTTP interp 不带 `act` = 本层 `op=session_tabs`
#   ⇒ dash/dash.itab 末行 @dynamic 的 itab 行 rpc；HTTP `?act=rename|delete` = 本层 op；
#   CLI `profiles`/`new` = 页面手动 widget 与命令行同款，直调 sessions.py / registry.py（⛔ 不经本层）。

# 服务端恒定/自动生成/由 profile 清单派生的字段：客户端传任一 ⇒ 400 显式拒绝（⛔ 不只是忽略）。
# 两枚变更 op 只控 `session`/`title`/`confirm`：`profile` 在列 = 人格只在创建时定（而创建不经本层）；
# `cwd` 在列 = 它由 profile 清单的 `cwd` 字段声明（proc.profile_cwd）；
# `command` 在列 = create_bot 时代的硬约束同族：spawn argv 的单点是
# pi-host LaunchConfig，声明者不可控（故 .agent 字段集里根本没有这个键）。
_SERVER_FIXED_FIELDS = ("command", "cwd", "profile", "sessionDir", "name", "host")


def _fixed_field_rejection(kw):
    """客户端传了服务端恒定字段 → 400 响应元组；都没传 → None。"""
    hit = sorted(k for k in _SERVER_FIXED_FIELDS
                 if kw.get(k) not in (None, ""))
    if not hit:
        return None
    return _j({"ok": False,
               "error": "field(s) %s are server-side fixed, auto-generated or derived "
                        "from the profile manifest for command-center sessions; these "
                        "ops take only 'session'/'title'/'confirm' (session creation is "
                        "not an HTTP surface: dash/session-ctl.py new --profile <名>)"
                        % ", ".join(hit)},
              '400 Bad Request')


def _prefix():
    """机器前缀 = 本机规范名（env/host-id 查表，口径同 agentd/report.py:chat_url 的 host）。
    ⛔ 不从请求路径派生：代理 `strip_prefix: true`（ext/proxy/proxy.py:forward）⇒ 被请求机
    看到的 path 不含 `/dev/` 一类前缀。不可得 → None（调用方吐零行，⛔ 不猜、⛔ 不发无前缀链接）。"""
    return S._self_host()


def _chat_url(agent_site, host):
    return "/%s%s?v=chat" % (host, agent_site) if host and agent_site else ""


def _list_sessions():
    host = _prefix()
    sessions = []
    for r in get_registry().rows():
        sessions.append({
            "name": r["name"], "title": r["title"],
            "titleSource": r["titleSource"], "project": r["project"],
            "cwd": r["cwd"], "profile": r["profile"], "host": r["host"],
            "state": r["state"], "pids": r["pids"], "mtime": r["mtime"],
            "agent": r["agent_site"], "session": r["session_site"],
            "chatUrl": _chat_url(r["agent_site"], host),
        })
    return _j({"ok": True, "host": host, "prefix": host or "",
               "sessionsDir": S.site_path_of(
                   _os.path.realpath(S.sessions_dir())),
               "sessions": sessions})


def _session_tabs():
    """设计稿 §4.3：@dynamic 的数据源。空态 = 零行；⛔ 不标运行态（tab 只管快速进，
    live/dormant 看工作区）；⛔ 不轮询（frame 加载时 fetch 一次 = 快照）。"""
    host = _prefix()
    if not host:
        _log.warning("session_tabs: local host undeterminable (env/host-id); "
                     "emitting zero rows")
        return _j({"ok": True, "prefix": None, "rows": []})
    rows = []
    for r in get_registry().rows():
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
        doc = get_registry().rename(S.session_name_from_site(session), kw.get("title"))
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
        doc = get_registry().delete(S.session_name_from_site(session))
    except ValueError as exc:
        return _j({"ok": False, "error": str(exc)}, '400 Bad Request')
    except OSError as exc:
        return _j({"ok": False, "error": "delete failed: %s" % exc},
                  '500 Internal Server Error')
    return _j(doc, '200 OK' if doc.get("ok") else '500 Internal Server Error')


def interp(store, op='', session='', cmd='', **kw):
    # Explicit client fences, when supplied, survive until the operation gate.
    # Legacy/manual URLs capture the current registration/instance server-side.
    tokens = []
    for field in ('expected_instance', 'expected_registration'):
        if field in kw and (not isinstance(kw[field], str) or not kw[field].strip()):
            return _j(dict(ok=False, error=field + ' must be a nonempty string'), '400 Bad Request')
    try:
        if 'expected_instance' in kw:
            from ext.sessiond.lifecycle import _EXPECTED
            tokens.append((_EXPECTED, _EXPECTED.set(kw['expected_instance'])))
        if 'expected_registration' in kw:
            from ext.sessiond.registry import _EXPECTED_REGISTRATION
            tokens.append((_EXPECTED_REGISTRATION,
                           _EXPECTED_REGISTRATION.set(kw['expected_registration'])))
        return _interp(store, op=op, session=session, cmd=cmd, **kw)
    finally:
        for variable, token in reversed(tokens):
            variable.reset(token)


def _interp(store, op='', session='', cmd='', **kw):
    if op == 'agent':
        # .agent 规格解析（任务 kcywpy）：不走会话桥接——会话路径由规格文件推导，
        # 前端拿推导结果再走正常 attach。
        return _resolve_agent(store, session)
    if op in ('list_sessions', 'rename_session',
              'delete_session', 'session_tabs'):
        # 指挥中心临时会话（任务 z293ql）：四个 op 都**不走** _bridge_or_err —— 建桥会
        # 为「还没打开过 ∨ 即将被删」的会话拉起进程（list/session_tabs 无需会话，
        # rename/delete 按 session 参数定位 run/sessiond/ 下那一枚；杀进程走
        # bridge.drop_session 的「绝不建桥」查表）。
        if op == 'list_sessions':
            return _list_sessions()
        if op == 'session_tabs':
            return _session_tabs()
        if op == 'rename_session':
            return _rename_session(session, kw)
        return _delete_session(session, kw)
    if op not in ('status', 'attach', 'commands', 'inspect', 'reload', 'clear', 'cmd',
                  'queue_rpc', 'chat_state', 'regenerate'):
        return _j({"ok": False, "error": "unknown op %r" % op}, '400 Bad Request')
    if op == 'cmd':
        try:
            cmd_obj = _json.loads(cmd) if cmd else None
        except ValueError:
            return _j({"ok": False, "error": "cmd is not valid JSON"}, '400 Bad Request')
        if not isinstance(cmd_obj, dict) or not isinstance(cmd_obj.get('type'), str) or not cmd_obj['type']:
            return _j({"ok": False, "error": "cmd must be an object with type"}, '400 Bad Request')
        if cmd_obj['type'] in _b.BLOCKED_COMMANDS:
            return _j({"ok": False, "error": "command is blocked"}, '403 Forbidden')
    b, err = _bridge_or_err(store, session)
    if err:
        return err
    if op == 'status':
        doc = dict(ok=True, **b.sup.status_doc())
        # 斜杠状态行第 4 行可见性三值（任务 bjhzvj；任务 8r0tww 补 socket 会话）：
        # 服务端既有数据，无新造——host 复用 _self_host()（env/host-id 查表，同
        # 宿主映射口径）；sessionDir = session_file 的 dirname；workDir 分两路：
        # socket 会话（agentd 族）= 参与方 spec.json 声明的 workdir（= runner 拉起
        # 会话进程的 cwd，任务 8r0tww），读取失败 → null；普通会话 = 会话进程真实在跑
        # （state=running ∧ pid）时 status_doc 的拉起 cwd，未拉起 → null。缺值前端显 `?`。
        # 既有 cwd 字段原样保留（调试口径不变）。
        doc["host"] = S._self_host()
        sf = doc.get("session_file") or ""
        doc["sessionDir"] = _os.path.dirname(sf) if sf else None
        if isinstance(b.sup, A.AgentClient):
            doc["workDir"] = A.agentd_spec_workdir(b.sup.participant_id)
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
    if op in ('queue_rpc', 'chat_state', 'regenerate'):
        if op == 'queue_rpc':
            action = kw.get('action')
            if action not in ('clear_queue', 'steer'):
                return _j({"ok": False, "error": "unsupported queue action"}, '400 Bad Request')
            command = {"type": action}
            if action == 'steer':
                command['message'] = kw.get('message', '')
            r = b.request(command)
        elif op == 'chat_state':
            r = b.chat_action('state')
            if r.get('ok'):
                r['pendingDialogs'] = b.pending_dialog_list()
                if not r.get('busy') and getattr(b, 'stop_requested', False):
                    r['state'] = 'stopped'
        else:
            try:
                images = _json.loads(kw.get('images') or '[]')
            except (ValueError, TypeError):
                return _j({"ok": False, "error": "invalid images JSON"}, '400 Bad Request')
            r = b.chat_action('regenerate', {'entryId': kw.get('entryId'),
                                           'message': kw.get('message', ''), 'images': images})
        # Return RPC failure as data too: clear+forward is explicitly NOT atomic.
        return _j(r)
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
        # socket 会话（agentd 任务/常驻）：探针扩展由封装脚本 agentd/pi-rpc-wrap.py
        # -e 注入（与 sessions.py / registry.py:_spawn 同款），握手链路复用 b.inspect()——只读转储，
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
        if isinstance(b.sup, A.AgentClient):
            # socket 会话：代理 control/restart（保历史换代，任务 dsuqbi）
            return _agentd_control_proxy(
                b, "restart", "user /reload via socket session")
        r = b.sup.reload()
        if not r.get("ok"):
            return _j({"ok": False,
                       "error": "reload failed: %s" % r.get("error")},
                      '502 Bad Gateway')
        return _j({"ok": True, "session": b.session,
                   "gen": r.get("gen"), "pid": r.get("pid")})
    if op == 'clear':
        if isinstance(b.sup, A.AgentClient):
            # socket 会话：代理 control/clear（弃历史换代：杀进程→备份→截断→空白新代，
            # 任务 dsuqbi）；跨机参与者维持拒绝（建桥咽喉 + 代理内纵深校验）。
            return _agentd_control_proxy(
                b, "clear", "user /clear via socket session")
        # 保留会话路径、清空全部内容：杀会话进程→截断 jsonl 到 0 + 去 replica 标记→立即重拉（顺序在
        # Supervisor.clear 内保证：先杀透再删，防旧进程把内存历史回写）。
        r = b.sup.clear()
        if not r.get("ok"):
            return _j({"ok": False,
                       "error": "clear failed: %s" % r.get("error")},
                      '502 Bad Gateway')
        return _j({"ok": True, "session": b.session,
                   "gen": r.get("gen"), "pid": r.get("pid")})
    if op == 'cmd':
        err = b.send(cmd_obj)
        if err:
            return _j({"ok": False, "error": err}, '403 Forbidden')
        return _j({"ok": True})
    return _j({"ok": False, "error": "unknown op %r" % (op,)}, '400 Bad Request')
