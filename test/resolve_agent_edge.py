#!/usr/bin/env python3
"""_resolve_agent 软链逃逸 edge 用例（任务 kqhweh，ticket.gwj1xr ②）。

评审 afxgmt 复现场景：.agent 位于 ~/m/run（软链 → /data/…/run）且 sessionDir 缺省/
经软链时，sess_dir 的 realpath 逃出 ~/m 字面，旧代码 `relpath(sess_dir, WS)` 产出
`../../…` 畸形站内路径且未被拦截。修复后：run 运行时区经 run 软链锚定还原为 /run/… 站内
路径；两锚定都逃逸 → 400。

运行：cd ~/m/w && python3 test/resolve_agent_edge.py
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ext.sessiond.rpc import api           # noqa: E402
from ext.sessiond import proc as _proc     # noqa: E402


class FakeStore:
    def __init__(self, docs):
        self.docs = docs

    def read(self, p):
        if p in self.docs:
            return json.dumps(self.docs[p])
        return None


def call(docs, session):
    meta, body = api._resolve_agent(FakeStore(docs), session)
    return meta.get("http_status", "200 OK"), json.loads(body)


def case1_run_zone_default_sessiondir():
    """run 运行时区 .agent、sessionDir 缺省：站内路径应还原为 /run/…（非 ../.. 畸形）。"""
    d = "kqhweh-edge-case1"
    site_dir = "/run/temp/%s" % d
    docs = {site_dir + "/edge.agent": {"host": "nv1"}}
    try:
        status, doc = call(docs, site_dir + "/edge.agent")
        assert status.startswith("200"), "expect 200, got %s: %s" % (status, doc)
        assert doc["ok"] is True
        assert doc["session"] == site_dir + "/edge.jsonl", doc["session"]
        assert ".." not in doc["session"], doc["session"]
        print("case1 OK: run 区 sessionDir 缺省 → 站内路径 %s" % doc["session"])
    finally:
        shutil.rmtree(os.path.join(_proc.WS, "run/temp", d), ignore_errors=True)


def case2_root_escape_400():
    """sessionDir realpath 逃出根集（~/m + run 区）→ 400（既有红线）。"""
    docs = {"/x/a.agent": {"host": "nv1", "sessionDir": "~/m/../tmp/kqhweh-escape"}}
    status, doc = call(docs, "/x/a.agent")
    assert status.startswith("400"), "expect 400, got %s: %s" % (status, doc)
    assert "escapes workspace" in doc["error"], doc
    print("case2 OK: 根集逃逸 → 400 (%s)" % doc["error"])


def case3_symlink_ws_escape_400():
    """rel_dir 逃逸 → 400 分支实测：WS 自身为软链时，根集内的 sessionDir 相对 WS
    字面产出 ../.. 且 run 锚定亦逃逸（无法还原站内路径）→ 拒绝。"""
    base = tempfile.mkdtemp(prefix="kqhweh-edge-")
    real = os.path.join(base, "real-ws")
    sym = os.path.join(base, "sym-ws")
    os.makedirs(os.path.join(real, "sub"))
    os.symlink(real, sym)
    saved = (_proc.WS, _proc.WS_REAL)
    _proc.WS, _proc.WS_REAL = sym, os.path.realpath(sym)
    try:
        docs = {"/x/b.agent": {"host": "nv1", "sessionDir": os.path.join(real, "sub")}}
        status, doc = call(docs, "/x/b.agent")
        assert status.startswith("400"), "expect 400, got %s: %s" % (status, doc)
        assert "escapes the site root" in doc["error"], doc
        print("case3 OK: 软链逃出根集 → 400 (%s)" % doc["error"])
    finally:
        _proc.WS, _proc.WS_REAL = saved
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    case1_run_zone_default_sessiondir()
    case2_root_escape_400()
    case3_symlink_ws_escape_400()
    print("ALL PASS")
