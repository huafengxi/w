# sessiond 架构与 API 文档

由多个迭代任务（0830-1056-j6er 等）累积，0830-1149-o65s 简化去重。本文以当前代码为准，代码位置引用 `文件:函数名`（不引用行号）。

---

## 1. 总览

sessiond 是 web 服务（8080，全局 Basic Auth，凭据 `~/.auth/passwd`）内嵌的**路径驱动聊天系统**：

- **会话 = 站内任意 `.jsonl` 路径**。`<path>/<name>.jsonl?v=chat` 打开聊天窗；路由键 = 完整路径（不同目录的同名文件 = 不同会话，可并存）。会话工作目录（cwd）= 该 jsonl 所在目录（决定 pi 加载哪个工作区的 AGENTS/扩展，预期行为，不特判）。
- **每会话 = 一个受监督的 `pi --mode rpc` 子进程**。由 `proc.py:Supervisor` 在 web 进程内直接监督（stdin/stdout 管道，进程内直连，无 unix socket），崩溃自动退避重启并从该 jsonl resume。
- **bridge 事件环**：`bridge.py:Bridge` 每会话路径一个，持有有界事件环（默认 2000 条），对多个 SSE 订阅者多播，并代理 `get_entries` 基线。
- **前端单文件页**：`view/index.html`（无构建、仅外部依赖 `marked.min.js`），从 `location.pathname` 解析会话路径。

### 视图路由（服务端）

| 文件 | 规则 |
|---|---|
| `vmap.frag` | `?v=chat` → `/sessiond/view/index.html`；另注册 `application/x-sessiond-jsonl` 专用 mime 同样指向该页 |
| `mime.frag` | `.jsonl` → `application/x-sessiond-jsonl`，即**直接打开任意 `.jsonl`（无 `?v=`）即聊天窗**；`?v=` 显式参数仍可覆盖（如 `?v=code` 看原文） |

vmap 翻译是服务端行为，浏览器 `location.pathname` 保持原始 `.jsonl` 路径。

### 数据流

```
                     ┌────────────────────────────── 8080 (Basic Auth) ─────────────────────────────┐
                     │                                                                              │
   浏览器             │  view/index.html（单文件前端：渲染 + 自愈 + 上行）                             │
   ┌──────────┐      │        │                                            ▲                        │
   │ 用户输入  │──────┼──────▶ │  fetch POST /sessiond/rpc/api.py           │                        │
   │ (prompt/  │      │        │    op=status/attach/cmd/                   │ SSE 帧                 │
   │  斜杠…)   │      │        │       reload/clear                         │ id:+data:              │
   └──────────┘      │        ▼                                            │ (`: ping` 保活)         │
        ▲            │   ┌──────────┐   send(cmd)/get_entries()   ┌────────────────┐                │
        │            │   │ rpc/     │────────────────────────────▶│                │                │
        │            │   │ api.py   │                             │  bridge.py     │                │
        │ 渲染事件    │   └──────────┘                             │  Bridge        │                │
        │            │   ┌──────────┐   resolve_since/iter_events │  (事件环+订阅)  │                │
        └────────────┼───│ rpc/     │◀────────────────────────────│                │                │
                     │   │ stream.py│   GET ?session=&since=      │                │                │
                     │   └──────────┘                             │                │                │
                     │                                            └───────▲────────┘                │
                     │                                                    │ on_event(obj) 入环      │
                     │                                            ┌───────┴────────┐                │
                     │                                            │  proc.py       │ stdin: JSON 行 │
                     │                                            │  Supervisor    │───────────────▶ │
                     │                                            │  (监督/退避/   │ ◀────────────── │
                     │                                            │   熔断/重启)   │ stdout: JSON 行 │
                     │                                            └───────▲────────┘                │
                     └────────────────────────────────────────────────────┼─────────────────────────┘
                                                                          │
                                                          ┌───────────────┴───────────────┐
                                                          │  `pi --mode rpc` 子进程         │
                                                          │  --session <jsonl> --name <名> │
                                                          │  cwd = jsonl 所在目录           │
                                                          │        │ 读写持久体             │
                                                          │        ▼                        │
                                                          │  <path>/<name>.jsonl            │
                                                          └───────────────────────────────┘
```

- **下行**：pi stdout JSON 行 → `proc.py:_read_stdout` → `bridge.py:_ingest` 入环 → `stream.py` SSE 帧 → 前端 `drainFrames` → 渲染。
- **基线**：前端 `op=attach` → `bridge.py:get_entries` 发 `{type:"get_entries"}` 并等配对响应（完整历史来自 jsonl，而非事件环）。
- **上行**：前端 `op=cmd` → `bridge.py:send` → 监督员单写者串行写入子进程 stdin。

---

## 2. 会话监督员（proc.py）

`proc.py:Supervisor` 每会话路径一个守护线程，生命周期与状态机：

