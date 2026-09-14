#!/usr/bin/env python3
"""ensure_bot_registration edge 用例（任务 6k39t0）。

in-process、临时 root、无 socket；临时 root 内自备 agents/、env/host-id、bots/profiles/<名>.json。
运行：cd ~/m/w && python3 test/create_bot_edge.py
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ext.sessiond import proc as _proc  # noqa: E402

# 真 agentctl 路径（用系统 python3 跑，与 web 进程同解释器）
AGENTCTL = os.path.expanduser("~/m/agentd/agentctl.py")
PYTHON3 = "/usr/bin/python3"


def _md5(path):
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def _make_root(canonical="testhost"):
    """创建临时 root，含 agents/、env/host-id、bots/profiles/executor.json。
    env/host-id 用本机真实 hostname 映射到 canonical，使 agentctl 能正确解析。"""
    import socket as _s
    root = tempfile.mkdtemp(prefix="cb-edge-")
    os.makedirs(os.path.join(root, "agents", "bot"))
    os.makedirs(os.path.join(root, "agents", "task"))
    os.makedirs(os.path.join(root, "env"))
    os.makedirs(os.path.join(root, "bots", "profiles"))
    # env/host-id：本机真实 hostname → canonical
    with open(os.path.join(root, "env", "host-id"), "w") as f:
        f.write("%s %s\n" % (_s.gethostname(), canonical))
    # bots/profiles/executor.json（最小合法 profile）
    with open(os.path.join(root, "bots", "profiles", "executor.json"), "w") as f:
        json.dump({"caps": []}, f)
    # workdir（合法目录）
    wd = os.path.join(root, "workdir")
    os.makedirs(wd)
    return root, wd


def _patch_self_host(root, canonical="testhost"):
    """monkeypatch _proc._self_host 使其直接返回指定规范名（绕过 hostname 查表）。"""
    orig = _proc._self_host
    _proc._self_host = lambda: canonical
    return orig


def _call(session_path, profile, workdir, root, ctl=None, **opts):
    """调 ensure_bot_registration；**opts = 任务 9xn4wa 新增的可选字段
    （description/restart_policy/subscribes/reaper），缺省不传 = 6k39t0 现行为。"""
    if ctl is None:
        ctl = AGENTCTL
    return _proc.ensure_bot_registration(session_path, profile, workdir,
                                         root=root, ctl=ctl, **opts)


# ---- 用例 ----

def case1_happy_path():
    """happy path：核 spec.json 六字段 + enable.json by/note 在场。"""
    root, wd = _make_root()
    orig = _patch_self_host(root)
    try:
        doc, err = _call("/agents/bot/testbot1/spec.json", "executor", wd, root)
        assert err is None, "unexpected error: %s" % err
        assert doc["created"] is True
        assert doc["participant"] == "bot/testbot1"
        assert doc["profile"] == "executor"
        assert doc["host"] == "testhost"
        # 核 spec.json 六字段
        spec_path = os.path.join(root, "agents", "bot", "testbot1", "spec.json")
        assert os.path.exists(spec_path), "spec.json not created"
        with open(spec_path) as f:
            spec = json.load(f)
        assert "DISPATCH_PROFILE=executor" in spec["command"], spec["command"]
        assert "AGENTD_RESIDENT=1" in spec["command"]
        assert "AGENTD_SESSION_NAME=bot/testbot1" in spec["command"]
        assert spec["workdir"] == wd or spec["workdir"] == os.path.expanduser(wd)
        assert spec["creator"] == "web/testhost", spec["creator"]
        assert spec["restartPolicy"] == "auto", spec["restartPolicy"]
        assert spec["host"] == "testhost", spec["host"]
        assert spec["createdByHost"] == "testhost", spec["createdByHost"]
        # reaper 缺省恒写职位信箱（任务 9xn4wa，登记方裁定 2026-09-14）：使
        # runner.resolve_reaper 命中「显式收件面」档而不报「spec 缺 reaper 字段」异常
        assert spec["reaper"] == "topic/dispatcher", spec
        assert doc["reaper"] == "topic/dispatcher" and doc["reaperDefaulted"] is True, doc
        # 缺省不传 description/subscribes ⇒ 两键不在场（spec 逐字与 6k39t0 现行为一致）
        assert "name" not in spec and "subscribes" not in spec, spec
        # 核 enable.json
        enable_path = os.path.join(root, "agents", "bot", "testbot1", "enable.json")
        assert os.path.exists(enable_path), "enable.json not created"
        with open(enable_path) as f:
            en = json.load(f)
        assert en["by"] == "web/testhost", en
        assert "created via 8080 chat URL" in en.get("note", ""), en
        print("case1 OK: happy path — spec.json 六字段 + reaper 缺省恒写"
              " + enable.json by/note 均正确")
    finally:
        _proc._self_host = orig
        shutil.rmtree(root, ignore_errors=True)


def case2_spec_already_exists():
    """spec 已在场 → created:false 且 spec.json md5 前后一致。"""
    root, wd = _make_root()
    orig = _patch_self_host(root)
    try:
        # 先创建
        doc1, err1 = _call("/agents/bot/testbot2/spec.json", "executor", wd, root)
        assert err1 is None and doc1["created"] is True
        spec_path = os.path.join(root, "agents", "bot", "testbot2", "spec.json")
        md5_before = _md5(spec_path)
        # 再调用
        # S1（评审 gk305i）：create-only 的「零写盘」不止 spec.json 的 md5——目录条目集与
        # enable.json 的内容/mtime 也须逐一不变，否则未来引入的副作用会被回归网漏掉。
        adir = os.path.join(root, "agents", "bot", "testbot2")
        en_path = os.path.join(adir, "enable.json")
        listing_before = sorted(os.listdir(adir))
        en_md5_before = _md5(en_path) if os.path.exists(en_path) else None
        en_mtime_before = os.path.getmtime(en_path) if os.path.exists(en_path) else None
        doc2, err2 = _call("/agents/bot/testbot2/spec.json", "executor", wd, root)
        assert err2 is None, "unexpected error: %s" % err2
        assert doc2["created"] is False
        assert doc2["effective"]["profile"] == "executor"
        md5_after = _md5(spec_path)
        assert md5_before == md5_after, "spec.json changed! %s != %s" % (md5_before, md5_after)
        assert sorted(os.listdir(adir)) == listing_before, \
            "create-only 不得增删目录条目：%r → %r" % (listing_before, sorted(os.listdir(adir)))
        if en_md5_before is not None:
            assert _md5(en_path) == en_md5_before, "enable.json 内容变了"
            assert os.path.getmtime(en_path) == en_mtime_before, "enable.json mtime 变了"
        print("case2 OK: spec 已在场 → created:false，md5 前后一致 + 目录条目/enable.json 零写盘")
    finally:
        _proc._self_host = orig
        shutil.rmtree(root, ignore_errors=True)


def case3_profile_not_found():
    """profile 不在 bots/profiles/ → 400 且消息含现场枚举的可用清单。"""
    root, wd = _make_root()
    try:
        doc, err = _call("/agents/bot/testbot3/spec.json", "nonexistent", wd, root)
        assert doc is None
        assert err is not None
        assert "nonexistent" in err
        assert "executor" in err, "可用清单未含 executor: %s" % err
        assert not err.startswith("409:")
        print("case3 OK: profile 不存在 → 400 + 可用清单含 executor")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def case4_profile_bad_syntax():
    """profile 含 ; 或 / 或前导 . → 400。"""
    root, wd = _make_root()
    try:
        for bad in ["exec;rm", "exec/utor", ".hidden"]:
            doc, err = _call("/agents/bot/testbot4/spec.json", bad, wd, root)
            assert doc is None, "expected error for profile=%r" % bad
            assert err is not None
            assert not err.startswith("409:")
        print("case4 OK: profile 含 ; / 前导. → 均 400")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def case5_workdir_invalid():
    """workdir 不存在 ∨ 相对路径 ∨ realpath 落在 agents/ 内 → 400。"""
    root, wd = _make_root()
    try:
        # 不存在
        doc, err = _call("/agents/bot/tb5a/spec.json", "executor", "/nonexistent/path", root)
        assert doc is None and err is not None and not err.startswith("409:")
        # 相对路径
        doc, err = _call("/agents/bot/tb5b/spec.json", "executor", "relative/path", root)
        assert doc is None and err is not None and not err.startswith("409:")
        # agents/ 内
        agents_dir = os.path.join(root, "agents")
        doc, err = _call("/agents/bot/tb5c/spec.json", "executor", agents_dir, root)
        assert doc is None and err is not None and not err.startswith("409:")
        print("case5 OK: workdir 不存在/相对路径/agents内 → 均 400")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def case6_task_family():
    """task 族路径 → 400。"""
    root, wd = _make_root()
    try:
        doc, err = _call("/agents/task/mytask/spec.json", "executor", wd, root)
        assert doc is None
        assert err is not None
        assert "bot 族" in err or "task" in err
        assert not err.startswith("409:")
        print("case6 OK: task 族路径 → 400 + 指引 bot 族")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def case7_name_collision_409():
    """同名 task/<名> 目录在场 → agentctl 拒绝被如实映射（409 + stderr 原文）。"""
    root, wd = _make_root()
    orig = _patch_self_host(root)
    try:
        # 先建同名 task 目录
        task_dir = os.path.join(root, "agents", "task", "collidebot")
        os.makedirs(task_dir)
        doc, err = _call("/agents/bot/collidebot/spec.json", "executor", wd, root)
        assert doc is None, "expected error for name collision"
        assert err is not None
        assert err.startswith("409:"), "expected 409 prefix, got: %s" % err
        # S2（评审 gk305i）：409 之外还须核 agentctl 的 stderr 原文被透传（验收口径写的
        # 就是「409 + stderr 原文」）——只核前缀会漏掉「错误被吞成空消息」的回归。
        assert "拒绝创建" in err and "已存在" in err, "stderr 原文未透传：%r" % err
        assert "rc=" in err, "缺 agentctl 退出码上下文：%r" % err
        print("case7 OK: 同名 task/ 在场 → 409 + stderr 原文（拒绝创建/已存在/rc= 均在场）")
    finally:
        _proc._self_host = orig
        shutil.rmtree(root, ignore_errors=True)


def case8_idempotent():
    """连续两次调用第二次 created:false（幂等）。"""
    root, wd = _make_root()
    orig = _patch_self_host(root)
    try:
        doc1, err1 = _call("/agents/bot/testbot8/spec.json", "executor", wd, root)
        assert err1 is None and doc1["created"] is True
        doc2, err2 = _call("/agents/bot/testbot8/spec.json", "executor", wd, root)
        assert err2 is None
        assert doc2["created"] is False
        print("case8 OK: 连续两次调用第二次 created:false（幂等）")
    finally:
        _proc._self_host = orig
        shutil.rmtree(root, ignore_errors=True)


def case9_enable_preexisting():
    """enable 步撞「已存在」→ created:true 且带 enablePreexisting（伪造 ctl 触发）。"""
    root, wd = _make_root()
    orig = _patch_self_host(root)
    # 伪造 ctl：register 成功，enable 返回 rc=2 + "enable.json 已存在"
    fake_ctl = os.path.join(root, "fake_agentctl.py")
    with open(fake_ctl, "w") as f:
        f.write("""\
