# ext/markdown — Markdown 视图（text/md）

把 `.md` 文件渲染成页面：标准 GFM markdown（marked），外加 **cmd interp**
扩展语法——行内命令 widget，命令在服务端执行、结果以 markdown 流式内嵌到页面。

## 视图入口

- `.md` 扩展名 → `text/md` → 本视图（`mime.frag` / `vmap.frag`）。
- `?v=text/md` 可对任意文件强制本视图（如 `.py` 文件按 md 渲染）。

链接书写约定（绝对路径、`.md` 无需 `?v=` 后缀等）见根 `README.md`
「shell 路由」条目，不复述。

## 标准渲染

+ marked GFM（表格、嵌套列表）。
+ **下划线转义**：代码块/行内代码**之外**的 `_` 一律转义为字面下划线——
  即 `_` 永远不会触发强调（斜体），`snake_case`、文件名等可放心直写；
  需要强调用 `*...*`。
+ 列表项按紧凑列表渲染（忽略空行带来的松散列表间距）。
+ 文档第一个 `# 一级标题` 作为页面标题。

## 扩展语法：cmd interp

### `${命令}` → 行内命令 widget

正文中 `${...}`（花括号内为命令，不含 `}`）在渲染处生成一个 widget：

+ **run**：执行命令。输出按行流式到达：
  - **stdout** → 累积后按 markdown 渲染，内嵌展示在 widget 结果区（边跑边更新）；
  - **stderr** → 进 widget 自带的 log 控制台（**log** 按钮切换显隐）。
+ **refresh**：重跑，但在参数末尾附加 `_fresh_`（调用约定：执行侧据此强制
  重算、跳过缓存——如 report 树 ido 的 result cache）。
+ **log**：切换本 widget 的 stderr 控制台。

### `#!cmd-interp <path>` → 指定 rpc 解释器端点

文档首行写 `#!cmd-interp <path>`，指定所有 `${...}` 命令提交到哪个服务端
rpc 端点；缺省 `/w/ext/shell/rpc/sh.py`。渲染时该指令行本身不显示。

缺省端点（sh.py）的执行语义：命令以 `bash -c` 在服务端运行，`src` 环境变量
指向文档路径；缺省派发函数把命令当作 report 树 ido 的 `<sub> [args...]`
派发（目标由文档路径推导），派发过程提示走 stderr（log 控制台可见），
ido 的 markdown 结果走 stdout（结果区）。

**流式协议要点**（理解输出行为所需）：端点以行帧流式返回，每行带一个前导
标签字节——`'1'` = stdout（进结果区，按 markdown 渲染）、`'2'` = stderr
（进 log 控制台）；命令结束流即关闭。

### 顶栏按钮

页面含至少一个 `${...}` widget 时，顶栏显示：

+ **run-all** / **refresh-all**：按文档顺序逐个执行/刷新所有命令
  （串行，一个跑完再跑下一个）；
+ **log**：统一切换所有 widget 的 log 控制台。

无 widget 的页面这三个按钮不显示。

### ⚠️ 安全提示

`${...}` 的命令**在服务端执行，权限 = w 服务进程**（可读改服务账号的一切
文件、执行任意程序）。不要对不可信来源的 md 文档渲染本视图。

## 页面交互

+ 顶栏 **edit**：页内编辑器，直接读写源文件（保存后即时重渲染）。
+ 链接缺省**新 tab 打开**（`<base target="_blank">`）。
+ 链接带 `?v=iframe`：点击不跳转，改为在链接下方就地内嵌一个 iframe 预览，
  再点收起；可同时展开多个（该参数纯客户端消费，不到达服务端）。
+ 本视图被嵌进外层容器（iframe 内）时，顶栏自动隐藏。

## 参数

+ `?t=<任意值>`：缓存破坏（客户端轮询取源文件时自动追加，人工访问一般不用管）。
+ `?refresh=N`：每 N 秒轮询源文件，内容有变化才重渲染（保持滚动位置；
  tab 隐藏期间暂停，回前台立即补一次）。不带或 ≤0 则只加载一次。
+ 容器层面的刷新（如 itab 的行级 `refresh=N` 属性）不属本视图，见
  `../frame/view/iframe.html`。

## 最小示例

```md
#!cmd-interp /w/ext/shell/rpc/sh.py

# Demo 页

当前时间（点 run 后内嵌显示，流式更新）：
${date}

磁盘概览（stdout 按 markdown 渲染，stderr 进 log 控制台）：
${df -h | head -3}
```