| 项 | 值/语义 |
|---|---|
| 状态 `state` | `idle → starting → running`；进程退出瞬间 `restarting`；熔断 `disabled` |
| 世代 `gen` | 每次 spawn +1；前端据此做基线自愈 |
| 崩溃退避 | `2^n` 秒，上限 `BACKOFF_MAX=30s`；`sessiond.session_restarting{delay}` 广播 |
| 熔断 | `FAIL_WINDOW=300s` 窗口内连续崩溃超 `FAIL_LIMIT=6` → `disabled`，停监督，广播 `sessiond.session_disabled`；仅 `reload/clear` 可解除 |
| 稳定期重置 | 上轮存活超 `STABLE_RESET=120s` → 崩溃计数与 `restarts` 清零 |
| 单行上限 | `LINE_LIMIT=16MiB`，超限丢弃该行 |
| 进程参数 | `pi --mode rpc --session <jsonl 绝对路径> --name <basename 去 .jsonl>`，`start_new_session=True`（web 被整组杀时不连带杀会话），stderr 继承 web（并入 `run/logs/web.log`） |
| 环境清洗 | `proc.py:clean_env` 剥除调度/任务身份变量（`AGENTD_TASK`/`DISPATCH_TASK_*`/`PI_SESSION*` 等），防会话归属投毒 |
| 旧宿主识别 | 零文件：spawn 注入 environ 标记 `SESSIOND_SESSION_FILE=<jsonl>`（`proc.py:HOST_MARKER`）；`proc.py:find_hosts` 扫 `/proc/*/environ` 精确匹配（pi 会 setproctitle 重写 argv，cmdline 匹配不可行） |
| 双宿主清场 | `proc.py:_clear_stale_proc`（首次拉起时，主要面向 web 重启/跨进程场景）：等旧宿主自然退出（stdin EOF，宽限 `ORPHAN_GRACE=15s`）→ SIGTERM → 3s → SIGKILL |
| 就位等待 | `proc.py:wait_ready` 默认 25s（覆盖清场 15s + SIGTERM 3s + spawn 余量） |

**生命周期帧**（监督员经 `on_event` 广播，入事件环下发）：
`sessiond.session_restarting{session, delay[, reload]}`、`sessiond.session_restarted{session, gen, pid}`、`sessiond.session_disabled{session}`。

**reload / clear**（`proc.py:reload` / `proc.py:clear`，共用 `_kill_and_restart`）：

| op | 语义 | 顺序保证 |
|---|---|---|
| `reload` | 杀会话进程（SIGTERM→3s→SIGKILL）→ 立即从该 jsonl resume 重拉；不计崩溃、不排退避、解除熔断 | — |
| `clear` | 保留会话路径、清空全部内容 | ① 杀进程 → ② 监督循环内、旧进程死透后**删除**（非截断，截断空文件会让 pi 懒落盘 `openSync(wx)` EEXIST）jsonl → ③ 立即重拉（空起点，文件不存在时 pi 自建） |

**路径安全红线**（`proc.py:resolve_session_path`）：必须 `/` 开头的站内路径、`.jsonl` 后缀；normpath 折叠 `..`/`.` 后与 `~/m` 拼接，再 `realpath` 前缀校验（白名单根 = `~/m` 与 `~/m/run`），双保险防符号链接逃逸；非法抛 `ValueError`（调用方回 400）。注册表键 = 解析后绝对路径。

---

## 3. 桥接层（bridge.py）

`bridge.py:get_bridge(session_path)`：按路径懒创建 `Bridge` 并拉起监督员；注册表 `_BRIDGES`，键 = 解析后绝对路径。

### 事件环与多播

| 项 | 值 |
|---|---|
| 环容量 `RING_MAX` | 2000（env `SESSIOND_BRIDGE_RING` 可覆盖），`deque` 超限淘汰最旧 |
| 事件 ID | 不透明 `<ns>:<seq>`；`ns` = 桥接建立时 8 位随机，`seq` 单调递增（重启不换桥，`ns` 不变） |
| 入环 | `bridge.py:_ingest`：pi stdout 原始行 + 监督员生命周期帧统一入环 |
| get_entries 响应瘦身 | 响应行照常入环，但 `entries` 列表替换为 `"<N entries omitted>"`（完整响应只交给等待者），避免巨型 SSE 负载 |
| 挂起 dialog 登记 | `extension_ui_request` 且 `method ∈ {select, confirm, input, editor}` → 记入 `pending_dialogs`，随基线响应 `pendingDialogs` 下发（请求体不在会话 entries 里，前端刷新/重连后靠它重建对话框）；请求带 `timeout` 字段（毫秒）者另记单调 deadline（`_dialog_deadline`），供超时清扫（见 §11.4） |

### 游标与订阅

- `bridge.py:resolve_since(since_eid)` → `(start_seq, gap)`：空 since → `(0, False)`；环内命中 → `(seq, False)`；**不在环内（已淘汰/未知）→ `(最旧可用位, True)`**（gap 由 `stream.py` 发 `webgw.gap` 帧）。
- `bridge.py:iter_events(start_seq)`：先补发 `start_seq` 之后的缓冲，再实时跟随；`cond.wait(timeout=15)` 超时 → yield `None` → SSE `: ping` 保活。

### 上行与基线

