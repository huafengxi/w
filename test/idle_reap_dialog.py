#!/usr/bin/env python3
"""idle_reap_dialog.py — 空闲回收（`bridge.py:_idle_scan`）对「阻塞在未答 dialog 的会话」的豁免。

钉住的不变量（一手代价 = 2026-10-03 现网实录：`idle reap: killing 20261002-032712-9667d9
(no subscribers, idle >900s)` → 用户重开聊天窗 respawn gen=2 → `sessiond.session_restarted`
清空 pending_dialogs ⇒ 那枚 ask_user **永久消失**：重挂接的基线带不出它、jsonl 只留一个没有
toolResult 的 toolCall、agent 侧结算成「无答复」）：

  ① 有未答 dialog ⇒ **不回收**（它在等用户，⛔ 不是空闲；三条空闲判据它全中）；
  ② 无 dialog 且三条判据全中 ⇒ **照常回收**（豁免不得把回收面关掉）；
  ③ dialog 已按自带 `timeout` 超时 ⇒ 被清扫 ⇒ **照常回收**（豁免不得变成永久保活）；
  ④ 有订阅者 ⇒ 不回收（既有判据，回归钉）。

自包含：夹具一律落 tempfile.mkdtemp()（自建 stub 桥接/监督员，⛔ 不经 `get_bridge`
⇒ 不拉起真实会话进程、⛔ 不启动模块级 reaper 线程、⛔ 不打现网 8080）；`_BRIDGES` 以
monkeypatch 替换、tearDown 还原，并**单向**断言生产注册表条目不得变少（并发会话新建不误红）。

运行：cd ~/m/w && python3 test/idle_reap_dialog.py
Expect: ALL PASS, exit code 0（任一断言失败 → AssertionError + 非 0 退出码）
"""
import os
import shutil
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ext.sessiond import bridge as B          # noqa: E402
from ext.sessiond import proc as P            # noqa: E402

IDLE = 1.0                                    # 阈值压到 1s：夹具 mtime/活动时间可控
AGED = 100                                    # 「已老化」的秒数（> IDLE）

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print("%s %s%s" % ("OK  " if cond else "FAIL", name,
                       (" — " + detail) if detail and not cond else ""))


class StubSup:
    """最小监督员替身：只提供 `_idle_scan` 读到的那几枚属性。
    ⛔ 不是 `proc.SocketSupervisor` 实例 ⇒ 不被 socket 分支排除。"""

    def __init__(self, session_file):
        self.session_file = session_file
        self.state = "running"
        self.proc = object()                  # 非 None ⇒ 视为进程在场
        self._cond = threading.Condition()
        self.name = "stub"
        self.reaped = 0

    def _idle_reap(self):
        self.reaped += 1


class StubBridge:
    """最小桥接替身：dialog 面用**真 Bridge 的实现**（`pending_dialog_list` /
    `_sweep_expired_dialogs_locked` / `_broadcast_dialog_resolved`），只把 sup 换成替身
    ⇒ 钉的是真实清扫语义，⛔ 不是二次实现。"""

    def __init__(self, session_file, dialogs=(), deadlines=None):
        self.session = os.path.basename(session_file)
        self.lock = threading.Lock()
        self.cond = threading.Condition(self.lock)
        self.subscribers = 0
        self.last_activity = time.monotonic() - AGED   # 无活动已老化
        self.ring = []
        self.sup = StubSup(session_file)
        self.pending_dialogs = {d["id"]: d for d in dialogs}
        self._dialog_deadline = dict(deadlines or {})
        # 借用真 Bridge 的三个方法（未绑定函数按类取，self 传本替身）
        self.pending_dialog_list = B.Bridge.pending_dialog_list.__get__(self)
        self._sweep_expired_dialogs_locked = \
            B.Bridge._sweep_expired_dialogs_locked.__get__(self)
        self._broadcast_dialog_resolved = \
            B.Bridge._broadcast_dialog_resolved.__get__(self)
        self._seq = 0

    def _append(self, obj):                     # 广播结算入环（真实现的最小依赖面）
        with self.lock:
            self._seq += 1
            eid = "stub:%d" % self._seq
            self.ring.append((self._seq, eid, obj))
            return eid, obj


