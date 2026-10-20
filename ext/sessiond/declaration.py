"""Resolve an interactive declaration without starting a process or caching parameters."""
import json as _json
import logging as _logging
import os as _os
import posixpath as _posixpath
from ext.sessiond import sessions as _sessions

_log = _logging.getLogger("sessiond-agent")


def _j(obj, status='200 OK'):
    return dict(type='application/json', http_status=status), _json.dumps(obj, ensure_ascii=False)


def resolve_agent(store, session):
    """Validate a declaration and return its resolved process parameters."""
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
    missing = [k for k in ("host",)
               if not isinstance(spec.get(k), str) or not spec.get(k).strip()]
    if missing:
        return _j({"ok": False,
                   "error": "agent file %s: missing or empty field(s): %s"
                            % (p, ", ".join(missing))},
                  '400 Bad Request')
    host = spec["host"].strip()
    if "workdir" in spec:
        _log.info("agent %s: legacy 'workdir' field ignored "
                  "(cwd = 显式 cwd 字段，缺省 .agent 所在目录)", name)
    cwd_val = spec.get("cwd")
    if cwd_val is not None:
        if not isinstance(cwd_val, str) or not cwd_val.strip():
            return _j({"ok": False,
                       "error": "agent file %s: 'cwd' must be a non-empty "
                                "string when present" % p},
                      '400 Bad Request')
        cwd_src = _os.path.realpath(_os.path.expanduser(cwd_val.strip()))
        roots = [_sessions.WS_REAL,
                 _os.path.realpath(_os.path.join(_sessions.WS, "run"))]
        if not any(cwd_src == r or cwd_src.startswith(r + _os.sep) for r in roots):
            return _j({"ok": False,
                       "error": "agent file %s: cwd escapes workspace: %s"
                                % (p, cwd_val)},
                      '400 Bad Request')
    else:
        cwd_site_dir = _posixpath.dirname(p)
        cwd_src = (_sessions.WS if cwd_site_dir in ("", "/")
                   else _os.path.join(_sessions.WS, cwd_site_dir.lstrip("/")))
    try:
        cwd = _sessions.resolve_cwd(cwd_src)
    except ValueError as e:
        return _j({"ok": False,
                   "error": "agent file %s: cwd rejected: %s"
                            % (p, e)},
                  '400 Bad Request')
    prof_val = spec.get("profile")
    profile = None
    if prof_val is not None:
        try:
            profile = _sessions.validate_profile(prof_val)
        except ValueError as e:
            return _j({"ok": False,
                       "error": "agent file %s: %s" % (p, e)},
                      '400 Bad Request')
    dir_val = spec.get("sessionDir")
    if dir_val is None:
        agent_site_dir = _posixpath.dirname(p)
        sess_dir_src = (_sessions.WS if agent_site_dir in ("", "/")
                        else _os.path.join(_sessions.WS, agent_site_dir.lstrip("/")))
        sess_dir = _os.path.realpath(sess_dir_src)
    else:
        if not isinstance(dir_val, str) or not dir_val.strip():
            return _j({"ok": False,
                       "error": "agent file %s: 'sessionDir' must be a non-empty "
                                "string when present" % p},
                      '400 Bad Request')
        sess_dir = _os.path.expanduser(dir_val.strip())
        real = _os.path.realpath(sess_dir)
        roots = [_sessions.WS_REAL,
                 _os.path.realpath(_os.path.join(_sessions.WS, "run"))]
        if not any(real == r or real.startswith(r + _os.sep) for r in roots):
            return _j({"ok": False,
                       "error": "agent file %s: sessionDir escapes workspace: %s"
                                % (p, dir_val)},
                      '400 Bad Request')
        sess_dir = real
    rel_dir = _os.path.relpath(sess_dir, _sessions.WS)
    if rel_dir == ".." or rel_dir.startswith(".." + _os.sep):
        run_real = _os.path.realpath(_os.path.join(_sessions.WS, "run"))
        rel_run = _os.path.relpath(sess_dir, run_real)
        if rel_run == ".." or rel_run.startswith(".." + _os.sep):
            return _j({"ok": False,
                       "error": "agent file %s: sessionDir %s escapes the site root "
                                "(rel to %s = %s)" % (p, sess_dir, _sessions.WS, rel_dir)},
                      '400 Bad Request')
        rel_dir = _posixpath.normpath(_posixpath.join("run", rel_run))
    site_jsonl = "/" + _posixpath.normpath(_posixpath.join(rel_dir, name + ".jsonl"))
    try:
        session_file = _sessions.resolve_session_path(site_jsonl)
    except ValueError as e:
        return _j({"ok": False,
                   "error": "agent file %s: derived session path rejected: %s"
                            % (p, e)},
                  '400 Bad Request')
    declaration_file = None
    try:
        local = store.head(p).get('rpath')
        if local:
            declaration_file = _os.path.realpath(local)
    except (OSError, AttributeError):
        pass  # informational parsing can still work for a virtual store
    return _j({"ok": True, "name": name, "host": host, "cwd": cwd,
               "declaration_file": declaration_file,
               "profile": profile,
               "sessionDir": sess_dir, "session": site_jsonl,
               "session_file": session_file,
               "hostRouting": "v1: session spawns locally on this 8080 host; "
                              "host is stored for future cross-host routing"})