- `bridge.py:send(cmd_obj)`：命令拦截面 `BLOCKED_COMMANDS = {switch_session, set_session_name}`（会改绑 session_file/会话名，使监督登记失准）→ 返回错误串；`extension_ui_response` 校验必须有对应悬空 `pending_dialogs` id，否则拒绝并返回错误串，放行且转发成功后广播 `sessiond.dialog_resolved` 结算帧（§11.4 发射时机表）；`abort` 特殊处理：把所有悬空 dialog 以 `cancelled` 代答（pi rpc dialog 是裸 Promise，`session.abort()` 触不到，不代答则回合永久阻塞），先发 abort 再串行发代答，并广播 `cancelled:true` 结算帧。应答/代答/`abort` 前均顺带清扫超时 dialog。
- `bridge.py:get_entries(timeout=ENTRIES_TIMEOUT=20s)`：`ensure_started` + `wait_ready` → 发 `{type:"get_entries", id:"wbge-…"}` → 等配对响应行（响应行的事件 ID = `watermark` 水位）。超时/不就位/不可写均返回 `{ok: False, error}`。

---

## 4. 后端 API：控制面 `rpc/api.py`

端点 `POST /sessiond/rpc/api.py`，`application/x-www-form-urlencoded`。公共参数 `session`（必填，站内 `.jsonl` 路径）。鉴权由 8080 全局 BasicAuth 承担；路径非法/缺失 → 400。

| op | 参数 | 成功响应（关键字段） | 语义与副作用 | 错误 |
|---|---|---|---|---|
| `status` | — | `{ok, session, state, pid, gen, restarts, disabled, session_file, cwd}` | 只读查询监督员状态（`proc.py:status_doc`），无副作用 | 400 session 缺失/非法 |
| `attach` | — | `{ok, session, gen, entries, pendingDialogs, leafId, watermark}` | **全量基线**：`get_entries()`；顺带下发悬空 dialog 快照（`pendingDialogs`） | 504 `get_entries` 超时/不就位；502 pi 拒绝（`success:false`） |
| `cmd` | `cmd`（JSON 字符串，须为含 `type` 的对象） | `{ok: true}` | 上行单条 pi 命令，经 `bridge.py:send`；被拦截/无悬空 dialog/stdin 不可写 → 403 | 400 JSON 解析失败或非对象/无 type；403 `send` 返回错误 |
| `reload` | — | `{ok, session, gen, pid}` | 杀进程 + resume 重拉，语义见 §2 reload 表（干净进程 = extension 全新加载）；等监督循环重拉完成（20s 超时） | 502 重拉超时等失败 |
| `clear` | — | `{ok, session, gen, pid}` | 保留路径清空全部内容，语义见 §2 clear 表；删除失败 → 会话虽已重拉但报 `ok:false`（错误信息含原因） | 502 失败（含删文件失败） |
| 未知 | — | — | — | 400 `unknown op` |

attach 响应形状由 `api.py:_baseline_doc` 整理；`watermark` = get_entries 响应行的事件环 ID，前端用它作为续流游标。

## 5. 后端 API：事件流 `rpc/stream.py`（SSE）

端点 `GET /sessiond/rpc/stream.py?session=<路径>[&since=<eid>]`。

| 项 | 语义 |
|---|---|
| Content-Type | `text/event-stream`；**不设 Content-Length** → wsgiserver 走 HTTP/1.1 chunked 分块传输 |
| 订阅上限 | `bridge.py:MAX_STREAMS_PER_BRIDGE=5`（env `SESSIOND_BRIDGE_MAXSTREAMS`），查上限 + 占位在同一 `cond` 锁内原子完成（`stream.py:interp`）；超限 → **429 `stream limit (5) reached for session …`**（防多页签/忘关页签耗尽 wsgiserver 线程池波及 8080 其它服务）；生成器 `finally` 中退订 |
| 开始帧 | `: stream open\n\n`（注释，非事件） |
| 正常帧 | `id: <ns>:<seq>\ndata: <json>\n\n`；`id` = 不透明事件游标 |
| 保活 | 环内 15s 无新事件 → `: ping\n\n` 注释（SSE 注释不更新 Last-Event-ID，不触发前端逻辑） |
| gap 帧 | `since` 不在环内（已淘汰/未知）→ 先发一帧 `data: {"type":"webgw.gap","gap":true,"since":…,"oldest":…,"session":…}`（**无 `id:` 行**），再从最旧可用位续流；前端据此走全量基线自愈 |
| 空 since | 从 seq 0 起播（环内现存全部事件），不发 gap |
| 参数错误 | 400：`session` 缺失或路径非法 |

---

## 6. 上行命令全集（op=cmd 的 `cmd.type`）

前端经 `op=cmd` 实际发送的命令类型（`index.html:handleSlash`、`index.html:sendPrompt`、`index.html:replyDialog`）：

| type | 发起入口 | 附加字段 | 语义 |
|---|---|---|---|
| `prompt` | Enter 且 **`turnActive == false`**（agent 空闲） | `message`, `id`, 可选 `images`（`[{type:"image", data:<b64>, mimeType}]`） | 新回合提示词；被拒语义见下 |
| `follow_up` | Enter 且 **`turnActive == true`**（流式中自动排队）；及 prompt 被 "already processing" 拒后的自动转换 | 同 `prompt`（转换时仅重发文本，图片不可恢复） | 排队至 agent 完全停止后才投递；送达信号 = 对应 user `message_start`（文本匹配，幂等转正） |
| `abort` | `/abort` | `id` | 立即执行、不进队列；后端对悬空 dialog 同步发 `cancelled` 代答并广播结算（见 `bridge.py:send`、§11.4），前端同步关闭本地 dialog |
| `extension_ui_response` | dialog 应答/取消 | `id` + `{value}` / `{confirmed}` / `{cancelled}` | 应答悬空 dialog；无对应悬空 id → 403 |
| `compact` | `/compact [自定义指令]` | `customInstructions?` | 请求压缩 |
| `set_model` | `/model <provider>/<modelId>` | `provider`, `modelId` | 切换模型（provider 缺省取当前 `curProvider`） |
| `set_thinking_level` | `/thinking <级别>` | `level` ∈ `off\|minimal\|low\|medium\|high\|xhigh\|max` | 调整 thinking 级别 |
| `export_html` | `/export [输出路径]` | `outputPath?` | 导出 HTML（默认会话工作目录） |
| `fork` | `/fork [entryId]` | `entryId`（缺省 = 当前 `curLeafId`） | 从某 entry 分叉 |