def aged_file(root, name):
    """会话文件夹具：mtime 老化到阈值之外（「不写盘 = 空闲」那一判据成立）。"""
    path = os.path.join(root, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write("")
    old = time.time() - AGED
    os.utime(path, (old, old))
    return path


def scan_one(b):
    """在 monkeypatch 掉的注册表里跑一轮真实 `_idle_scan`，返回该桥接的回收次数。"""
    real_bridges, real_timeout = B._BRIDGES, B.IDLE_TIMEOUT
    B._BRIDGES = {"/stub/session.jsonl": b}
    B.IDLE_TIMEOUT = IDLE
    try:
        B._idle_scan()
    finally:
        B._BRIDGES, B.IDLE_TIMEOUT = real_bridges, real_timeout
    return b.sup.reaped


def main():
    root = tempfile.mkdtemp(prefix="idle-reap-dialog-")
    prod_before = set(B._BRIDGES.keys())       # 生产注册表快照（单向断言用）
    sdir = P.sessions_dir()
    sdir_before = set(os.listdir(sdir)) if os.path.isdir(sdir) else set()
    try:
        dialog = {"type": "extension_ui_request", "id": "dlg-live",
                  "method": "select", "title": "选 A 还是 B？",
                  "options": ["A", "B"]}

        # ① 有未答 dialog ⇒ 不回收
        b1 = StubBridge(aged_file(root, "s1.jsonl"), dialogs=[dialog])
        check("① 未答 dialog 在场 ⇒ 不回收", scan_one(b1) == 0,
              "reaped=%d" % b1.sup.reaped)

        # ② 无 dialog + 三判据全中 ⇒ 照常回收
        b2 = StubBridge(aged_file(root, "s2.jsonl"))
        check("② 无 dialog 且空闲 ⇒ 照常回收", scan_one(b2) == 1,
              "reaped=%d" % b2.sup.reaped)

        # ③ dialog 自带 timeout 已过期 ⇒ 清扫 + 结算广播 ⇒ 照常回收（豁免不永久保活）
        b3 = StubBridge(aged_file(root, "s3.jsonl"),
                        dialogs=[dict(dialog, id="dlg-stale", timeout=10)],
                        deadlines={"dlg-stale": time.monotonic() - 1})
        reaped3 = scan_one(b3)
        settled = [o for _s, _e, o in b3.ring
                   if isinstance(o, dict)
                   and o.get("type") == "sessiond.dialog_resolved"]
        check("③ 超时 dialog 被清扫 ⇒ 照常回收", reaped3 == 1,
              "reaped=%d pending=%s" % (reaped3, sorted(b3.pending_dialogs)))
        check("③b 清扫同批广播结算（多 tab 关框同步）",
              len(settled) == 1 and settled[0].get("id") == "dlg-stale"
              and settled[0].get("timeout") is True,
              "ring=%s" % (settled,))

        # ④ 有订阅者 ⇒ 不回收（既有判据回归钉；带 dialog 与否都一样）
        b4 = StubBridge(aged_file(root, "s4.jsonl"))
        b4.subscribers = 1
        check("④ 有订阅者 ⇒ 不回收", scan_one(b4) == 0,
              "reaped=%d" % b4.sup.reaped)

        # ⑤ 未答 dialog 的豁免不依赖 mtime 新鲜度（写盘老化照样豁免）
        b5 = StubBridge(aged_file(root, "s5.jsonl"), dialogs=[dialog])
        b5.last_activity = time.monotonic() - AGED
        check("⑤ 豁免优先于 mtime/活动老化判据", scan_one(b5) == 0,
              "reaped=%d" % b5.sup.reaped)

        # 生产面零触碰（单向：注册表条目不得变少 ⇒ 并发新建不误红）
        prod_after = set(B._BRIDGES.keys())
        check("⑥ 生产 _BRIDGES 零触碰（条目未变少）",
              prod_before <= prod_after,
              "before=%d after=%d" % (len(prod_before), len(prod_after)))
        sdir_after = set(os.listdir(sdir)) if os.path.isdir(sdir) else set()
        check("⑦ 生产会话目录零新增/零消失", sdir_before == sdir_after,
              "new=%s gone=%s" % (sorted(sdir_after - sdir_before),
                                   sorted(sdir_before - sdir_after)))
    finally:
        shutil.rmtree(root, ignore_errors=True)

    failed = [n for n, ok, _d in RESULTS if not ok]
    print("")
    if failed:
        print("FAILED: %s" % ", ".join(failed))
        return 1
    print("ALL PASS (%d checks)" % len(RESULTS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