import sys, os, json
args = sys.argv[1:]
if "register" in args:
    # 模拟 register 成功：写 spec.json
    name_idx = args.index("--name") + 1
    name = args[name_idx]
    root_idx = args.index("--root") + 1
    root = args[root_idx]
    adir = os.path.join(root, "agents", "bot", name)
    os.makedirs(adir, exist_ok=True)
    spec = {"command": "DISPATCH_PROFILE=executor AGENTD_RESIDENT=1 exec true",
            "workdir": "/tmp", "creator": "web/testhost", "restartPolicy": "auto",
            "host": "testhost", "createdByHost": "testhost"}
    with open(os.path.join(adir, "spec.json"), "w") as sf:
        json.dump(spec, sf)
    sys.exit(0)
if "enable" in args:
    # 模拟 enable.json 已存在
    sys.stderr.write("enable.json 已存在——只进不退，不可重复写定\\n")
    sys.exit(2)
sys.exit(0)
""")
    try:
        # 预先写 enable.json（模拟 scheduler 先行放行）
        adir = os.path.join(root, "agents", "bot", "testbot9")
        os.makedirs(adir, exist_ok=True)
        with open(os.path.join(adir, "enable.json"), "w") as f:
            json.dump({"ts": "2026-01-01", "by": "scheduler"}, f)
        doc, err = _call("/agents/bot/testbot9/spec.json", "executor", wd,
                         root, ctl=fake_ctl)
        assert err is None, "unexpected error: %s" % err
        assert doc["created"] is True
        assert doc.get("enablePreexisting") is True, doc
        print("case9 OK: enable 撞「已存在」→ created:true + enablePreexisting:true")
    finally:
        _proc._self_host = orig
        shutil.rmtree(root, ignore_errors=True)


def case10_mismatch():
    """已在场但 profile/workdir 与入参不同 → mismatch 列表非空。"""
    root, wd = _make_root()
    orig = _patch_self_host(root)
    try:
        doc1, err1 = _call("/agents/bot/testbot10/spec.json", "executor", wd, root)
        assert err1 is None and doc1["created"] is True
        # 用不同 workdir 再调
        wd2 = os.path.join(root, "workdir2")
        os.makedirs(wd2)
        doc2, err2 = _call("/agents/bot/testbot10/spec.json", "executor", wd2, root)
        assert err2 is None
        assert doc2["created"] is False
        assert "workdir" in doc2["mismatch"], doc2
        print("case10 OK: 已在场 + workdir 不同 → mismatch 含 workdir")
    finally:
        _proc._self_host = orig
        shutil.rmtree(root, ignore_errors=True)


# ---- 任务 9xn4wa：表单可选字段 + bot_form_meta ----

def _spec(root, name):
    with open(os.path.join(root, "agents", "bot", name, "spec.json")) as f:
        return json.load(f)


def case11_description():
    """description → spec `name` 字段；不传 → 该键不在场（零回归）。"""
    root, wd = _make_root()
    orig = _patch_self_host(root)
    try:
        doc, err = _call("/agents/bot/tb11a/spec.json", "executor", wd, root,
                         description="e2e form 验证")
        assert err is None, err
        assert doc["created"] is True and doc["description"] == "e2e form 验证", doc
        assert _spec(root, "tb11a")["name"] == "e2e form 验证"
        # 空串/纯空白 = 未给 → 不写键
        doc2, err2 = _call("/agents/bot/tb11b/spec.json", "executor", wd, root,
                           description="   ")
        assert err2 is None, err2
        assert "name" not in _spec(root, "tb11b"), _spec(root, "tb11b")
        assert "description" not in doc2, doc2
        print("case11 OK: description → spec.name；空串不写键")
    finally:
        _proc._self_host = orig
        shutil.rmtree(root, ignore_errors=True)


def case12_reaper():
    """reaper 显式值透传 → spec `reaper`；不传 → 缺省职位信箱（恒写）。"""
    root, wd = _make_root()
    orig = _patch_self_host(root)
    try:
        doc, err = _call("/agents/bot/tb12a/spec.json", "executor", wd, root,
                         reaper="bot/some-reaper")
        assert err is None, err
        assert _spec(root, "tb12a")["reaper"] == "bot/some-reaper"
        assert doc["reaper"] == "bot/some-reaper" and doc["reaperDefaulted"] is False, doc
        doc2, err2 = _call("/agents/bot/tb12b/spec.json", "executor", wd, root)
        assert err2 is None, err2
        assert _spec(root, "tb12b")["reaper"] == "topic/dispatcher"
        assert doc2["reaperDefaulted"] is True, doc2
        print("case12 OK: reaper 显式值透传 / 缺省恒写 topic/dispatcher")
    finally:
        _proc._self_host = orig
        shutil.rmtree(root, ignore_errors=True)


def case13_subscribes():
    """subscribes：逗号分隔→数组、去重保序；不传 → 键不在场。"""
    root, wd = _make_root()
    orig = _patch_self_host(root)
    try:
        doc, err = _call("/agents/bot/tb13a/spec.json", "executor", wd, root,
                         subscribes="topic/a, topic/b,topic/a")
        assert err is None, err
        assert _spec(root, "tb13a")["subscribes"] == ["topic/a", "topic/b"], \
            _spec(root, "tb13a")
        assert doc["subscribes"] == ["topic/a", "topic/b"], doc
        # 数组形态也接受
        doc2, err2 = _call("/agents/bot/tb13b/spec.json", "executor", wd, root,
                           subscribes=["topic/c"])
        assert err2 is None, err2
        assert _spec(root, "tb13b")["subscribes"] == ["topic/c"]
        # 不传 → 键不在场
        _call("/agents/bot/tb13c/spec.json", "executor", wd, root)
        assert "subscribes" not in _spec(root, "tb13c")
        print("case13 OK: subscribes 去重保序（字符串/数组两形态）；不传不写键")
    finally:
        _proc._self_host = orig
        shutil.rmtree(root, ignore_errors=True)


def case14_subscribes_non_topic():
    """subscribes 含非 topic/ 族 ∨ 非法文法 → 400 且零落盘。"""
    root, wd = _make_root()
    orig = _patch_self_host(root)
    try:
        for bad in ("bot/dev-dispatcher", "task/x", "nope", "topic/x,bot/y",
                    "topic/../x", "topic/"):
            doc, err = _call("/agents/bot/tb14/spec.json", "executor", wd, root,
                             subscribes=bad)
            assert doc is None, "expected reject for subscribes=%r" % bad
            assert err and not err.startswith("409:"), (bad, err)
        assert not os.path.exists(os.path.join(root, "agents", "bot", "tb14")), \
            "非法 subscribes 拒后不得落目录"
        print("case14 OK: subscribes 非 topic/ 族 ∨ 非法文法 → 400 + 零落盘")
    finally:
        _proc._self_host = orig
        shutil.rmtree(root, ignore_errors=True)


def case15_restart_policy():
    """restartPolicy：非法值 → 400；manual/one-shot → spec 值正确；缺省 auto。"""
    root, wd = _make_root()
    orig = _patch_self_host(root)
    try:
        for bad in ("always", "Auto", "one_shot", "restart", "auto,manual"):
            doc, err = _call("/agents/bot/tb15bad/spec.json", "executor", wd, root,
                             restart_policy=bad)
            assert doc is None and err and not err.startswith("409:"), (bad, doc, err)
        assert not os.path.exists(os.path.join(root, "agents", "bot", "tb15bad"))
        for i, rp in enumerate(("manual", "one-shot", "auto")):
            nm = "tb15-%d" % i
            doc, err = _call("/agents/bot/%s/spec.json" % nm, "executor", wd, root,
                             restart_policy=rp)
            assert err is None, err
            assert _spec(root, nm)["restartPolicy"] == rp, _spec(root, nm)
            assert doc["restartPolicy"] == rp, doc
        # 缺省（不传 ∨ 空串）= auto（= 6k39t0 现行为）
        _call("/agents/bot/tb15def/spec.json", "executor", wd, root)
        assert _spec(root, "tb15def")["restartPolicy"] == "auto"
        _call("/agents/bot/tb15empty/spec.json", "executor", wd, root, restart_policy="  ")
        assert _spec(root, "tb15empty")["restartPolicy"] == "auto"
        print("case15 OK: restartPolicy 非法值 400；manual/one-shot/auto 透传；缺省 auto")
    finally:
        _proc._self_host = orig
        shutil.rmtree(root, ignore_errors=True)


def case16_bad_description_and_reaper():
    """description 超 200 字符 ∨ 含 NUL → 400；reaper 非法文法 → 400（不静默回落）。"""
    root, wd = _make_root()
    orig = _patch_self_host(root)
    try:
        doc, err = _call("/agents/bot/tb16/spec.json", "executor", wd, root,
                         description="x" * 201)
        assert doc is None and err and "201" in err and "200" in err, err
        doc, err = _call("/agents/bot/tb16/spec.json", "executor", wd, root,
                         description="a\x00b")
        assert doc is None and err and "NUL" in err, err
        for bad in ("foo", "nope/x", "topic/../x", "task/a/b", ".hidden/x", "x/y/z"):
            doc, err = _call("/agents/bot/tb16/spec.json", "executor", wd, root,
                             reaper=bad)
            assert doc is None, "expected reject for reaper=%r" % bad
            assert err and not err.startswith("409:"), (bad, err)
        assert not os.path.exists(os.path.join(root, "agents", "bot", "tb16")), \
            "非法值拒后不得落目录（校验全部先于写盘）"
        print("case16 OK: description 超长/含 NUL → 400；reaper 非法文法 → 400 且不回落")
    finally:
        _proc._self_host = orig
        shutil.rmtree(root, ignore_errors=True)


def case17_bot_form_meta():
    """op=bot_form_meta 的后端函数：不存在 → existing:null + profiles 非空 +
    commandTemplate 含占位；已登记 → existing 含 spec 现值；task 族/非法路径 → 错误。"""
    root, wd = _make_root()
    orig = _patch_self_host(root)
    try:
        # ① 不存在的 bot → 200（existing:null）
        doc, err = _proc.bot_form_meta("/agents/bot/tb17/spec.json", root=root)
        assert err is None, err
        assert doc["existing"] is None, doc
        assert doc["host"] == "testhost", doc
        assert doc["botName"] == "tb17", doc
        assert doc["defaultReaper"] == "topic/dispatcher", doc
        names = [p["name"] for p in doc["profiles"]]
        assert "executor" in names, names
        assert set(doc["profiles"][0]) >= {"name", "summary", "model", "capsCount"}, \
            doc["profiles"][0]
        assert "{profile}" in doc["commandTemplate"] and "{name}" in doc["commandTemplate"]
        assert "AGENTD_SESSION_NAME=bot/tb17" in doc["commandPreview"], doc["commandPreview"]
        # ② profile 提示 → 预览取该 profile
        doc2, err2 = _proc.bot_form_meta("/agents/bot/tb17/spec.json", root=root,
                                         profile="executor")
        assert err2 is None and "DISPATCH_PROFILE=executor" in doc2["commandPreview"], doc2
        # ③ 已登记 → existing 含 spec 现值，且预览回落现役 DISPATCH_PROFILE
        cdoc, cerr = _call("/agents/bot/tb17/spec.json", "executor", wd, root,
                           description="表单验证用", reaper="bot/zz-reaper")
        assert cerr is None and cdoc["created"] is True
        doc3, err3 = _proc.bot_form_meta("/agents/bot/tb17/spec.json", root=root)
        assert err3 is None, err3
        ex = doc3["existing"]
        assert ex and ex["name"] == "表单验证用" and ex["reaper"] == "bot/zz-reaper", ex
        assert ex["workdir"] and ex["restartPolicy"] == "auto", ex
        assert "DISPATCH_PROFILE=executor" in doc3["commandPreview"], doc3["commandPreview"]
        # ④ task 族 ∨ 非法路径 → 错误（api 层映射 400）
        for bad in ("/agents/task/t1/spec.json", "/assistant/foo.jsonl",
                    "", "/agents/bot/../evil/spec.json"):
            d, e = _proc.bot_form_meta(bad, root=root)
            assert d is None and e, (bad, d, e)
        # ⑤ 只读：bot_form_meta 不得建桥/写盘（前后 md5 一致 + 无新目录）
        md5_before = _md5(os.path.join(root, "agents", "bot", "tb17", "spec.json"))
        listing_before = sorted(os.listdir(os.path.join(root, "agents", "bot")))
        _proc.bot_form_meta("/agents/bot/tb17-new/spec.json", root=root)
        assert _md5(os.path.join(root, "agents", "bot", "tb17", "spec.json")) == md5_before
        assert sorted(os.listdir(os.path.join(root, "agents", "bot"))) == listing_before, \
            "bot_form_meta 对不存在的 bot 也不得建目录"
        print("case17 OK: bot_form_meta — 不存在 existing:null + profiles 非空 + 模板占位"
              " / 已登记含现值 / task 族与非法路径拒 / 只读零副作用")
    finally:
        _proc._self_host = orig
        shutil.rmtree(root, ignore_errors=True)


def case18_api_rejects_client_command():
    """硬约束：`op=create_bot` 传 command 参数 → 400，消息明写不由客户端提供。
    直调 rpc/api.py:interp（store 不参与 create_bot/bot_form_meta 分支 ⇒ 传 None）；
    两个 proc 函数用薄 shim 注入临时 root + 真 agentctl，不碰生产树（不改模块常量 WS）。"""
    import importlib
    _api = importlib.import_module("ext.sessiond.rpc.api")
    root, wd = _make_root()
    orig = _patch_self_host(root)
    orig_ensure = _proc.ensure_bot_registration
    orig_meta = _proc.bot_form_meta

    def _shim_ensure(sp, pf, wdir, **kw):
        return orig_ensure(sp, pf, wdir, root=root, ctl=AGENTCTL, **kw)

    def _shim_meta(sp, **kw):
        return orig_meta(sp, root=root, **kw)

    try:
        _proc.ensure_bot_registration = _shim_ensure
        _proc.bot_form_meta = _shim_meta
        sess = "/agents/bot/tb18/spec.json"
        # ① 传 command → 400 + 消息明写
        meta, body = _api.interp(None, op="create_bot", session=sess,
                                 profile="executor", workdir=wd,
                                 command="rm -rf /")
        assert meta["http_status"] == "400 Bad Request", meta
        payload = json.loads(body)
        assert payload["ok"] is False, payload
        assert "command 不由客户端提供" in payload["error"], payload
        assert "profile + 裸名生成" in payload["error"], payload
        assert not os.path.exists(os.path.join(root, "agents", "bot", "tb18")), \
            "传 command 被拒 → 零落盘"
        # ② 空串 command 当未给（不误伤）→ 正常创建
        meta2, body2 = _api.interp(None, op="create_bot", session=sess,
                                   profile="executor", workdir=wd, command="")
        assert meta2["http_status"] == "200 OK", (meta2, body2)
        p2 = json.loads(body2)
        assert p2["ok"] is True and p2["created"] is True, p2
        sp = _spec(root, "tb18")
        assert "DISPATCH_PROFILE=executor" in sp["command"], sp
        assert "rm -rf" not in sp["command"], sp
        assert sp["reaper"] == "topic/dispatcher", sp
        # ③ 新可选参数经 api 层透传（description/restartPolicy/subscribes/reaper）
        meta3, body3 = _api.interp(None, op="create_bot",
                                   session="/agents/bot/tb18b/spec.json",
                                   profile="executor", workdir=wd,
                                   description="api 层透传",
                                   restartPolicy="manual",
                                   subscribes="topic/api-x",
                                   reaper="bot/api-reaper")
        assert meta3["http_status"] == "200 OK", (meta3, body3)
        sp3 = _spec(root, "tb18b")
        assert sp3["name"] == "api 层透传" and sp3["restartPolicy"] == "manual", sp3
        assert sp3["subscribes"] == ["topic/api-x"] and sp3["reaper"] == "bot/api-reaper", sp3
        # ④ 非法 restartPolicy / reaper 经 api 层 → 400
        for kw in ({"restartPolicy": "always"}, {"reaper": "foo"}):
            meta4, body4 = _api.interp(None, op="create_bot",
                                       session="/agents/bot/tb18c/spec.json",
                                       profile="executor", workdir=wd, **kw)
            assert meta4["http_status"] == "400 Bad Request", (kw, meta4, body4)
            assert json.loads(body4)["ok"] is False
        assert not os.path.exists(os.path.join(root, "agents", "bot", "tb18c"))
        # ⑤ op=bot_form_meta：不建桥、对不存在的 bot 也 200（existing:null）
        meta5, body5 = _api.interp(None, op="bot_form_meta",
                                   session="/agents/bot/tb18-nope/spec.json")
        assert meta5["http_status"] == "200 OK", (meta5, body5)
        p5 = json.loads(body5)
        assert p5["ok"] is True and p5["existing"] is None, p5
        assert p5["profiles"], p5
        # ⑥ task 族 → 400
        meta6, body6 = _api.interp(None, op="bot_form_meta",
                                   session="/agents/task/t1/spec.json")
        assert meta6["http_status"] == "400 Bad Request", meta6
        assert json.loads(body6)["ok"] is False
        print("case18 OK: api 层 — 传 command → 400（消息明写）+ 零落盘；"
              "新可选参数透传；非法值 400；bot_form_meta 200/400 分流")
    finally:
        _proc.ensure_bot_registration = orig_ensure
        _proc.bot_form_meta = orig_meta
        _proc._self_host = orig
        shutil.rmtree(root, ignore_errors=True)


def case19_bot_name_bad_syntax():
    """bot 名走与 profile 同款的白名单（评审 gk305i S4，任务 7s42g6 补例）：
    坏名在 **web 侧**就 400，不是靠 agentctl 的 409 兜底；且零落盘（目录未创建）。
    写法与 case4（坏 profile）对称——case4 打 profile 位，本例打 bot 名位。"""
    root, wd = _make_root()
    try:
        for bad in [".hidden", "a..b"]:
            doc, err = _call("/agents/bot/%s/spec.json" % bad, "executor", wd, root)
            assert doc is None and err and not err.startswith("409:"), (bad, doc, err)
            assert not os.path.exists(os.path.join(root, "agents", "bot", bad)), bad
        print("case19 OK: bot 名含前导 . / 含 .. → 均 web 侧 400 + 目录未创建")
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    case1_happy_path()
    case2_spec_already_exists()
    case3_profile_not_found()
    case4_profile_bad_syntax()
    case5_workdir_invalid()
    case6_task_family()
    case7_name_collision_409()
    case8_idempotent()
    case9_enable_preexisting()
    case10_mismatch()
    case11_description()
    case12_reaper()
    case13_subscribes()
    case14_subscribes_non_topic()
    case15_restart_policy()
    case16_bad_description_and_reaper()
    case17_bot_form_meta()
    case18_api_rejects_client_command()
    case19_bot_name_bad_syntax()
    print("ALL PASS")