协议层面另存在但**本前端不发送**的类型：

| type | 说明 |
|---|---|
| `steer` | pi rpc 支持「当前工具完成后、下一次模型调用前投递」的插队命令；本前端无发送入口，但 `queue_update.steering` 队列会在 QUEUED 面板以蓝色 `steer` 徽标展示（如其它客户端入队） |
| `switch_session` / `set_session_name` | **后端硬拦截**（`bridge.py:BLOCKED_COMMANDS`），发送返回 403 |

**"already processing" 拒绝语义**：前端以 `turnActive` 判空闲发 `prompt`，与 agent 启动存在同瞬竞态；pi 对处理中的 `prompt` 回 `{success:false, error:"already processing"}`。前端 `index.html:handleCmdResponse` 识别后不标红、不标 rejected，自动转 `follow_up` 重发入队（flash「Agent busy → queued as follow-up」）。其余拒绝 → flash 红色并置面板条目 `rejected` 态。

**回执**：pi rpc 对 prompt/steer/follow_up 均有 `{type:"response", id, command, success}` 回执，经事件环回传，前端按 `id` 匹配 `pendingSends`。

---

## 7. 前端界面清单（view/index.html）

### 7.1 可见表面

| 元素 | 用途 | 显隐触发 |
|---|---|---|
| `#stream` 主消息流 | 全部对话渲染区 | 常驻；`attach`/全量自愈时 `innerHTML` 清空重建 |
| 用户消息行 `.role-user` | 右对齐浅蓝底，文本 + 图片缩略图（点击经 blob URL 新窗看原图） | 由 user `message_start`（流式）或基线 `renderEntryMessage` 渲染 |
| assistant 消息块 `.role-assistant.md` | markdown 渲染（marked + 白名单消毒 + 极简语法高亮），流式 150ms 节流、`message_end` 收尾 | assistant `message_start` / 基线回放 |
| 过程组 `.process-group`（`<details>`） | **一个来回（turn）一组**：收该回合所有 assistant 消息的 thinking + 工具块；header「Process details · N process blocks · M tool calls」+ 旋转箭头；最终回答文本留组外 | 懒创建（首个过程块到达）；实时处理中 `open`；**`agent_end`/`agent_settled` 自动折叠**；基线/自愈回放一律默认折叠；点 header 手动切换；无过程块的纯文本回合不出现 |
| thinking 块 `.thinking-block`（`<details>`） | 折叠盒，标题固定「Thinking」（无字数），内容为斜体灰底 `<pre>`；基线（`content.thinking`）与流式（`thinking_delta` 累积）共用同一 DOM | 默认折叠；随过程组显隐 |
| 工具块 `.tool-block`（`<details>`） | summary = 工具名 + 参数预览（按工具取关键参数：bash→command、read/write/edit→path、grep/find→pattern、dispatch→name、web_search→query，其余首参数）+ 右侧耗时；展开 = 参数色块（浅灰蓝）+ 输出色块（浅暖灰，≤300px 滚动，展示截断 50KB） | 状态**无文字徽标、用颜色**：`run`=强调蓝、`done`=弱化灰、`err`=红 |
| `.waiting` 等待行 | 「Waiting for model…」呼吸动画行 | `turnActive` 且无流式产出时显示：user 消息发出后、工具结果（`message_end` toolResult / `tool_execution_end`）后；任一产出帧（assistant `message_start`/`message_update`/`tool_execution_start`）到达即移除 |
| `.errbox` 错误红框 | 模型错误（`stopReason=="error"` 的 `errorMessage`；及 `agent_end`/`compaction_end` 的 `errorMessage`） | 同文去重不叠框（`lastRenderedErrorMsg`） |
| `.retrybox` 重试警告框 | `auto_retry_start`：「Retrying (n/max) in Ns…」 | success → 移除 + flash；failure → 原地转红框常驻 |
| `#inputbar` 输入栏 | `#promptInput` 自动增高（≤120px）；Enter 发送/排队，Shift+Enter 换行，IME 组合保护 | 常驻；无发送按钮（三段模式已移除），排队与否由 `turnActive` 自动决定 |

### 7.2 浮层与条件面板

