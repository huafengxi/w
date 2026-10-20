# sessiond 架构与 API 文档

## 1. 总览

sessiond 是 w 内嵌的聊天传输层。历史由 pi 的会话文件持有，浏览器通过基线与 SSE 渲染。

| 入口 | 进程所有者 | 访问方式 |
|---|---|---|
| `.agent` 声明者 | 独立 P 持有 pi；Web 管理显式生命周期操作 | 本机 Unix socket → P → PIPE → pi |
| `agents/task/<名>/spec.json`、`agents/bot/<名>/spec.json` | agentd | 连接已登记的 RPC socket |

HTTP API 与 SSE 的 `session` 参数始终使用声明者路径。内部桥接以解析后的会话文件实路径为键；同一会话的多个浏览器共享一条进程通道。

## 2. 进程生命周期

普通会话的 `client.Client` 只连接独立 P 的 Unix socket，不持有 pi PIPE。
`lifecycle.Lifecycle` 串行执行有限的实例操作，`registry.Registry` 协调注册与 CRUD。

- 明确访问时启动或连接；启动须核验本次 P 的内核 peer 身份并取得匹配的 `get_state` 成功回执。
- 浏览器关闭仅释放订阅；Web 客户端 shutdown 只 detach，P/pi 继续运行。Web 重启可依本机实例事实重新连接。
- 确认的自然退出进入 dormant；下一次明确访问可启动新实例。socket EOF 不证明 pi 退出，不完整或未知身份不自动重试接管。
- reload：带当前 P 的真实 boot/PID/start 身份，确认旧进程树停止，再从同一历史启动；不另造随机 instance token。
- clear：同样确认停止后截断历史，再启动；截断或停止失败不启动替代实例。
- delete：核验注册/进程身份与文件身份，确认停止后删除声明、历史和已知宿主文件；保留 inbox，不递归删除无关文件或新 owner 的端点。

普通临时会话一会话一目录：`.agent`（声明配置与标题）、`.jsonl`（pi 历史）、`host.json`（仅有顶层 pid、start、boot）、socket `s` 和诊断 `host.log`。Web 核验 P 后写入身份文件，单独写两份 JSON；控制状态仅在 Web 内存中，P 只维护 socket 与日志。无按代归档、启动参数快照或独立 pidfile。Popen 前不写启动意图；极端启动中断可能留下需人工处理的现场。字段与写入边界见 [ORDINARY.md](ORDINARY.md)。

普通会话只用进程内线程锁，不承诺多 Web 进程原子仲裁或同时启动唯一性。
环境清洗由调用方的 agentd/envscrub 提供；profile 与 pi 扩展在清洗后显式注入。
agentd 会话始终只连接其既有 socket，不借普通生命周期启动或接管。

## 3. 桥接与请求

`bridge.Bridge` 持有有界事件环、查询等待者、队列事件与悬空 dialog。进程输出进入事件环并广播给各订阅者。

`get_entries` 与 `get_commands` 通过 RPC id 配对响应。完整查询结果交给等待者，事件环只保留摘要。进程换代或退出时结算旧查询与 dialog。

事件 ID 为 `<namespace>:<seq>`。SSE 在开流与持续消费期间检查缺口；缺口触发浏览器重新取基线。响应水位之后的事件继续回放。

## 4. HTTP API

端点：`POST /sessiond/rpc/api.py`，表单参数。鉴权由 w 的全局 BasicAuth 承担。

请求必须先校验操作名和命令形状，再打开进程通道。

| op | 参数 | 行为 |
|---|---|---|
| `agent` | `.agent` 路径 | 解析声明，返回 host/cwd/profile/sessionDir 与内部会话路径 |
| `attach` | 声明者路径 | 返回历史基线、leafId、gen、watermark、pendingDialogs、queue |
| `status` | 声明者路径 | 返回当前宿主状态与目录信息 |
| `cmd` | 声明者路径、cmd JSON | 发送 pi RPC 命令 |
| `commands` | 声明者路径 | 返回会话命令清单 |
| `inspect` | 声明者路径 | 返回系统提示与工具清单 |
| `queue_rpc` | 声明者路径、action=clear_queue/steer、message? | 等待真实 pi RPC 回执；仅文本，不处理附件 |
| `chat_state` | 声明者路径 | 通过会话内公开扩展查询完整 busy/终态，并附悬空 dialog |
| `regenerate` | 声明者路径、entryId、message、images JSON | 空闲时从目标用户消息之前重新生成同文件新分支 |
| `reload` / `clear` | 声明者路径 | 交互会话直接操作；agentd 会话代理 control/restart 或 control/clear |
| `list_sessions` | — | 枚举本机临时会话 |
| `session_tabs` | — | 输出 itab 的动态行 |
| `rename_session` | 临时会话路径、title | 写入标题 override；空标题清除 override |
| `delete_session` | 临时会话路径、confirm=1 | 停止宿主、移除声明与历史文件、终结事件流 |

