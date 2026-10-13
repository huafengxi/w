#!/usr/bin/env python3
"""dormant_send_revive.py — 懒态（dormant）会话的**上行首条命令**必须等复活就位再写。

钉住的不变量（一手代价 = 2026-10-13 现网实录，web.log）：
  会话 lofk 于 00:16:31 被 `_idle_scan` 回收转 dormant；09:37:41,789 用户在聊天窗敲下第一条
  消息 → `op=cmd {"type":"prompt"}` → ,796 **RESP 403**（7ms）→ ,811 才 `spawned gen=15`。
  即：`Bridge.send` 只 `ensure_started()`（异步起监督线程）就立刻写 stdin，此刻 `proc is None`
  ⇒ 命令被丢，前端输入框已清空（只留一行 sysLine err）⇒ 用户视角「第一条消息凭空消失」。

  ① dormant + 复活有延迟 ⇒ `send` **等就位**、命令落到 stdin（返回 None）；
  ② 复活永不就位（超时）⇒ 返回**错误串**（不静默丢：前端有 sysLine/队列面板标错可依据）；
  ③ 已在跑 ⇒ 直通、**不额外等待**（常规路径零开销，wait_ready 立即 True）；
  ④ 熔断（disabled）⇒ 立即错误串，不空等满超时窗。

自包含：夹具 = 自建 stub 桥接/监督员 + `B.Bridge.send.__get__(stub)` 借用**真实现**
（⛔ 不是二次实现）；⛔ 不经 `get_bridge` ⇒ 不拉起真实会话进程、⛔ 不启动模块级 reaper
线程、⛔ 不写 ~/m/run、⛔ 不打现网 8080。

运行：cd ~/m/w && python3 test/dormant_send_revive.py
Expect: ALL PASS, exit code 0（任一断言失败 → 非 0 退出码）
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ext.sessiond import bridge as B          # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print("%s %s%s" % ("OK  " if cond else "FAIL", name,
                       (" — " + detail) if detail and not cond else ""))


class StubSup:
    """最小监督员替身：接口面对齐 `proc.Supervisor` 被 `Bridge.send` 用到的三枚
    （`ensure_started` / `wait_ready` / `send`）+ `state`。

    `revive_after` = `ensure_started` 后多少秒翻成 running（None = 永不就位）；
    就位前 `send` 一律 False（复刻真实现的「proc is None ⇒ 写不进」）。"""

    def __init__(self, revive_after=0.0, state="dormant"):
        self.state = state
        self.proc = None
        self.disabled = state == "disabled"
        self.started = 0
        self.writes = []
        self.revive_after = revive_after
        self._cond = threading.Condition()
        self._t0 = None

    def ensure_started(self):
        self.started += 1
        with self._cond:
            if self._t0 is None:
                self._t0 = time.monotonic()

    def _settle(self):
        """按 revive_after 翻态（模拟监督线程 spawn 完成的时刻）。
        熔断（disabled）⇒ 监督循环已退出、不经 reload 不复活（同 proc.py 口径）。"""
        if self.disabled or self.revive_after is None or self._t0 is None:
            return
        if time.monotonic() - self._t0 >= self.revive_after:
            self.proc = object()
            self.state = "running"

    def wait_ready(self, timeout=1.0):
        # 缺省窗压到 1s（真实现 = proc.py 的 25s）：只为夹具快，钉的是「等/不等」而非窗口长度。
        deadline = time.monotonic() + timeout
        with self._cond:
            while True:
                self._settle()
                if self.state == "running" and self.proc is not None:
                    return True
                if self.disabled:
                    return False
                remain = deadline - time.monotonic()
                if remain <= 0:
                    return False
                self._cond.wait(min(remain, 0.02))

    def send(self, obj):
        if self.proc is None:
            return False
        self.writes.append(obj)
        return True


class StubBridge:
    """最小桥接替身：`send` 用**真 Bridge 的实现**（未绑定函数按类取，self 传本替身）。"""

    def __init__(self, sup):
        self.session = "stub"
        self.lock = threading.Lock()
        self.cond = threading.Condition(self.lock)
        self.subscribers = 0
        self.last_activity = time.monotonic()
        self.ring = []
        self.sup = sup
        self.pending_dialogs = {}
        self._dialog_deadline = {}
        self._seq = 0
        self.send = B.Bridge.send.__get__(self)
        self.note_activity = B.Bridge.note_activity.__get__(self)

    def _append(self, obj):
        with self.lock:
            self._seq += 1
            self.ring.append((self._seq, "stub:%d" % self._seq, obj))


def main():
    # ① dormant + 复活有延迟（0.3s）⇒ 等就位、命令落到 stdin
    b1 = StubBridge(StubSup(revive_after=0.3))
    t0 = time.monotonic()
    err1 = b1.send({"type": "prompt", "message": "第一条", "id": "p1"})
    dt = time.monotonic() - t0
    check("① dormant 首条命令等复活就位后落 stdin（不丢）",
          err1 is None and len(b1.sup.writes) == 1
          and b1.sup.writes[0]["message"] == "第一条",
          "err=%r writes=%s" % (err1, b1.sup.writes))
    check("①b 确实等过 spawn 延迟（≥0.3s）且未空等满超时窗",
          0.3 <= dt < 5.0, "dt=%.3fs" % dt)
    check("①c ensure_started 被调用（懒态复活入口）", b1.sup.started == 1,
          "started=%d" % b1.sup.started)

    # ② 复活永不就位 ⇒ 返回错误串（⛔ 不静默丢）
    b2 = StubBridge(StubSup(revive_after=None))
    err2 = b2.send({"type": "prompt", "message": "x", "id": "p2"})
    check("② 永不就位 ⇒ 错误串带 state（前端可标错，不静默丢）",
          isinstance(err2, str) and "not ready" in err2
          and "state=dormant" in err2 and b2.sup.writes == [],
          "err=%r writes=%s" % (err2, b2.sup.writes))

    # ③ 已在跑 ⇒ 直通、零额外等待（常规路径不引入延迟）
    b3 = StubBridge(StubSup(state="running"))
    b3.sup.proc = object()
    t0 = time.monotonic()
    err3 = b3.send({"type": "prompt", "message": "热路径", "id": "p3"})
    dt3 = time.monotonic() - t0
    check("③ 已在跑 ⇒ 直通且零额外等待",
          err3 is None and len(b3.sup.writes) == 1 and dt3 < 0.5,
          "err=%r dt=%.3fs" % (err3, dt3))

    # ④ 熔断（disabled）⇒ 立即错误串，不空等满窗
    b4 = StubBridge(StubSup(state="disabled"))
    t0 = time.monotonic()
    err4 = b4.send({"type": "prompt", "message": "x", "id": "p4"})
    dt4 = time.monotonic() - t0
    check("④ 熔断 ⇒ 立即错误串（不空等 25s 窗）",
          isinstance(err4, str) and "state=disabled" in err4 and dt4 < 1.0,
          "err=%r dt=%.3fs" % (err4, dt4))

    failed = [n for n, ok, _d in RESULTS if not ok]
    print("")
    if failed:
        print("FAILED: %s" % ", ".join(failed))
        return 1
    print("ALL PASS (%d checks)" % len(RESULTS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