| 元素 | 用途 | 显隐触发 |
|---|---|---|
| `#flashNotice` flash 轻提示 | 顶部居中浮层，承载所有系统提示（挂接/自愈/reload/熔断/发送被拒等），**错误不静默吞掉** | `flash()` 触发；普通 4s、`err` 8s 自动隐藏 |
| `#slashNotice` 斜杠提示条 | 输入框上方内联提示（`/` 支持表粘性展示、命令结果/用法错误） | 输入以 `/` 开头 → 粘性显示 `SLASH_HELP`；执行命令后非粘性 6s 自动隐藏；非 `/` 输入清除 |
| `#queuedPanel` QUEUED 面板 | 「QUEUED · N」：后端权威队列（`queue_update`）+ 本地乐观补位（`pendingSends`，同文本不重复）；条目 = 徽标（`steer` 蓝色描边 / `follow-up` 灰色）+ 单行截断文本；被拒条目红色；**无 Recall 按钮**（pi rpc 协议无召回能力） | 有任一条目时显示，空则隐藏；`queue_update`/发送/送达/清屏时重渲染 |
| `#attachBar` 附件条 | 输入栏上方待发送图片缩略图（粘贴取图，仅拦 `image/*`），单个移除 + Clear (N) | `pendingImages` 非空时显示；发送后清空隐藏 |
| `#dialogBox` dialog 模态盒 | 右下固定浮层：标题（`dialog [method] id=…`）+ 正文 + 按 method 生成控件 + Cancel + 倒计时「Auto-settle in Ns」（250ms tick，到期由 pi 端超时结算） | 默认 `display:none`；`extension_ui_request` 到达且当前无其它框时显示；同框异 id 时仅登记排队（`showDialog` 同 id 幂等）；应答/`cancelled`/`sessiond.dialog_resolved`/`/abort` 时关闭并自动弹下一个排队框；应答被服务端拒绝（如对端已结算的 `no pending dialog`）时 `replyDialog` catch 分支也本地关框（双保险，§11.4） |

dialog 控件按 `method` 分支：`select` → 每选项一个按钮；`confirm` → Confirm(primary)/Reject；`input`/`editor`/未知 → 输入框（Enter 提交）+ Submit。

### 7.3 隐藏/无图形元素与内部结构

| 元素 | 说明 |
|---|---|
| 提示音（无图形控件） | WebAudio 合成 beep：`done` 双音上行（仅 `agent_settled`）、`notify` 单音（页签后台时新 `message_start` 且 `!turnActive`、`sessiond.error`、`extension_ui_request`）；300ms 节流；首次用户手势才解锁（未解锁静默跳过）；开关仅 `/sound [on\|off]`，**默认开、无 UI 开关** |
| `pendingSends` 中 `mode:"prompt"` 条目 | prompt 直发的**静默登记**：不进 QUEUED 面板（`renderQueuedPanel` 跳过），仅供 "already processing" 兜底定位原文重发；属界面状态机的一部分 |
| `.mdtext` 子容器 | assistant 块内 markdown 专用子容器（`mdDiv`），重渲染不擦除 thinking/工具子节点 |
| `curAssistantEl._think` | 流式 thinking 块句柄挂在当前 assistant 元素上（懒创建） |
| `/agents/` 双宿主警示 | 见 §11.1（全文唯一权威处） |
| 无会话提示 | `location.pathname` 不匹配 `/…*.jsonl` → 错误提示引导用 `?v=chat` 打开 |
| 已移除的面 | 顶部状态栏（改 flash）、Abort 按钮（改 `/abort`）、发送/模式按钮、QUEUED 的 Recall 按钮、工具块文字状态徽标、details 展开三角 |

### 7.4 前端关键状态变量

| 变量 | 语义 |
|---|---|
| `attached` / `SESSION` | 当前挂接会话路径（= `location.pathname` 解析） |
| `turnActive` | 回合进行中（user `message_start` 置 true，`agent_end`/熔断清）；决定发送走 `prompt` 还是 `follow_up`，及 waiting 指示与后台 notify 音降噪 |
| `streamCursor` / `lastEidNum` | 最后收到的事件 ID（`<ns>:<seq>`）= 续流游标 / 其数字部分（本地连续性检查） |
| `streamAlive` / `streamCtrl` / `reconnectTimer` | 流存活标志 / AbortController / 1.5s 重连定时器 |
| `lastGen` | 会话进程世代；`session_restarted` 且 gen 变化 → 基线自愈 |
| `curLeafId` / `seenEntryIds` | 活跃分支叶 / 已渲染 entry 幂等集 |
| `seenToolIds` / `toolBlocks` | 已渲染 toolCallId 集（基线 `r:` 前缀表结果侧，与实时事件互斥）/ toolCallId→折叠块句柄 |
| `turnGroup` / `lastProcessGroup` | 当前回合的过程组引用（user `message_start` 重置）/ 孤儿工具帧的兜底挂点（指向同一组） |
| `curAssistantEl` / `curAssistantBuf` / `mdRenderTimer` | 当前流式 assistant 节点 / 累积文本 / 150ms 节流渲染定时器 |
| `pendingSends` / `queueState` | 本地待送达跟踪（cmd id → {mode,text,rejected}）/ 后端权威队列快照 {steering, followUp} |
| `pendingDialogs` / `dialogTimer` / `dialogDeadline` | 悬空 dialog 请求体集（含排队）/ 倒计时定时器 / 到期时间戳 |
| `pendingImages` | 待发送附件 [{mime, b64, dataUrl}] |
| `retryBoxEl` / `lastRenderedErrorMsg` | 当前重试警告框 / 最近已渲染错误文本（去重键） |
| `healBusy` / `healFails` | 自愈互斥锁（`doClear` 亦借用）/ 连续失败计数（5 次耗尽） |
| `soundOn` / `soundCtx` / `soundUnlocked` / `lastBeepAt` | 提示音开关 / AudioContext / 手势解锁标志 / 节流时间戳 |
| `waitingEl` / `curProvider` / `curModelId` / `composing` | waiting 行句柄 / 当前模型（基线 `model_change` 跟踪，`/model` 缺省 provider 用）/ IME 组合标志 |

