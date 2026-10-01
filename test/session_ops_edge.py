#!/usr/bin/env python3
"""session_ops_edge.py — 指挥中心临时会话（run/sessiond/*.agent）的边界与正反控套件。

任务 z293ql（设计稿 §4.2）：cwd 成了客户端可选字段 ⇒ 攻击面比单一指挥中心大一档。
本套件钉住四件收口 + 删除纪律：
  ① 项目清单**服务端枚举**（~/m 自身 + 顶层含 .git 的子目录；⛔ 无静态清单）；
  ② 客户端 cwd 经**成员校验 + 既有逃逸校验**双保险（正控 ≥1 / 反控 ≥5）；
  ③ profile 白名单（形态 + 清单在场 + **只放行 form: interactive**）、
     sessionDir/name 服务端恒定或自动生成；
  ④ ⛔ 无 `command` 键（客户端传 command/profile/sessionDir/name/host ⇒ api 层 400）；
  ⑤ delete 的删除面：realpath 前缀断言（拒符号链接逃逸）+ root 身份断言 + 删前 stat
     三要素证据 + 删后复核；rename 的标题校验与 override 落点（.agent 的 title 字段）。

自包含：夹具一律落 tempfile.mkdtemp()（monkeypatch proc.WS/WS_REAL/_self_host），
不触网、不打现网 8080、不写 ~/m；清理前做 realpath 前缀断言（只删自建临时根）。

运行：cd ~/m/w && python3 test/session_ops_edge.py
Expect: ALL PASS, exit code 0（任一断言失败 → AssertionError + 非 0 退出码）
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ext.sessiond import proc as _proc          # noqa: E402
from ext.sessiond.rpc import api as _api        # noqa: E402


class FakeStore:
    """最小 store：只实现 api 层用到的 read/get_rpath（口径同 resolve_agent_edge.py）。"""

    def __init__(self, root):
        self.root = root

    def get_rpath(self, path):
        p = path.lstrip("/")
        return os.path.join(self.root, p)

    def read(self, path):
        rp = self.get_rpath(path)
        try:
            with open(rp, "rb") as f:
                return f.read()
        except OSError:
            return None


def _write_json(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(doc, ensure_ascii=False, indent=1) + "\n")


def _mkws(base):
    """造一枚假 ~/m：两枚项目（w = .git 目录、lore = .git 文件）、一枚非项目目录（env）、
    运行时区（run/sessiond）、四枚 profile 清单（interactive/task/resident/缺 form）、
    一枚逃出根集的符号链接（evil → base/outside）。"""
    ws = os.path.join(base, "real-ws")
    outside = os.path.join(base, "outside")
    os.makedirs(os.path.join(ws, "w", ".git"))
    os.makedirs(os.path.join(ws, "lore"))
    with open(os.path.join(ws, "lore", ".git"), "w") as f:
        f.write("gitdir: elsewhere\n")
    os.makedirs(os.path.join(ws, "env"))            # 根集内但**不是**项目（凭据面）
    os.makedirs(os.path.join(ws, "run", "sessiond"))
    os.makedirs(outside)
    with open(os.path.join(outside, "secret.txt"), "w") as f:
        f.write("do-not-delete\n")
    os.symlink(outside, os.path.join(ws, "evil"))   # 符号链接逃逸用
    profiles = os.path.join(ws, "bots", "profiles")
    _write_json(os.path.join(profiles, "command-center.json"),
                {"name": "command-center", "form": "interactive", "caps": []})
    _write_json(os.path.join(profiles, "executor.json"),
                {"name": "executor", "form": "task", "caps": ["executor"]})
    _write_json(os.path.join(profiles, "dispatcher.json"),
                {"name": "dispatcher", "form": "resident", "caps": []})
    _write_json(os.path.join(profiles, "noform.json"), {"name": "noform"})
    return ws, outside


def _reject(fn, *a, **kw):
    """断言 fn(*a) 抛 ValueError，返回错误原文（反控用：贴被拒输出）。"""
    try:
        fn(*a, **kw)
    except ValueError as e:
        return str(e)
    raise AssertionError("expected ValueError, got success: %s%r" % (fn, a))


# ---------------------------------------------------------------- ① 项目枚举

def case1_enumeration(ws, outside):
    items = _proc.list_projects()
    rels = [r for r, _p in items]
    assert rels == [".", "lore", "w"], rels          # WS 自身居首 + 顶层 git 仓按名排序
    assert items[0][1] == os.path.realpath(ws), items[0]
    assert rels.count("env") == 0, "非项目目录不得进白名单"
    assert rels.count("run") == 0 and rels.count("evil") == 0, rels
    print("case1 OK: 服务端枚举 = %s（env/run/evil 不在集内）" % rels)


# ---------------------------------------------------------------- ② cwd 正反控

def case2_cwd_controls(ws, outside):
    # 正控（≥1 例）：~/m 内合法项目通过
    got = _proc.resolve_project_cwd("w")
    assert got == os.path.realpath(os.path.join(ws, "w")), got
    assert _proc.resolve_project_cwd(".") == os.path.realpath(ws)
    assert _proc.resolve_project_cwd("lore") == os.path.realpath(
        os.path.join(ws, "lore"))
    print("case2 OK: 正控 3 例通过（'w' / '.' / 'lore' → %s）" % got)

    # 反控（≥5 例，逐例贴被拒原文）
    rejects = [
        ("../escape", "相对逃逸（.. 段）"),
        ("../../etc", "多级相对逃逸"),
        ("/etc", "~/m 外的绝对路径"),
        (os.path.join(outside, "run", "sessiond"), "~/m/run 外的 run 路径（绝对）"),
        ("run/sessiond", "根集内但非项目（运行时区）"),
        ("env", "根集内但非项目（凭据面）"),
        ("evil", "符号链接逃逸（→ 根集外）"),
        ("~/m/w", "~ 形态（不是站内相对路径）"),
        ("", "空串"),
        ("w\x00x", "NUL 字节"),
    ]
    for raw, label in rejects:
        msg = _reject(_proc.resolve_project_cwd, raw)
        print("   reject %-14s %-26r → %s" % (label, raw, msg))
    assert len(rejects) >= 5
    # 非字符串形态
    for bad in (None, 7, ["w"]):
        _reject(_proc.resolve_project_cwd, bad)
    print("case2 OK: 反控 %d 例（+3 例非字符串）全部被拒" % len(rejects))


# ---------------------------------------------------------------- ③ profile 白名单

def case3_profile_whitelist(ws, outside):
    assert _proc.validate_profile("command-center") == "command-center"
    print("case3 OK: 正控 form: interactive 通过（command-center）")
    for name, label in (("executor", "form: task"), ("dispatcher", "form: resident"),
                        ("noform", "缺 form 字段"), ("nosuchprofile", "清单不在场"),
                        ("../evil", "名字含穿越段"), ("CMD", "大写/形态非法"),
                        ("", "空串")):
        msg = _reject(_proc.validate_profile, name)
        print("   reject %-16s %-14r → %s" % (label, name, msg))
    _reject(_proc.validate_profile, None)
    print("case3 OK: 反控 7 例（+1 例非字符串）全部被拒")


# ---------------------------------------------------------------- ④ 建会话与字段集

def case4_create(ws, outside):
    doc = _proc.create_cc_session("w")
    agent_path = doc["agent_path"]
    assert os.path.exists(agent_path), agent_path
    with open(agent_path, encoding="utf-8") as f:
        spec = json.load(f)
    assert set(spec) == {"host", "cwd", "sessionDir", "profile", "name",
                         "createdAt"}, sorted(spec)
    assert "command" not in spec and "cmd" not in spec, spec
    assert spec["profile"] == "command-center", spec
    assert spec["host"] == "test-host", spec
    assert spec["cwd"] == os.path.realpath(os.path.join(ws, "w")), spec
    assert spec["sessionDir"] == os.path.realpath(os.path.join(ws, "run", "sessiond"))
    assert spec["name"] == doc["name"] == os.path.basename(agent_path)[:-len(".agent")]
    assert _proc.SESSION_NAME_OK.match(doc["name"]), doc["name"]
    assert doc["agent_site"] == "/run/sessiond/%s.agent" % doc["name"], doc
    assert doc["session_site"] == "/run/sessiond/%s.jsonl" % doc["name"], doc
    assert oct(os.stat(agent_path).st_mode & 0o777) == "0o600", "文件权限须 0600"
    print("case4 OK: 字段集 = %s（⛔ 无 command 键；0600）" % sorted(spec))

    # profile 不由客户端控：传非 interactive 档 ⇒ 拒
    msg = _reject(_proc.create_cc_session, "w", "executor")
    print("   reject 客户端传 profile='executor' → %s" % msg)
    # cwd 非法 ⇒ 拒（不落盘）
    before = len(os.listdir(os.path.join(ws, "run", "sessiond")))
    _reject(_proc.create_cc_session, "env")
    after = len(os.listdir(os.path.join(ws, "run", "sessiond")))
    assert before == after, (before, after)
    print("case4 OK: 非法 cwd 拒绝且零落盘（%d → %d）" % (before, after))

    # host 不可得 ⇒ 拒（⛔ 不猜）
    saved = _proc._self_host
    _proc._self_host = lambda: None
    try:
        msg = _reject(_proc.create_cc_session, "w")
        print("   reject host 不可得 → %s" % msg)
    finally:
        _proc._self_host = saved
    return doc


# ---------------------------------------------------------------- ⑤ 标题派生两回落

def case5_title(ws, outside):
    d = os.path.join(ws, "run", "sessiond")
    # 回落①：无配对 jsonl
    assert _proc.session_title(os.path.join(d, "ghost.jsonl"), "ghost") == "ghost"
    print("case5 OK: 回落① 无配对 jsonl → 用 .agent name")
    # 回落②：jsonl 在场但无 user 消息
    p2 = os.path.join(d, "nouser.jsonl")
    with open(p2, "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "session", "version": 3, "cwd": ws}) + "\n")
        f.write(json.dumps({"type": "session_info", "name": "[x]"}) + "\n")
        f.write(json.dumps({"type": "message",
                            "message": {"role": "assistant",
                                        "content": [{"type": "text",
                                                     "text": "hi"}]}}) + "\n")
    assert _proc.session_title(p2, "nouser") == "nouser"
    print("case5 OK: 回落② jsonl 无 user 消息 → 用 .agent name")
    # 正控：首条 user 消息的首个非空行（多行取首行 + 截断）
    p3 = os.path.join(d, "withuser.jsonl")
    long_line = "第一行标题\n第二行不该出现"
    with open(p3, "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "session", "version": 3}) + "\n")
        f.write(json.dumps({"type": "message",
                            "message": {"role": "user",
                                        "content": [{"type": "text",
                                                     "text": "\n\n" + long_line}]}})
                + "\n")
        f.write(json.dumps({"type": "message",
                            "message": {"role": "user",
                                        "content": "第二条不该被取"}}) + "\n")
    got = _proc.session_title(p3, "withuser")
    assert got == "第一行标题", got
    p4 = os.path.join(d, "long.jsonl")
    with open(p4, "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "message",
                            "message": {"role": "user",
                                        "content": "x" * 200}}) + "\n")
    assert len(_proc.session_title(p4, "long")) == _proc.AUTO_TITLE_MAX
    # 字符串形态 content 也认
    p5 = os.path.join(d, "strcontent.jsonl")
    with open(p5, "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "message",
                            "message": {"role": "user", "content": "裸串标题"}}) + "\n")
    assert _proc.session_title(p5, "strcontent") == "裸串标题"
    for p in (p2, p3, p4, p5):
        os.unlink(p)
    print("case5 OK: 正控 = 首条 user 消息首个非空行（截断 %d、str/list 两形态都认）"
          % _proc.AUTO_TITLE_MAX)


# ---------------------------------------------------------------- ⑥ 列表与分组

def case6_list(ws, outside, created):
    second = _proc.create_cc_session(".")            # 第二枚：cwd = ~/m 自身
    rows = _proc.list_cc_sessions()
    by_name = {r["name"]: r for r in rows}
    assert created["name"] in by_name and second["name"] in by_name, sorted(by_name)
    r1 = by_name[created["name"]]
    assert r1["project"] == "~/m/w", r1["project"]
    assert by_name[second["name"]]["project"] == "~/m", by_name[second["name"]]
    assert r1["state"] in ("running", "dormant") and isinstance(r1["pids"], list)
    assert r1["agent_site"] == "/run/sessiond/%s.agent" % created["name"]
    assert r1["session_site"] == "/run/sessiond/%s.jsonl" % created["name"]
    groups = sorted({r["project"] for r in rows})
    assert groups == ["~/m", "~/m/w"], groups
    # rename 的 override 生效 + titleSource 标记
    _proc.rename_cc_session(created["name"], "我的工作会话")
    rows = {r["name"]: r for r in _proc.list_cc_sessions()}
    assert rows[created["name"]]["title"] == "我的工作会话", rows[created["name"]]
    assert rows[created["name"]]["titleSource"] == "override"
    assert rows[second["name"]]["titleSource"] == "name"   # 无 jsonl ⇒ 回落 name
    # 坏文件宽容跳过（⛔ 不整页失败）
    bad = os.path.join(ws, "run", "sessiond", "broken.agent")
    with open(bad, "w") as f:
        f.write("{not json\n")
    names = {r["name"] for r in _proc.list_cc_sessions()}
    assert "broken" not in names and created["name"] in names, names
    os.unlink(bad)
    print("case6 OK: 列表按项目分组 = %s；override/auto/name 三种标题来源都在场；"
          "坏文件跳过不炸页" % groups)
    return second


# ---------------------------------------------------------------- ⑦ rename 校验

def case7_rename(ws, outside, created):
    name = created["name"]
    for bad, label in ((None, "非字符串"), ("../x", "穿越段"), ("a/b", "含分隔符"),
                       ("", "空串"), (".hidden", "点开头")):
        msg = _reject(_proc.rename_cc_session, bad, "t")
        print("   reject rename name=%-10r %-10s → %s" % (bad, label, msg))
    msg = _reject(_proc.rename_cc_session, "nosuchsession", "t")
    print("   reject rename 不存在的会话 → %s" % msg)
    msg = _reject(_proc.rename_cc_session, name, "x" * (_proc.TITLE_MAX + 1))
    print("   reject rename 标题超长 → %s" % msg)
    msg = _reject(_proc.rename_cc_session, name, "bad\x01title")
    print("   reject rename 标题含控制字符 → %s" % msg)
    # 空标题 = 清除 override（回落自动标题）
    r = _proc.rename_cc_session(name, "   ")
    assert r["override"] is None and r["title"] == name, r
    with open(os.path.join(ws, "run", "sessiond", name + ".agent"),
              encoding="utf-8") as f:
        assert "title" not in json.load(f), "空标题须删掉 title 键"
    print("case7 OK: 反控 5+3 例被拒；空标题清除 override 且字段回落")


# ---------------------------------------------------------------- ⑧ delete 删除面

def case8_delete(ws, outside, created, second):
    name = created["name"]
    agent = os.path.join(ws, "run", "sessiond", name + ".agent")
    jsonl = os.path.join(ws, "run", "sessiond", name + ".jsonl")
    with open(jsonl, "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "session"}) + "\n")
    st = os.stat(agent)
    r = _proc.delete_cc_session(name)
    assert r["removed"] == ["agent", "jsonl"], r
    assert r["afterExists"] == {"agent": False, "jsonl": False}, r
    assert not os.path.exists(agent) and not os.path.exists(jsonl)
    ev = r["evidence"]["agent"]
    assert ev["ino"] == st.st_ino and ev["size"] == st.st_size, (ev, st)
    assert set(ev) >= {"path", "realpath", "dev", "ino", "size"}, sorted(ev)
    print("case8 OK: 删前身份证据（dev=%s ino=%s size=%s realpath=%s）+ 删后复核 exists=False"
          % (ev["dev"], ev["ino"], ev["size"], ev["realpath"]))
    # 幂等：再删一次（.agent 已不在）⇒ 名字校验通过、零 removed、复核仍为假
    r2 = _proc.delete_cc_session(name)
    assert r2["removed"] == [] and r2["afterExists"] == {"agent": False,
                                                         "jsonl": False}, r2
    print("case8 OK: 重复删除幂等（removed=[]）")

    # 反控 A：符号链接逃逸 —— <name>.agent 是指向根集外文件的软链 ⇒ 拒且目标未动
    esc = os.path.join(ws, "run", "sessiond", "esc.agent")
    os.symlink(os.path.join(outside, "secret.txt"), esc)
    target_before = os.stat(os.path.join(outside, "secret.txt"))
    msg = _reject(_proc.delete_cc_session, "esc")
    print("   reject delete 符号链接逃逸 → %s" % msg)
    assert os.path.exists(os.path.join(outside, "secret.txt")), "目标文件不得被动"
    assert os.stat(os.path.join(outside, "secret.txt")).st_ino == target_before.st_ino
    os.unlink(esc)

    # 反控 B：名字非法（穿越/分隔符）
    for bad in ("../evil", "a/b", "", None, "x" * 80):
        msg = _reject(_proc.delete_cc_session, bad)
        print("   reject delete name=%-10r → %s" % (bad, msg))

    # 反控 C：root 身份断言 —— 会话目录解析成 WS 根（合成形态：SESSIONS_SUBDIR 空）
    saved = _proc.SESSIONS_SUBDIR
    _proc.SESSIONS_SUBDIR = ()
    try:
        msg = _reject(_proc.session_files, second["name"])
        print("   reject root 身份断言（会话目录 == WS 根）→ %s" % msg)
    finally:
        _proc.SESSIONS_SUBDIR = saved
    # 清理第二枚（走正常删除面）
    r3 = _proc.delete_cc_session(second["name"])
    assert r3["afterExists"] == {"agent": False, "jsonl": False}, r3
    print("case8 OK: 反控 A/B/C 全部被拒，第二枚会话正常收口")


# ---------------------------------------------------------------- ⑨ api 层（in-process）

def case9_api(ws, outside):
    """op 层：客户端传服务端恒定字段 ⇒ 400；正常建/列/改名/删各一次（in-process，
    ⛔ 不打现网 8080）。"""
    store = FakeStore(ws)

    def call(**kw):
        meta, body = _api.interp(store, **kw)
        return meta.get("http_status", "200 OK"), json.loads(body)

    st, doc = call(op="create_session", cwd="w")
    assert st == "200 OK" and doc["ok"], (st, doc)
    name = doc["name"]
    assert doc["profile"] == "command-center" and doc["host"] == "test-host", doc
    assert doc["agent"].endswith("/run/sessiond/%s.agent" % name), doc
    print("case9 OK: op=create_session → %s（agent=%s）" % (name, doc["agent"]))

    for kw, label in (({"op": "create_session", "cwd": "w", "command": "rm -rf /"},
                       "command"),
                      ({"op": "create_session", "cwd": "w", "profile": "executor"},
                       "profile"),
                      ({"op": "create_session", "cwd": "w", "sessionDir": "/tmp"},
                       "sessionDir"),
                      ({"op": "create_session", "cwd": "w", "name": "pwn"}, "name"),
                      ({"op": "create_session", "cwd": "w", "host": "mac"}, "host")):
        st, doc = call(**kw)
        assert st.startswith("400"), (label, st, doc)
        assert doc["ok"] is False
        print("   reject 客户端传 %-11s → %s %s" % (label, st, doc["error"]))

    st, doc = call(op="create_session", cwd="env")
    assert st.startswith("400") and "whitelist" in doc["error"], (st, doc)
    print("   reject 客户端传 cwd='env'（根集内非项目）→ %s %s" % (st, doc["error"]))
    st, doc = call(op="create_session")
    assert st.startswith("400"), (st, doc)
    print("   reject 缺 cwd → %s %s" % (st, doc["error"]))

    st, doc = call(op="list_sessions")
    assert st == "200 OK" and doc["ok"], (st, doc)
    assert any(s["name"] == name for s in doc["sessions"]), doc
    assert doc["projects"][0]["rel"] == ".", doc["projects"]
    print("case9 OK: op=list_sessions → %d 枚会话 / %d 个项目选项"
          % (len(doc["sessions"]), len(doc["projects"])))

    st, doc = call(op="rename_session", session="/run/sessiond/%s.jsonl" % name,
                   title="改名后的标题")
    assert st == "200 OK" and doc["title"] == "改名后的标题", (st, doc)
    st, doc = call(op="session_tabs")
    assert st == "200 OK" and doc["ok"] and doc["prefix"] == "test-host", (st, doc)
    assert len(doc["rows"]) == 1 and doc["rows"][0]["title"] == "改名后的标题", doc
    assert doc["rows"][0]["url"] == ("/test-host/run/sessiond/%s.agent?v=chat" % name), doc
    print("case9 OK: op=rename_session + op=session_tabs（prefix=%s、url=%s）"
          % (doc["prefix"], doc["rows"][0]["url"]))

    st, doc = call(op="delete_session", session="/run/sessiond/%s.jsonl" % name,
                   confirm="1")
    assert st == "200 OK" and doc["ok"], (st, doc)
    # 本例从未 attach 过 ⇒ jsonl 从未落盘 ⇒ 只删 .agent（缺文件不报错、删后复核仍为假）
    assert doc["removed"] == ["agent"], doc
    assert doc["evidence"]["jsonl"]["absent"] is True, doc
    assert doc["afterExists"] == {"agent": False, "jsonl": False}, doc
    assert doc["process"]["found"] is False, doc      # 从未打开 ⇒ ⛔ 不为它建桥拉起
    st, doc = call(op="session_tabs")
    assert doc["rows"] == [], doc                      # 空态吐零行
    print("case9 OK: op=delete_session（process.found=False、未建桥）+ 空态 session_tabs 零行")

    st, doc = call(op="delete_session", session="/run/sessiond/%s.jsonl" % name)
    assert st.startswith("400") and "confirm" in doc["error"], (st, doc)
    print("   reject delete 缺 confirm → %s %s" % (st, doc["error"]))
    st, doc = call(op="delete_session", session="/etc/passwd", confirm="1")
    assert st.startswith("400"), (st, doc)
    print("   reject delete 站内路径非法 → %s %s" % (st, doc["error"]))
    st, doc = call(op="nope")
    assert st.startswith("400") and doc["ok"] is False, (st, doc)
    # 既有实现里「未知 op」的 400 排在 session 参数门之后（_bridge_or_err 先拒缺参）
    # ⇒ 本件零改动，这里只钉「新增分支没把既有 400 口径改掉」。
    print("case9 OK: 非本件 op（op=nope）仍 400（既有 session 参数门在前，口径未变）→ %s"
          % doc["error"])


def case10_agent_profile(ws, outside):
    """`_resolve_agent` 的 profile 字段：白名单放行 + 登记到拉起参数（供 _spawn 回注）。"""
    from ext.sessiond import bridge as _b
    store = FakeStore(ws)
    d = os.path.join(ws, "run", "sessiond")

    def mk(name, extra):
        doc = {"host": "test-host", "cwd": ws,
               "sessionDir": os.path.realpath(d)}
        doc.update(extra)
        _write_json(os.path.join(d, name + ".agent"), doc)
        return "/run/sessiond/%s.agent" % name

    # 正控：form: interactive 的清单名放行
    p1 = mk("t-ok", {"profile": "command-center"})
    meta, body = _api._resolve_agent(store, p1)
    doc = json.loads(body)
    assert meta["http_status"] == "200 OK", (meta, doc)
    assert doc["ok"] and doc["profile"] == "command-center", doc
    key = _proc.resolve_session_path(doc["session"])
    with _b._SPAWN_OVERRIDES_LOCK:
        ov = dict(_b._SPAWN_OVERRIDES[key])
    assert ov["profile"] == "command-center" and ov["cwd"] == os.path.realpath(ws), ov
    print("case10 OK: 正控 .agent 带 profile=command-center → 200 + 登记拉起参数 %s" % ov)

    # 反控：form: task / form: resident / 缺 form / 清单不在场 / 形态非法 → 400
    for extra, label in (({"profile": "executor"}, "form: task"),
                         ({"profile": "dispatcher"}, "form: resident"),
                         ({"profile": "noform"}, "缺 form 字段"),
                         ({"profile": "nosuchprofile"}, "清单不在场"),
                         ({"profile": "../evil"}, "名字形态非法"),
                         ({"profile": ""}, "空串"),
                         ({"profile": 7}, "非字符串")):
        site = mk("t-bad", extra)
        meta, body = _api._resolve_agent(store, site)
        doc = json.loads(body)
        assert meta["http_status"].startswith("400"), (label, meta, doc)
        assert doc["ok"] is False
        print("   reject .agent profile %-14s → %s %s"
              % (label, meta["http_status"], doc["error"]))

    # 存量形态：不带 profile 字段 ⇒ 200 且 profile=None（裸 pi 会话，逐字不变）
    p3 = mk("t-legacy", {})
    meta, body = _api._resolve_agent(store, p3)
    doc = json.loads(body)
    assert meta["http_status"] == "200 OK" and doc["profile"] is None, (meta, doc)
    key = _proc.resolve_session_path(doc["session"])
    with _b._SPAWN_OVERRIDES_LOCK:
        assert _b._SPAWN_OVERRIDES[key]["profile"] is None
    print("case10 OK: 存量 .agent（无 profile 字段）→ 200 且 profile=None（行为逐字不变）")
    for n in ("t-ok", "t-bad", "t-legacy"):
        os.unlink(os.path.join(d, n + ".agent"))


def case11_spawn_env(ws, outside):
    """`Supervisor._spawn` 的 env 面：profile 在场 ⇒ clean_env() 之后回注 DISPATCH_PROFILE；
    缺省 ⇒ ⛔ 不注（既有 .agent/.jsonl 会话的 spawn env 逐字不变）。
    monkeypatch subprocess.Popen ⇒ ⛔ 不真拉进程。"""
    import io as _io
    import subprocess as _sp

    class _FakeProc:
        def __init__(self):
            self.pid = 424242
            self.stdin = _io.BytesIO()
            self.stdout = _io.BytesIO(b"")

        def poll(self):
            return None

    captured = {}

    def fake_popen(cmd, **kw):
        captured["cmd"] = cmd
        captured["env"] = kw.get("env")
        captured["cwd"] = kw.get("cwd")
        return _FakeProc()

    jsonl = os.path.join(ws, "run", "sessiond", "spawnenv.jsonl")
    saved = _sp.Popen
    _proc.subprocess.Popen = fake_popen
    try:
        for profile, expect in (("command-center", "command-center"), (None, None)):
            captured.clear()
            sup = _proc.Supervisor(jsonl, on_event=lambda o: None, cwd=ws,
                                   profile=profile)
            sup._spawn()
            env = captured["env"]
            assert _proc.HOST_MARKER in env, "既有标记必须仍在场"
            if expect:
                assert env.get("DISPATCH_PROFILE") == expect, env.get("DISPATCH_PROFILE")
            else:
                assert "DISPATCH_PROFILE" not in env, env.get("DISPATCH_PROFILE")
            print("case11 OK: profile=%-16r ⇒ spawn env DISPATCH_PROFILE=%r"
                  "（HOST_MARKER 仍在场，cwd=%s）"
                  % (profile, env.get("DISPATCH_PROFILE"), captured["cwd"]))
    finally:
        _proc.subprocess.Popen = saved
        if os.path.exists(jsonl):
            os.unlink(jsonl)


def case12_drop_session(ws, outside):
    """删除的**进程面**：`bridge.drop_session` 绝不建桥；命中桥接 ⇒ `Supervisor.shutdown`
    杀进程（SIGTERM 档）+ 摘除登记。monkeypatch subprocess.Popen ⇒ ⛔ 不真拉 pi。"""
    import io as _io
    import threading as _th
    from ext.sessiond import bridge as _b

    site = "/run/sessiond/dropcase.jsonl"
    key = _proc.resolve_session_path(site)

    # ① 未命中（从未打开）⇒ found=False 且 **⛔ 不建桥**
    r = _b.drop_session(site)
    assert r == {"found": False, "killed": False}, r
    with _b._BRIDGES_LOCK:
        assert key not in _b._BRIDGES, "drop_session ⛔ 不得为被删的会话建桥/拉起"
    print("case12 OK: 未命中桥接 ⇒ found=False 且零建桥（删从未打开的会话不会反而拉起它）")

    class _FakeProc:
        def __init__(self):
            self.pid = 424243
            self.stdin = _io.BytesIO()
            self.stdout = _io.BytesIO(b"")
            self._ev = _th.Event()
            self.terminated = False
            self.killed = False

        def poll(self):
            return 0 if self._ev.is_set() else None

        def wait(self, timeout=None):
            if self._ev.wait(timeout if timeout is not None else 20):
                return 0
            raise _proc.subprocess.TimeoutExpired("fake", timeout)

        def terminate(self):
            self.terminated = True
            self._ev.set()

        def kill(self):
            self.killed = True
            self._ev.set()

    holder = {}

    def fake_popen(cmdo, **kw):
        holder["proc"] = _FakeProc()
        holder["env"] = kw.get("env")
        return holder["proc"]

    saved = _proc.subprocess.Popen
    _proc.subprocess.Popen = fake_popen
    try:
        b = _b.Bridge(site, cwd=ws, profile="command-center")
        with _b._BRIDGES_LOCK:
            _b._BRIDGES[key] = b
        _b.set_session_cwd(site, ws, "command-center")
        b.sup.ensure_started()
        assert b.sup.wait_ready(timeout=15), "假进程未就位"
        assert holder["env"].get("DISPATCH_PROFILE") == "command-center", holder["env"]
        r = _b.drop_session(site)
        assert r["found"] is True and r["killed"] is True, r
        assert holder["proc"].terminated is True, "SIGTERM 档未走"
        assert holder["proc"].killed is False, "已优雅退出 ⇒ ⛔ 不应再 SIGKILL"
        with _b._BRIDGES_LOCK:
            assert key not in _b._BRIDGES, "桥接未摘除"
        with _b._SPAWN_OVERRIDES_LOCK:
            assert key not in _b._SPAWN_OVERRIDES, "拉起参数登记未摘除"
        print("case12 OK: 命中桥接 ⇒ shutdown 杀进程（terminated=True、未走 SIGKILL）"
              "+ 桥接与拉起登记都摘除（返回 %s）" % r)
        # 再删一次 ⇒ 幂等（已摘除）
        r2 = _b.drop_session(site)
        assert r2 == {"found": False, "killed": False}, r2
        print("case12 OK: 重复 drop 幂等（found=False）")
    finally:
        _proc.subprocess.Popen = saved
        with _b._BRIDGES_LOCK:
            _b._BRIDGES.pop(key, None)


def main():
    base = tempfile.mkdtemp(prefix="sessiond-ops-edge.")
    saved = (_proc.WS, _proc.WS_REAL, _proc._self_host)
    try:
        ws, outside = _mkws(base)
        _proc.WS = ws
        _proc.WS_REAL = os.path.realpath(ws)
        _proc._self_host = lambda: "test-host"
        case1_enumeration(ws, outside)
        case2_cwd_controls(ws, outside)
        case3_profile_whitelist(ws, outside)
        created = case4_create(ws, outside)
        case5_title(ws, outside)
        second = case6_list(ws, outside, created)
        case7_rename(ws, outside, created)
        case8_delete(ws, outside, created, second)
        case9_api(ws, outside)
        case10_agent_profile(ws, outside)
        case11_spawn_env(ws, outside)
        case12_drop_session(ws, outside)
        print("ALL PASS")
        return 0
    finally:
        _proc.WS, _proc.WS_REAL, _proc._self_host = saved
        # 清理面 root 身份断言（全局红线「删除类操作」）：只删本函数自建的临时根，
        # 且它不得等于/包含生产根（~/m）；不满足 ⇒ 打印拒绝并**不删**（⛔ ignore_errors）。
        real = os.path.realpath(base)
        prod = os.path.realpath(os.path.expanduser("~/m"))
        if not real.startswith(tempfile.gettempdir() + os.sep) \
                or real == prod or prod.startswith(real + os.sep) \
                or real == os.path.realpath(os.path.expanduser("~")):
            print("SKIP cleanup: %r is not a self-created temp dir" % real)
        else:
            shutil.rmtree(real)


if __name__ == "__main__":
    sys.exit(main())