`switch_session`、`set_session_name` 被拦截，保持通道与文件绑定一致。rename/delete 的客户端参数限于 session/title/confirm；新建入口由调用方 CLI 提供。

## 5. SSE

端点：`GET /sessiond/rpc/stream.py?session=<声明者路径>[&since=<事件ID>]`。

支持 SharedWorker 的浏览器按宿主代理前缀共享一个 worker，并以
`/sessiond/rpc/multiplex.py?subscriptions=<JSON>` 合并该宿主的会话流。
subscriptions 为至多 32 个不同的 `{session,since}`；每个会话沿用独立游标、缺口检测与订阅限额。
worker 将事件分发到对应页面，过滤已消费的回放；页面代际 token 丢弃旧订阅消息。
页签关闭释放订阅，worker 统一重连；浏览器不支持 worker 时保留单会话流。
这避免同源多个聊天页分别持有 HTTP/1.1 SSE 而占满浏览器连接槽，使状态查询和命令能继续发出。
多路流由桥接事件唤醒，不另建读取线程或轮询会话状态；1 秒轻量 ping 用于及时发现旧流断开并释放额度。

每个桥接限制订阅数；w 的 HTTP 层还对全部 SSE（包括代理响应）使用共享预算，保留普通请求的工作线程。额度不足返回 503；单桥接额度不足返回 429。

订阅与共享预算由可关闭迭代器持有。正常完成、断开或未开始消费就被拒绝时都释放预约。空闲连接发送 ping 注释。

## 6. 浏览器命令

普通文本在空闲时使用 prompt，回合中使用 follow_up；prompt 被忙态拒绝时允许转入 follow_up。

固定斜杠命令由页面处理。扩展、prompt 和 skill 命令来自当前会话的 `get_commands` 清单；内部探针命令不进入用户白名单。extension 命令使用 prompt，即时交由 pi 执行。

## 7. 渲染

前端为单文件 HTML，无构建步骤。历史按 leafId 重建活跃分支，分片渲染完成后开流。工具输出与过程组由事件更新；用户输入、dialog 与队列状态由各自面板承载。

模型、思考强度、运行中状态和上下文用量从 pi 查询取得，宿主信息从 status 或声明解析结果取得。

### 历史编辑

用户消息以持久 entryId 定位；实时消息点击时先取真实 entries 唯一解析，不能唯一定位则拒绝。进入编辑仅装载文本/图片和保存当前草稿，不 abort 或回溯；取消恢复进入前草稿。busy/未知/待回答时不能提交历史编辑，也不能转为 follow-up；可主动点停止当前任务。

后端先经 get_commands 确认当前世代存在 `sessiond-chat`，旧进程未加载时只报待空闲重载，绝不把未知内部斜杠命令误作用户消息送入模型。会话内 `sessiond-chat` 扩展命令检查空闲、目标活跃分支与实际持久文件绑定，使用公开 `ctx.navigateTree(entryId, {summarize:false})` 移到该用户消息的 parent，再用公开 `pi.sendUserMessage` 开新回合。旧分支仍在同一 append-only JSONL 中，不生成跨注册表的新文件、不带入旧分支摘要或旧后续上下文。pi 对当前 leaf 的 navigateTree 为 no-op 时，先公开追加一条不进入模型上下文的 custom breadcrumb，再导航。导航取消或不符预期时不提交消息，客户端保留编辑内容；网络失败可能送达未知，不自动重试。重新生成不会撤销旧工具操作。

## 8. 恢复

事件缺口、进程 gen 变化触发重新取基线。基线代际使旧渲染循环失效；新水位作为续流游标。

网络断开后尝试重连；页面回前台重新建立 SSE。Web 取得真实的非信号 P wait 且确认进程树闭合后才判 dormant，用户重开或发送命令可启动新代。Web 重启会丢失内存中的 wait 和操作状态；P 已死而无真实 wait 时保持 unknown，须显式核验恢复。

## 9. 路径与声明

`declaration.resolve_agent` 是 `.agent` 参数解析单点；每个请求重新读取声明。cwd 与 sessionDir 经实路径根集校验，会话文件为 `<sessionDir>/<文件名>.jsonl`。

host 必填，cwd/sessionDir 缺省为声明者所在目录，profile 可选。profile 只允许调用方清单中声明为 interactive 的条目。目录在实际启动时创建。

服务提供的声明解析响应属于信息查询；启动资格来自请求所引用的声明文件。

## 10. agentd 会话

入口使用 task/bot 的 spec.json。内部目标为该参与方的 session/session.jsonl。

路由读取 spec.json、pid.json，验证声明宿主、进程身份与 RPC socket。未登记、缺席、暂停、终态或通道异常返回指引。直播由 `agent_client.AgentClient` 连接；其断连终结桥接并使下一次访问重新路由。