---

## 8. 连接与自愈

### 长连接消费（非 EventSource）

`index.html:openStream` 用 `fetch` + `resp.body.getReader()` 手动泵读（不用原生 EventSource），`drainFrames` 按 `\n\n` 切帧、解析 `id:`/`data:`（注释行无 `data:` 直接丢弃）。选择手动泵的原因是需要精确控制 AbortController 与 since 游标语义。

### 自愈闭环

| 触发 | 动作 |
|---|---|
| 断流（fetch 失败/`r.done`） | 1.5s 后静默重连，带 `since=streamCursor` 续流（重连不刷屏提示） |
| 服务端 `webgw.gap` 帧 | flash 警告 → `heal("event ring eviction")` 全量基线重拉 |
| **本地 seq 缺口**（第二道探测：`eidNum > lastEidNum + 1`） | `heal("seq jump …")` |
| `sessiond.session_restarted` 且 `gen` 变化 | `heal("session process restart gen→N")` |
| `sessiond.session_restarting`（含 `reload`） | 仅 flash；重拉完成后的 `session_restarted` 带新 gen 再触发自愈 |

`heal()`（`index.html:heal`）：`healBusy` 互斥 → 停流 → `op=attach` 全量基线 → `applyBaseline(doc, false)` **清屏全量重渲染**（页面级策略：消息对象无稳定 id，全量重渲染是无重复的构造性保证；基线组默认折叠、`pendingDialogs` 逐个重建对话框）→ 以 `watermark` 为新游标 `openStream`。失败 2s 后重试，累计 5 次耗尽后提示手动刷新。

**已知边界**：`watermark` 取 get_entries 响应行入环时刻。若 attach 发生在回合进行中，响应行之前入环的在途增量（message_update delta/工具 partialResult）不在 jsonl 基线内、又位于 watermark 之前，新订阅者看不到——表现即在途消息从半截开始显示；由收尾帧全量语义自愈（message_end 最终全文渲染、tool_execution_end 带完整 result）。

基线渲染时活跃分支重建：从 `leafId` 沿 `parentId` 走回根（含 pre-compaction 链），`entry` 幂等集去重；基线中的工具结果（`toolResult`）并入对应工具块，无实时事件故耗时用消息时间戳近似。

---

## 9. 下行事件清单（前端消费）

### 9.1 会话/桥接事件

| type | 语义 | 前端响应 |
|---|---|---|
| `webgw.gap` | 事件环缺口（since 已淘汰），紧随其后从最旧位续流 | 全量基线自愈，见 §8 |
| `sessiond.session_restarting` | 进程退出将重拉；`delay`=退避秒数，`reload:true`=主动重载 | flash（区分主动/崩溃措辞） |
| `sessiond.session_restarted` | 新进程就位，带 `gen`/`pid` | flash；gen 变化 → 基线自愈 |
| `sessiond.session_disabled` | 熔断（崩溃循环超限），监督停止 | flash 错误；清 `turnActive` 与 waiting |
| `sessiond.error` | sessiond 错误通知 | flash 错误；后台 notify 音（见 §11.5） |
| `sessiond.dialog_resolved` | dialog 结算广播（`bridge.py:_broadcast_dialog_resolved`）：应答成功（无附加字段）/ abort 代答（`cancelled:true`）/ 超时清扫（`timeout:true`）/ 会话重启清表（`restarted:true`）；发射时机表见 §11.4 | 各端关闭对应框（幂等），见 §11.4 |

### 9.2 对话事件（`MAIN_EVENT_TYPES`，主流渲染）

| type | 语义 | 前端响应 |
|---|---|---|
| `message_start` | 消息开始（`message.role`） | user：转正+渲染+置 `turnActive`+waiting；assistant：新建块；见 §7.1/§7.4 |
| `message_update` | 流式增量，载荷在 `assistantMessageEvent`：`text_delta`（文本）/`thinking_delta`（思考）/`toolcall_end`（工具调用参数定型，带 `toolCall.id/name/arguments`） | 累积渲染 / thinking 块 / 工具块入过程组（均先 hideWaiting），见 §7.1 |
| `message_end` | 消息收尾 | assistant：收尾+兜底+错误红框；toolResult：幂等登记+waiting；见 §7.1 |
| `tool_execution_start` | 工具开始执行 | 建/更新工具块 `run` 态、计时起点，见 §7.1 工具块 |
| `tool_execution_update` | 工具流式输出（`partialResult` 为累积值） | 整段替换工具输出区，见 §7.1 |
| `tool_execution_end` | 工具结束（`result`/`isError`） | 写结果、算耗时、`done`/`err` 态+waiting，见 §7.1 |
| `extension_ui_request` | dialog 请求（`method` ∈ select/confirm/input/editor，带 `id`/`title`/`options`/`timeout` 等） | `showDialog` 显示或排队，见 §7.2；后台 notify 音见 §11.5 |

