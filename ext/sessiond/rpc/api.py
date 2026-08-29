# -*- type=script -*-
# sessiond 控制面 + 上行命令端点（w/ext/sessiond，任务 0829-1510-w25l；
# 基线 get_entries / 增量补发：任务 0829-1609-8dwt）。
# op=status 透传 ctl.sock status（含守护 epoch）；op=reload 经 ctl.sock 下发
# reload-session（杀会话进程+立即从 jsonl resume 重拉，0829-1740-u7tb）；
# 管理面（0829-1803-2umc）：op=new/set-cwd/rename 经 ctl.sock 下发
# add-session/set-cwd/rename-session（前端管理斜杠命令 /new /cd /set-name 承接）；
# op=attach 建/复用 Bridge 并经守护
# 透传 pi rpc get_entries 返回消息基线（全量）；op=entries 增量补发（since=entry id
# 游标，success:false 自动回退全量并置 gap）；op=cmd 透传单条 pi 命令。
# （op=detach 已移除：前端「断开挂接」按钮下线，断开 = 关页签，桥由空闲回收，
# inform1，0829-1733-1hjh）
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


def _check_session(session):
    """S1（0829-1640-har3）：格式校验 + 注册表白名单。返回错误响应或 None。

    白名单源 = ctl.sock status 的注册表名单（拒保留名 `ctl` 与野 socket，
    防经 ctl.sock 越权下发控制命令）；注册表不可达时 fail-closed。
    """
    if not session or '/' in session or session.startswith('.'):
        return _j({"ok": False, "error": "missing/bad session"},
                  '400 Bad Request')
    try:
        names = _b.registry_names()
    except Exception as e:
        return _j({"ok": False,
                   "error": "registry unavailable, refusing: %s" % e},
                  '502 Bad Gateway')
    if session not in names:
        return _j({"ok": False,
                   "error": "session %r not in registry (refused)" % session},
                  '403 Forbidden')
    return None


def _mgmt_err(r, default='502 Bad Gateway'):
    """管理命令拒绝响应（任务 0829-1803-2umc）：按守护 reason 映射 HTTP 状态。"""
    reason = (r or {}).get("reason")
    status = {"dup": '409 Conflict', "gate": '409 Conflict',
              "bad-name": '400 Bad Request', "bad-cwd": '400 Bad Request',
              "unknown": '404 Not Found'}.get(reason, default)
    return _j({"ok": False,
               "error": (r or {}).get("error") or "unknown daemon error",
               "reason": reason or "unknown"}, status)


def interp(store, op='', session='', cmd='', since='', name='', cwd='', **kw):
    _b.gc_idle_bridges()
    if op == 'status':
        try:
            st = _b.ctl_status()
        except Exception as e:
            return _j({"ok": False, "error": "ctl.sock unavailable: %s" % e},
                      '502 Bad Gateway')
        # inform4（0829-1733-1hjh）：守护 status_doc 不含 cwd → 经声明式注册表补齐
        cwds = _b.registry_cwd_map()
        for s in st.get("sessions") or []:
            if isinstance(s, dict) and cwds.get(s.get("name")):
                s["cwd"] = cwds[s["name"]]
        return _j(st)
    if op == 'new':
        # 管理命令（0829-1803-2umc）：新建会话 = 注册表新增条目 + 立即拉起。
        # 目标是尚不存在的会话 → 不过 _check_session 白名单；重名/门控名占用
        # 由守护拒绝（透传错误）。
        r = None
        try:
            r = _b.ctl_add_session(name.strip(), cwd.strip() or None)
        except Exception as e:
            return _j({"ok": False, "error": "ctl.sock unavailable: %s" % e},
                      '502 Bad Gateway')
        if not r or not r.get("ok"):
            return _mgmt_err(r)
        return _j({"ok": True, "name": r.get("name"), "cwd": r.get("cwd"),
                   "session_file": r.get("session_file")})
    deny = _check_session(session)
    if deny:
        return deny
    if op == 'attach':
        try:
            b = _b.get_bridge(session, create=True)
        except FileNotFoundError as e:
            return _j({"ok": False, "error": str(e)}, '404 Not Found')
        except PermissionError as e:
            return _j({"ok": False, "error": str(e)}, '403 Forbidden')
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
        except PermissionError as e:
            return _j({"ok": False, "error": str(e)}, '403 Forbidden')
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
    if op == 'reload':
        # 进程级 reload（0829-1740-u7tb）：服务端路径 桥接→ctl.sock reload-session，
        # 不经会话面透传（S1 白名单已在上方 _check_session 过）。既有 Bridge 连接不受影响：
        # 会话 socket 由守护持有，进程杀/拉只换进程不换挂接；守护广播的
        # session_restarting/restarted 帧沿现有 SSE 流达前端，触发 gen 自愈。
        try:
            r = _b.ctl_reload_session(session)
        except Exception as e:
            return _j({"ok": False, "error": "ctl.sock unavailable: %s" % e},
                      '502 Bad Gateway')
        if not r or not r.get("ok"):
            return _j({"ok": False,
                       "error": "reload failed: %s"
                                % ((r or {}).get("error") or "unknown")},
                      '502 Bad Gateway')
        return _j({"ok": True, "session": session,
                   "gen": r.get("gen"), "pid": r.get("pid")})
    if op == 'set-cwd':
        # 管理命令（0829-1803-2umc）：当前会话切工作目录 = 注册表更新 +
        # 进程级 reload 重启生效（守护同步完成，响应带新 gen）。
        try:
            r = _b.ctl_set_cwd(session, cwd.strip())
        except Exception as e:
            return _j({"ok": False, "error": "ctl.sock unavailable: %s" % e},
                      '502 Bad Gateway')
        if not r or not r.get("ok"):
            return _mgmt_err(r)
        return _j({"ok": True, "session": session, "cwd": r.get("cwd"),
                   "gen": r.get("gen"), "pid": r.get("pid"),
                   "respawn": r.get("respawn", True)})
    if op == 'rename':
        # 管理命令（0829-1803-2umc）：当前会话改名 = 注册表 + socket 改名 +
        # 新 --name respawn；重名/门控名占用由守护拒绝。旧名挂接失效（预期）。
        try:
            r = _b.ctl_rename_session(session, name.strip())
        except Exception as e:
            return _j({"ok": False, "error": "ctl.sock unavailable: %s" % e},
                      '502 Bad Gateway')
        if not r or not r.get("ok"):
            return _mgmt_err(r)
        # 旧名的桥回收（其 socket 已由守护关闭）
        _b.drop_bridge(session)
        return _j({"ok": True, "old": session, "session": r.get("name"),
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