reload/clear 写入该参与方 control 请求并等待回执；进程操作由 agentd 执行。

## 11. 多端状态

### 11.1 历史

各端使用同一 pi 历史基线和同一事件通道。

### 11.2 队列

基线附最近的 queue_update；实时事件更新 steering/followUp。整批撤回与转 steer 以 clear_queue 返回数组为准，保留 steering 内及 followUp 内原顺序，不假定跨队列顺序。按钮在操作期间互斥；转发逐条等待回执，失败停止并把未确认及未发送文本追加至草稿，不盲重试。清空和转发不是原子操作，清空会移除队列图片但只取回文本；普通发送失败的附件恢复独立保留。

### 11.3 运行状态

输入框右侧图标不承担发送/停止，文本 title、aria-label 和轻点说明区分未知、busy、等待用户、成功、失败、停止与就绪。agent_end 不收口；agent_settled 才区分结果，避免工具、重试、压缩、队列续跑之间闪现完成。SSE 建连时一次 chat_state 查询恢复权威状态，不新增周期轮询；断线置未知，较旧快照不能覆盖更新事件。普通输入不因 busy 禁用。

### 11.4 Dialog

select/confirm/input/editor 请求按 id 保留，基线重建尚未结算的问题。期限按请求 timeout 跟踪。

超时、成功应答、abort 代答和进程代际结束均广播 dialog_resolved。清扫发生在查询响应前时，仍必须先广播结算再交付响应。

### 11.5 提示音

提示音为各浏览器本地状态，用户手势解锁后生效。

### 11.6 生命周期

明确 reload/clear 作用于整个会话；各端据新 gen 重新挂接。

### 11.7 页签状态

滚动位置、折叠与声音开关由各页签维护。

## 12. 临时交互会话

### 12.1 创建

调用方 CLI 调用 `bootstrap.get_registry().create(profile)`。声明与历史落本机 `run/sessiond/<name>/<name>.agent|jsonl`；profile 决定 cwd，短名由服务生成。创建不会产生进程启动标记；Web 在 P 运行且真实 peer/get_state 验收通过后才写 `host.json`。

### 12.2 列表与变更

列表从声明文件读取标题、profile、cwd 与历史活动时间；运行态由本机独立 P 的实例事实核验取得，不扫描进程名或 environ 推断宿主。标题顺序为 override、首条用户消息、文件名。

删除与声明解析共用进程内注册表锁；不把它描述成跨 Web 进程原子事务。rename 只改声明标题。文件路径与 profile 校验归 sessions，声明与 inbox 完整性、真实进程身份检查和停止后删除协调归 registry/lifecycle。删除后保留的 inbox 使名称不复用。旧平铺临时会话只读展示为 unknown，须显式离线迁移，不在请求中自动接管。

### 12.3 动态 tab

session_tabs 输出调用方 itab 的动态行；前端加载时取快照。聊天窗内 `/rename`（包括清除标题 override）与 `/delete` 仅在 API 明确返回 `ok: true` 后，向父窗口同源发送一次 `{type: 'frame:tabs-changed'}`；失败不通知。独立打开、没有父级容器时不通知。

frame 容器校验同源与已挂载子 iframe 身份后，重新获取完整列表文件及全部 `@dynamic`，不轮询、不重载整个父页面。改名保留原 iframe 与当前选中项；删除会移除对应 tab 和 iframe，删除当前项时回到首项。协议与机制见 `../../README.md`「iframe show many doc」。

## 13. 源码职责与装配

| 文件 | 职责 |
|---|---|
| bootstrap.py | HTTP/CLI 共用配置，显式创建 Registry 和客户端工厂 |
| sessions.py | 路径、profile/cwd、声明/历史文件操作、标题派生 |
| declaration.py | 每请求解析 .agent，返回绑定参数 |
| registry.py | 普通会话声明/inbox 校验、CRUD、客户端/Bridge 装配 |
| lifecycle.py | 独立 P 的启动验收、实例事实、身份核验、停止和换代 |
| client.py | 普通 P socket 客户端；断开/销毁连接不表示杀进程 |
| agent_client.py | agentd 声明路由、既有 socket 客户端、control 请求代理 |
| bridge.py | RPC 应答配对、历史基线、事件环、dialog 与浏览器广播 |
| pi-extension.ts | pi 内部检查、聊天状态及同文件重新生成；由调用方显式 -e 注入 |
| rpc/、view/ | HTTP/SSE 与浏览器界面 |

装配传递显式 client_factory/bridge_factory，不替换导入模块的函数或类。
普通链路由独立 P 唯一持有 pi PIPE；客户端与文件操作层不承接其职责。
独立 P 由调用方配置宿主入口；不属于 Web 仓，不反向依赖本目录。
