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


def _call(session_path, profile, workdir, root, ctl=None):
    if ctl is None:
        ctl = AGENTCTL
    return _proc.ensure_bot_registration(session_path, profile, workdir,
                                         root=root, ctl=ctl)


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
        # 核 enable.json
        enable_path = os.path.join(root, "agents", "bot", "testbot1", "enable.json")
        assert os.path.exists(enable_path), "enable.json not created"
        with open(enable_path) as f:
            en = json.load(f)
        assert en["by"] == "web/testhost", en
        assert "created via 8080 chat URL" in en.get("note", ""), en
        print("case1 OK: happy path — spec.json 六字段 + enable.json by/note 均正确")
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
        doc2, err2 = _call("/agents/bot/testbot2/spec.json", "executor", wd, root)
        assert err2 is None, "unexpected error: %s" % err2
        assert doc2["created"] is False
        assert doc2["effective"]["profile"] == "executor"
        md5_after = _md5(spec_path)
        assert md5_before == md5_after, "spec.json changed! %s != %s" % (md5_before, md5_after)
        print("case2 OK: spec 已在场 → created:false，md5 前后一致")
    finally:
        _proc._self_host = orig
        shutil.rmtree(root, ignore_errors=True)


def case3_profile_not_found():
    """profile 不在 bots/profiles/ → 400 且消息含现场枚举的可用清单。"""
    root, wd = _make_root()
    doc, err = _call("/agents/bot/testbot3/spec.json", "nonexistent", wd, root)
    assert doc is None
    assert err is not None
    assert "nonexistent" in err
    assert "executor" in err, "可用清单未含 executor: %s" % err
    assert not err.startswith("409:")
    print("case3 OK: profile 不存在 → 400 + 可用清单含 executor")


def case4_profile_bad_syntax():
    """profile 含 ; 或 / 或前导 . → 400。"""
    root, wd = _make_root()
    for bad in ["exec;rm", "exec/utor", ".hidden"]:
        doc, err = _call("/agents/bot/testbot4/spec.json", bad, wd, root)
        assert doc is None, "expected error for profile=%r" % bad
        assert err is not None
        assert not err.startswith("409:")
    print("case4 OK: profile 含 ; / 前导. → 均 400")


def case5_workdir_invalid():
    """workdir 不存在 ∨ 相对路径 ∨ realpath 落在 agents/ 内 → 400。"""
    root, wd = _make_root()
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


def case6_task_family():
    """task 族路径 → 400。"""
    root, wd = _make_root()
    doc, err = _call("/agents/task/mytask/spec.json", "executor", wd, root)
    assert doc is None
    assert err is not None
    assert "bot 族" in err or "task" in err
    assert not err.startswith("409:")
    print("case6 OK: task 族路径 → 400 + 指引 bot 族")


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
        print("case7 OK: 同名 task/ 在场 → 409 + stderr 原文")
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
    print("ALL PASS")