### 9.3 回合状态事件

| type | 语义 | 前端响应 |
|---|---|---|
| `auto_retry_start` | 模型调用自动重试开始（`attempt`/`maxAttempts`/`delayMs`/`errorMessage`） | 重试警告框，见 §7.1 `.retrybox` |
| `auto_retry_end` | 重试结束（`success`/`finalError`） | success 移除+flash；failure 转红框，见 §7.1 |
| `queue_update` | 后端权威队列快照 `{steering, followUp}` | 重渲染 QUEUED 面板，见 §7.2 |
| `turn_end` | **子回合边界**（一次 agent run 发多次，含以工具调用结束的子回合） | 仅清 waiting 指示；**不清 `turnActive`** |
| `agent_end` | agent run 结束（可能带 `errorMessage`） | 清 `turnActive`+waiting、折叠过程组；错误红框（去重），见 §7.1 |
| `compaction_end` | 压缩结束 | 仅 `errorMessage` → 红框 |
| `agent_settled` | agent 运行彻底落定（无自动重试/压缩/队列续跑，回到等待用户输入终态；pi `finally` 保证发出） | **唯一触发 done 提示音**；折叠全部过程组 |
| `response` | 上行命令回执 `{id, command, success, error?}` | `handleCmdResponse`："already processing" → 自动转 follow_up（见 §6）；其余拒绝 → flash + rejected |

其余非主流类型（side 帧，由 `classifyEvent` 判定）不渲染；基线回放中 `model_change`/`thinking_level_change` 等 entry 仅更新本地状态（当前模型）或出一行 `◆ …` 提示（经 flash）。

---

## 11. 多 tab / 多客户端并发

同一 jsonl 被多个页签/多个浏览器同开时，模型是**同一桥接 + 同一会话进程上的多个独立订阅者**：会话级状态（进程、队列、悬空 dialog、事件环）天然一致，纯视图态各端独立、互不同步。机制细节一律以前文章节为权威描述，本章只记跨端模型独有的信息。

### 11.1 会话进程唯一（单宿主）

注册表归一：`bridge.py:get_bridge` 注册表键 = 解析后绝对路径（见 §3），多页签命中**同一 `Bridge`**（同一事件环、同一 `Supervisor`、同一 `pi --mode rpc` 子进程）；开新页签只做 attach + 订阅，不 spawn 进程。environ 标记（`SESSIOND_SESSION_FILE`）/`/proc` 扫描/双宿主清场机制见 §2——清场主要面向 web 重启/跨进程场景，同一 web 进程内的多 tab 走注册表归一，不触发清场。

**`/agents/` 警示（全文唯一权威处）**：前端启动时若会话路径含 `/agents/`：红色 flash 警告（可能被其它宿主如子任务以 `pi --session` 打开，8080 识别域看不见外部宿主，可能双宿主）；只警告不拦截（`index.html`）。

### 11.2 每 tab = 全量基线 + 独立 SSE 订阅

每页签各自 `op=attach` 取全量基线（`entries` + `pendingDialogs` + `watermark`）；`attach` 是一次性 POST，**不占 SSE 订阅名额**。每页签一条独立 `GET stream.py?since=<本 tab 游标>`，游标、断线重连各自独立；事件环对**所有订阅者多播同一条流**。订阅上限/原子占位/429/`finally` 退订机制见 §5。**第 6 个页签**：页面照常打开、attach 正常，仅事件流 429 建不起来；已有页签不受影响。

### 11.3 双端发送语义（服务端排队兜底）

各端按本地 `turnActive` 判空闲（空闲 → `prompt` 直发；回合中 → `follow_up` 入队，见 §6）；队列在 pi 进程内，`queue_update` 快照经事件环多播 → 双端 QUEUED 面板同步看到排队条目与续跑（送达信号见 §6）。两端同瞬并发 `prompt`：一条被接受，另一条回 "already processing" → 自动转 `follow_up` 重发入队（拒绝语义见 §6），最终收敛于服务端队列，**不丢消息**；其余拒绝各端只见自己的发送结果（flash 红色 + `rejected` 态）。

### 11.4 dialog（extension_ui_request）双端同弹与结算

- 双端同弹：`extension_ui_request` 多播到所有订阅者，各页签 `showDialog` 弹同一模态盒（同框异 id 排队登记）；刷新/重连后靠基线 `pendingDialogs` 重建（`bridge.py:pending_dialog_list`）。
- 先到生效双保险：应答校验见 §3 `bridge.py:send`——先到的一端生效，另一端再答被拒 403；前端 `index.html:replyDialog` 对服务端报错（如 `no pending dialog` = 对端已结算）在 catch 分支也本地 `closeDialog` 关框，防非应答端 Cancel 永久卡死，与后端广播互为兜底。
- `sessiond.dialog_resolved` 结算广播入事件环、多播到**所有订阅者**——一端结算、各端同步关框；含应答端自身：其前端已本地关框删表，`index.html` handler 查无 `pendingDialogs[id]` 幂等 no-op，安全；事件入环，晚到订阅者（since 回放）也能收到。
- 会话进程重启：`bridge.py:_ingest` 收 `sessiond.session_restarted`——pi 侧 dialog Promise 全部失效，清空 `pending_dialogs`/`_dialog_deadline` 并对所有悬空 dialog 广播 `restarted:true` 结算，否则 attach 基线（`pendingDialogs`）会向各端重建已死 dialog。
- `/abort`：把所有悬空 dialog 以 `cancelled` 代答（机制见 §3，否则回合永久阻塞）并逐个广播 `cancelled:true` 结算 → 双端同步关框。

