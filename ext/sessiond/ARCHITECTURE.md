# sessiond 架构与 API 文档

## 1. 总览

sessiond 是 w 内嵌的聊天传输层。历史由 pi 的会话文件持有，浏览器通过基线与 SSE 渲染。

| 入口 | 进程所有者 | 访问方式 |
|---|---|---|
| `.agent` 声明者 | web 的交互会话宿主 | 本地 stdin/stdout |
| `agents/task/<名>/spec.json`、`agents/bot/<名>/spec.json` | agentd | 连接已登记的 RPC socket |

HTTP API 与 SSE 的 `session` 参数始终使用声明者路径。内部桥接以解析后的会话文件实路径为键；同一会话的多个浏览器共享一条进程通道。

## 2. 进程生命周期

`proc.Supervisor` 持有一枚交互进程，生命周期操作由同一把操作锁串行执行。

- 用户打开、发送命令或明确重载时启动一代进程。
- 自然退出进入 `dormant`；启动失败进入 `failed`。下一次明确访问可以重试。
- `reload`：停止旧进程、等待 stdout 排空，再从同一历史文件启动。
- `clear`：停止旧进程、等待 stdout 排空、截断历史并认领本机原件，再启动。截断失败返回错误。
- `shutdown`：永久关闭当前宿主；排队中的启动操作必须检查关闭状态。删除成功后关闭该桥接的事件流。
- 停止使用自有 Popen 句柄，先 SIGTERM，超时后 SIGKILL；退出未确认时保留失败结果。

启动前清场同文件的残留 web 宿主，保留 environ 标记与进程身份交叉校验。环境清洗单点为调用方的 `agentd/envscrub.py`；声明者的 profile 在清洗后显式注入。

空闲扫描只回收无订阅、无进行中回合、无待答 dialog，且活动时间与历史文件 mtime 均已老化的交互会话。取得进程操作锁后重新检查活动条件。agentd 进程的生命周期由 agentd 管理。

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
| `reload` / `clear` | 声明者路径 | 交互会话直接操作；agentd 会话代理 control/restart 或 control/clear |
| `list_sessions` | — | 枚举本机临时会话 |
| `session_tabs` | — | 输出 itab 的动态行 |
| `rename_session` | 临时会话路径、title | 写入标题 override；空标题清除 override |
| `delete_session` | 临时会话路径、confirm=1 | 停止宿主、移除声明与历史文件、终结事件流 |

`switch_session`、`set_session_name` 被拦截，保持通道与文件绑定一致。rename/delete 的客户端参数限于 session/title/confirm；新建入口由调用方 CLI 提供。

## 5. SSE

端点：`GET /sessiond/rpc/stream.py?session=<声明者路径>[&since=<事件ID>]`。

每个桥接限制订阅数；w 的 HTTP 层还对全部 SSE（包括代理响应）使用共享预算，保留普通请求的工作线程。额度不足返回 503；单桥接额度不足返回 429。

订阅与共享预算由可关闭迭代器持有。正常完成、断开或未开始消费就被拒绝时都释放预约。空闲连接发送 ping 注释。

## 6. 浏览器命令

普通文本在空闲时使用 prompt，回合中使用 follow_up；prompt 被忙态拒绝时允许转入 follow_up。

固定斜杠命令由页面处理。扩展、prompt 和 skill 命令来自当前会话的 `get_commands` 清单；内部探针命令不进入用户白名单。extension 命令使用 prompt，即时交由 pi 执行。

## 7. 渲染

前端为单文件 HTML，无构建步骤。历史按 leafId 重建活跃分支，分片渲染完成后开流。工具输出与过程组由事件更新；用户输入、dialog 与队列状态由各自面板承载。

模型、思考强度、运行中状态和上下文用量从 pi 查询取得，宿主信息从 status 或声明解析结果取得。

## 8. 恢复

事件缺口、进程 gen 变化触发重新取基线。基线代际使旧渲染循环失效；新水位作为续流游标。

网络断开后尝试重连；页面回前台重新建立 SSE。进程退出显示 dormant，用户重开或发送命令启动新代。

## 9. 路径与声明

`declaration.resolve_agent` 是 `.agent` 参数解析单点；每个请求重新读取声明。cwd 与 sessionDir 经实路径根集校验，会话文件为 `<sessionDir>/<文件名>.jsonl`。

host 必填，cwd/sessionDir 缺省为声明者所在目录，profile 可选。profile 只允许调用方清单中声明为 interactive 的条目。目录在实际启动时创建。

服务提供的声明解析响应属于信息查询；启动资格来自请求所引用的声明文件。

## 10. agentd 会话

入口使用 task/bot 的 spec.json。内部目标为该参与方的 session/session.jsonl。

路由读取 spec.json、pid.json，验证声明宿主、进程身份与 RPC socket。未登记、缺席、暂停、终态或通道异常返回指引。直播由 `SocketSupervisor` 连接；其断连终结桥接并使下一次访问重新路由。

reload/clear 写入该参与方 control 请求并等待回执；进程操作由 agentd 执行。

## 11. 多端状态

### 11.1 历史

各端使用同一 pi 历史基线和同一事件通道。

### 11.2 队列

基线附最近的 queue_update；实时事件更新 steering/followUp。

### 11.3 运行状态

agent_start 标记进行中，agent_settled 标记回合结束；get_state 校准运行状态。回合进行中禁止空闲回收。

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

调用方 CLI 调用 `proc.create_cc_session(profile)`。声明与历史落本机运行时目录；profile 决定 cwd，短名由服务生成。

### 12.2 列表与变更

列表从声明文件读取标题、profile、cwd 与历史活动时间；运行态通过宿主标记扫描取得。标题顺序为 override、首条用户消息、文件名。

删除与声明解析共用注册表锁，保证停止到文件移除期间不能另建宿主。rename 只改声明标题。路径校验与文件删除在 proc 层执行。

### 12.3 动态 tab

session_tabs 输出调用方 itab 的动态行；前端加载一次快照。
