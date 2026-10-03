# -*- type=script -*-
"""dyn-rows.py — `@dynamic` 指令行的 demo 端点（**非 dash** 用例，任务 z293ql）。

`ext/frame/view/iframe.html` 的 `@dynamic <path>` 是通用增强：任何 itab/iflow 页都能把
一行指令换成一个端点吐出的若干行。本文件就是那个端点的最小形态 —— 返回
`名字 URL [k=v …]` 格式的纯文本（一行一枚，`#` 起首的行与认不出的行由前端过滤）。

配套页面 = [/w/demo/dyn.itab](/w/demo/dyn.itab)：指令行放在**中间**，用来证明「就地
splice」而不是「一律追加到末尾」。生产用例 = `/dash/session-ctl.py` 的 HTTP interp 面（活跃指挥
中心会话，数据源 = `ext/sessiond/rpc/api.py` 的 `op=session_tabs`；同一文件的 CLI 面是工作区页
每节 widget 的只读列表）。

语义（与设计稿 §4.3 一致）：frame 加载时 fetch 一次 = **快照**，无轮询、无定时刷新；
本端点失败 ⇒ 该指令即刻展开零行；超时上界 10 s ⇒ 期间整表（含静态行）延迟渲染。
"""


def interp(store, **kw):
    rows = [
        "dyn-readme /w/README.md",
        "dyn-design /w/design.md",
    ]
    return dict(type='text/plain'), "\n".join(rows) + "\n"