`sessiond.dialog_resolved` 发射时机表（均经 `bridge.py:_broadcast_dialog_resolved`，帧形 `{type:"sessiond.dialog_resolved", id[, 附加字段]}`）：

| 时机 | 触发点 | 附加字段 |
|---|---|---|
| 应答成功 | `bridge.py:send`：`extension_ui_response` 转发 pi 成功后 | — |
| abort 代答 | `bridge.py:send`：`abort` 把悬空 dialog 以 `cancelled` 代答后 | `cancelled:true` |
| 超时清扫 | `bridge.py:_sweep_expired_dialogs_locked` 清出过期 dialog 后（清扫时机见下表） | `timeout:true` |
| 会话重启清表 | `bridge.py:_ingest`：`sessiond.session_restarted` 清空 `pending_dialogs` 时 | `restarted:true` |

timeout 清扫机制（0830-1104-eji4）：pi rpc-mode 的 dialog 超时是内部 setTimeout、**不发任何事件**，桥接必须自己跟踪 deadline，否则 `pending_dialogs` 永不清理、attach 基线会向新/刷新端重建已死 dialog。

| 项 | 语义 |
|---|---|
| deadline 登记 | `bridge.py:_ingest` 登记带 `timeout` 字段（毫秒）的 dialog 时记单调 deadline（`time.monotonic()` + timeout，存 `_dialog_deadline`） |
| 顺带清扫（无定时器） | `bridge.py:_sweep_expired_dialogs_locked` 在四类时机顺带清出过期 dialog：① 任意 pi 事件经 `_ingest`（含新 dialog 到达——pi 超时后必有后续事件流经这里，无需额外定时器）② 应答（`send` 的 `extension_ui_response`）③ `abort` ④ attach 基线（`pending_dialog_list`） |
| pi 端结算语义 | 请求带 `timeout` 时到期由 pi 端以默认值结算、回合继续（裸 Promise 超时 resolve）；前端倒计时「Auto-settle in Ns」仅展示，关框靠桥接的 `timeout:true` 广播 |
| 无 timeout 字段的 dialog | 不登记 deadline → 永久悬空直至应答/`abort`（与 pi 端一致：裸 Promise 仅 response/signal/timeout 可解，无 timeout 即永悬），**非 bug** |

### 11.5 后台 tab 提示音

回合起点 `message_start` 且 `document.hidden && !turnActive` → 响一声 `beep("notify")`：判定先于渲染（`turnActive=true` 在渲染 user `message_start` 时才置），故**回合起点恰好响一声**；回合内续写（`turnActive == true` 时的 `message_start`，即每次工具调用后模型续写开新 assistant message）静默，避免后台每个工具调用响一声。`sessiond.error`、`extension_ui_request` 到达时后台同样响 notify。声音开关/手势解锁/节流均本 tab 独立（见 11.7），一端后台响铃不影响另一端。

### 11.6 reload / clear 是会话级操作

`op=reload/clear` 语义见 §2 reload/clear 表——作用于**会话进程而非页签级**：任一 tab 发起，全会话生效。双端同步路径：杀进程/重拉经事件环广播 `sessiond.session_restarting` → `sessiond.session_restarted{gen}` 到**所有订阅者**，各页签检测 `gen` 变化各自 `heal()`（见 §8）：停流 → `attach` 全量基线 → 清屏全量重渲染 → 以新 `watermark` 重新开流。SSE 订阅挂在 bridge（不挂进程），进程被杀/重拉**不断流**；「断线重连」实为各端 `heal` 主动停流重建，双端最终渲染状态一致。

### 11.7 不同步的纯本地状态（各 tab 独立）

| 状态 | 说明 |
|---|---|
| `/sound` 开关（`soundOn`） | 默认开，仅本 tab 的 `/sound [on\|off]` 切换，无 UI 开关、不跨端 |
| 折叠态 | 过程组 / thinking 块 / 工具块的 `<details>` 展开折叠态为本地 DOM 状态；自动折叠事件（`agent_end`/`agent_settled`）双端各自执行，手动展开互不可见 |
| flash / notice | `#flashNotice`、`#slashNotice`、sysLine 提示均为本端提示面 |
| `pendingSends` | 本地乐观待送达跟踪（含 prompt 静默登记）；权威队列是 `queue_update`（双端可见），本地补位条目仅本端可见 |
| 输入草稿与附件 | 输入框文本、`pendingImages` 待发送附件、`composing` 等纯输入侧状态 |
| 游标/幂等集 | `streamCursor`/`seenEntryIds`/`seenToolIds` 等各端独立维护，只服务于本端渲染去重 |

一致性总结：**会话态（进程/队列/dialog/事件环）由「注册表归一 + 事件环多播 + 全量基线」保证跨端一致；上表为纯视图态，不影响会话本身的正确性。**
