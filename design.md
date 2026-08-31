# design

每个URL都定位了一个resource，resource可以看作是抽象的file。根据file的mime type，
不同的类型的文件会映射到另一个path，原始文件的path我们叫 `src_path`, 映射后的path我们叫 `view_path` 。
`view_path` 可以看作是打开 `src_path` 的方式。

整个server由与业务无关的 `core/` 加上按约定加载的 `ext/<feature>/` 组成，
`stores/` 提供 store 实现，配置文件 (`vmap`, `mime`, `stores/fstab`) 是声明式的
真理来源，代码去适配它们。

## file and store

store用来持久化file，接口如下：

```
class AbstractStore:
   def head(self, path): pass                       # 返回文件meta，至少包含 'type'
   def read(self, path): pass                       # 读取整个文件 (目录路径以 '/' 结尾)
   def read_dir(self, path): pass                   # 可选，目录读取的快路径
   def lazy_read(self, path, range_req=''): pass    # 可选，分段读取，返回 (total, start, end, iter)
   def write(self, path, content): pass
   def delete(self, path): pass                     # 可选
   def mv(self, path, new_path): pass               # 可选，仅限同一store内
```

`head` 返回的dict除了 `type` 还可能带 `rpath` / `size` / `modified` 等字段，
`RootStore` (定义在 `stores/store.py`) 会按 `fstab` 把请求路径分发到对应的子store。

## mime type

`store.head(path)` 返回一个dict，字典的 `'type'` 域就是 mime type。
后缀 → mime 的查表在 `mime/` 目录 (`mime/__init__.py` 合并基线 `mime/mime.frag`
与所有 `ext/<name>/mime.frag`)，文本文件也可以在开头显式指定：

```
 -*- type=script -*- # 显式指定mime type
```

URL query string 里的 `v` 优先级更高，例如 [?v=read](?v=read)。POST body 会被当作
query string 一起并入 args。

## view_map

`mime type` 到 `view_path` 的映射叫 view_map，服务时通过 [/vmap/](/vmap/) 暴露。

view_map 也是按约定拼装：`vmap/__init__.py:build()` 读入 `vmap/vmap.frag`
(核心映射) 再合并所有 `ext/<name>/vmap.frag` (ext 追加自己的映射)，
`core/handler.py:VMap` 在启动时直接调用 `vmap.build()` 加载。

view 文件本身分布在 `core/view/` 与各 `ext/<feature>/view/` 目录。
`core/view/` 只保留通用组件 (`template/404/code/iframe/split` 等)，每个 ext
自带自己的 view 资源。

### 缺失文件的视图兜底（统一语义，0830-2242-u42d 补注）

**「缺失文件 + 后缀显式 mime 映射 + 该 mime 在 vmap 有视图 → 渲染视图（而非 404）」
是设计的必然行为，不是回归**（实现 0830-1924-ft6s，`core/handler.py`）。
判定链：`store.head` 对缺失文件给不出 `type` → 若后缀在 **frag 显式映射表**
（`mime.guess_explicit`：基线 `mime/mime.frag` + 各 `ext/<name>/mime.frag`，
不含 mimetypes/text-plain 泛化回退）命中、且该 mime 在 vmap 有视图，则兜底到该视图；
视图侧自行容忍文件缺失（如聊天窗渲染空会话）。

层味：core 只认「mime → 视图」，文件存在与否不是路由条件——`?v=` 本来就能对任意
路径强制同视图，兜底只是把「显式映射的路径」纳入同一语义，未新增攻击面。
**泛化面**：不止 `.jsonl`——任何「后缀有 frag 显式映射且 mime 在 vmap 有视图」的
缺失路径（`.md`/`.org`/`.itab` 等）都从 404 页变为视图渲染；未映射后缀（即不在任何
frag 显式映射表内的后缀，如 `.txt`、`.log`；注意 `.svg` 有映射（image/svg+xml）
只是无专属视图，不属此类）缺失仍走 404 语义。排障时见到「缺失文件却渲染了视图」
先对照本节，勿当 bug。

## .agent 文件类型（任务 kcywpy；fw2ll1 cwd/sessionDir 拆分）

`xxx.agent` 是 **agent 规格文件**：JSON，字段风格参考 `~/m/agents/` 任务 spec.json，
多余字段宽容；缺字段/坏 JSON 有明确错误提示。文件放 ~/m 服务树内（如
`~/m/assistant/dispatcher.agent`）；agent 名 = 文件名。

**字段（2026-08-31 用户拍板最终形态）**：只留 `host` + 可选 `sessionDir`——

- **cwd（隐含）= .agent 文件所在目录**：会话进程的 pi 工作目录，决定加载哪个
  工作区的 AGENTS/扩展（如 `~/m/assistant/*.agent` → cwd=`~/m/assistant` → 命中
  `assistant/.pi` 全套扩展与 `assistant/AGENTS.md`）。天然在 ~/m 服务树内，无逃逸问题。
- **`sessionDir`（可选）= 会话目录**：会话 jsonl 的落盘处，也是跨机可观测的
  participant 目录。缺省 = cwd 目录（会话文件落在 .agent 旁边，最朴素形态）。
- **`host` v1 边界**：仅持久化保存（存于 .agent 文件）+ 随 `op=agent` 响应返回 +
  聊天页可见；不做跨机拉起。
- **旧 `workdir` 字段（kcywpy v1）不再识别**：读到忽略并记日志提示，不报错。

其余机制：

- **视图映射**：`mime.frag` `.agent → application/x-sessiond-agent`，vmap 指向聊天视图
  （同 `?v=chat`，另有 `?v=agent` 别名）——复用现有聊天界面与监督机制，仅会话启动参数来源不同。
- **启动参数解析单点**：`ext/sessiond/rpc/api.py` `op=agent`（`_resolve_agent`）：
  读规格 → 校验 → **sessionDir 不存在自动 `mkdir -p`（含 participant/ 中间层）+ 记日志** →
  登记显式 cwd（`bridge.set_session_cwd`）→ 返回会话路径与 cwd/sessionDir。
  会话 = **`<sessionDir>/<agent名>.jsonl`**（每 agent 一会话、可重连续聊，类比
  `/assistant/dispatcher.jsonl` 的组织），之后完全走既有 .jsonl 会话链路（桥接/监督/重连）。
- **cwd 显式传递**（fw2ll1）：`proc.Supervisor(session_path, on_event, cwd=None)` ——
  cwd 由调用方显式传入；缺省 = jsonl 所在目录。`.jsonl` 直开路径无登记 → 走缺省，
  **行为零变化**（含现网 `/assistant/dispatcher.jsonl`）。
- **安全**：sessionDir `expanduser` 后 realpath、cwd（.agent 所在目录）均必须落在
  会话路径同一根集（~/m 与 ~/m/run 实路径）内，逃逸 → 400；推导出的会话路径再经
  `resolve_session_path` 二次校验。错误语义：文件缺失 → 404；路径非法/坏 JSON/
  缺 host/sessionDir 非法 → 400。缺失 .agent 依本节上方兜底语义渲染聊天页，由页内展示友好错误。
- **可观测设计**：`sessionDir` 指向 `agents/participant/<名字>/` 时，会话文件经
  agents-sync 四机同步——一台机器可观测另一台机器上 participant 的会话/收件状态。
  **跨宿主守卫（任务 8nherl 最小版）**：.agent 声明 host 的会话只能被声明机实体化——
  守卫单点在 `proc.py host_guard`（Supervisor 创建 = 建桥接/spawn 唯一咽喉）：目标会话文件命中
  声明者推导（`<sessionDir>/<agent名>.jsonl`，assistant/** 递归同口径）且声明 host ≠ 本机 →
  拒绝并提示「请通过 <host> 的服务访问」；本机身份不可得（env/host-id 缺失/未命中）= 配置错误，
  对命中声明者拒绝放行；无声明者会话零波及。
- **host 跨机路由（后续）**：按 host 把请求经反向通道/各机 8080 代理转发到目标机的
  同名端点（配套：多机 8080 + 代理 + rsh 端口转发），只需改 `_resolve_agent` 单点与
  其返回的路由指示。
- **存量约定**：`assistant/dispatcher.agent`（sessionDir=`~/m/agents/participant/dispatcher`）、
  `assistant/operator.agent`（sessionDir=`~/m/agents/participant/operator`；operator 的 cwd
  随之为 `~/m/assistant`，会话文件位置不变，将加载 assistant 扩展——约定自然结果）。
- **UI 约定**：会话名/页标题 = agent 名（文件名）；`/agents/participant/` 下会话豁免
  双宿主盲区警示（该目录是 .agent 会话的约定归属地，sessiond 自家拉起）。
- **同名会话单机不变量**（ogwtb4 评审建议，任务 kqhweh）：同一 session jsonl 同一时刻
  只应被本机一个 supervisor 持有（单一宿主）。非 hub 机器的 receiver 按会话名门控
  认领目录，隐含假设「同名参与方会话仅在一台机器运行」——跨机同时打开同名会话会双宿主
  竞争写同一同步文件（见上方跨机警示）。后续机型扩展（多机同名参与方）必须先破此不变量
  的设计（如 host 路由）。
- **冷面提醒**（任务 kqhweh 修正，原表述失真曾致现网 500 窗口，见 ticket.gwj1xr）：
  后缀→mime 映射表与 vmap 在 web 进程启动时构建一次，新增 `.agent` 映射需重启 web 生效
  （重启前 `/xxx.agent` 返回原文，`?v=chat` 路径不受影响）。代码改动生效语义分两类：
  **script 型 rpc（本目录 `rpc/api.py`、`rpc/stream.py` 等 mime=script 文件）每请求 exec，
  提交即生效**；而它们引用的 `proc`/`bridge` 等模块与 mime/vmap 一样是启动时缓存，
  **需重启 web 才生效**。重启会杀掉全部 8080 会话进程（含现网调度员会话，可自行 resume），
  按需择窗口。

## script

只有 mime type 为 `script` 的 python 文件才会被当作 RPC 执行。
script 里应定义 `interp(store, **args)` 函数；当 `view_path` 是 script 时，
handler 会调用 `interp(...)` 产生响应。

script 分布在 `core/rpc/` (核心只读 rpc，如 `core_read.py`) 与
`ext/<feature>/rpc/` 目录。执行时，`core/handler.py:run_script()` 给 script
注入一个固定的全局命名空间 (标准库 + `popen` / `sub` / `response_part_file`
/ `build_dict` / `dict_updated` / `NULLFD` 等 helper)。script 需要 ext 自己
的模块时，直接 `from ext.<feature>.xxx import Foo` 即可 (例如
`ext/media/rpc/album_rpc.py` 里 `from ext.media.album import AlbumDB`)，
没有额外的注册步骤。

`interp` 的返回值应该是 `(meta, content_iterator)` 的 tuple，也可以只是一个
字符串，字符串会按 `text/plain` 处理。

## RootStore和fstab

`stores/fstab` 把不同类型的 store 挂载到 root store。fstab 是单个声明式文件
(没有 fstab.frag)，每行 `挂载点 类型 参数...`。目前 `stores/` 下提供的
store 实现：

1. DictStore  — 通用的内存键值 store (空 dict 起步，通过 write 更新)。
2. DirStore   — 把本地目录挂载为 store。
3. EncStore   — 在 DirStore 基础上对文件名与内容做混淆，实现简单的加密目录。
4. WebDavStore — 通过 `webdav4` 把远端 WebDAV 挂载为 store，支持 range read。
5. CmdStore   — 把 read/write/delete 委托给外部命令执行。

store 类按命名约定自动加载：`build_root_store` 解析出 fstab 里的类型 `T` 后，
`import stores.<t>_store` 并取 `TStore` 类，然后 `cls(*args, **kw)` 实例化。
因此新增 store 只要在 `stores/` 下放一个 `<t>_store.py` 并在 fstab 里加行即可，
不需要注册代码。

## 扩展的约定

一个 ext 就是 `ext/<feature>/` 目录，可选包含：

- `vmap.frag` / `mime.frag` — 会被自动合并进 `vmap/` 与 `mime/`。
- `sh.rc.frag` — bash 片段 (alias / 函数)，被 `ext/shell/sh.rc` 自动 source。
- `rpc/*.py` — script，供 vmap.frag 里的映射指向。
- `view/*` — 模板 / JS / CSS 等静态资源。
- `bin/` — 会被 `core/server.py:set_path()` 追加到 `PATH`，供 script/CmdStore 调用。
- 任意 `*.py` 模块 — script 通过 `from ext.<feature>.xxx import ...` 直接使用。

没有 `register.py`、没有 `ext=` 环境变量预导入；扩展纯粹按目录约定被发现，
不需要写注册代码。

## shell rc

`ext/shell/rpc/sh.py` 用 bash 流式执行命令，启动时把 `BASH_ENV` 指向
`ext/shell/sh.rc`。这个基线 rc 会遍历并 source 所有 `ext/<name>/sh.rc.frag`，
从而让各 ext 以约定的方式往 bash 环境里注入 alias / 函数 (如 `ext/sql` 提供
表查询别名、`ext/report` 提供 ido 调度命令)，无需在 sh.py 里硬编码。frag
加载完后 `sh.rc` 还会 source 同目录下的 `.sh.rc` (已被 `.gitignore` 忽略)
作为本地覆盖；例如 `ext/report` 的 `ido_report_cmd` 需要 `ido_root` 环境
变量指向 ido 根目录，未设置时会向 stderr 报错并返回 1，没有默认值。

## reverse proxy

路径前缀 → 上游的反向代理，规则声明在 **`ext/proxy/routes.json`**（本节是权威口径）。
规则文件按 mtime 热加载（每请求检查 stat，变更才重读），改规则不用重启服务。
实现：`ext/proxy/proxy.py`（纯标准库 `http.client`，零三方依赖）。

规则格式（顶层 `routes` 数组，也可直接写数组）：

```json
{"routes": [{"prefix": "/proxy/demo", "upstream": "http://127.0.0.1:18099",
             "strip_prefix": false, "timeout": 10}]}
```

- `prefix`：以 `/` 开头的路径前缀；多条规则取**最长前缀**匹配（朴素
  `startswith` 语义，不强制路径段边界）。
- `upstream`：`http(s)://host[:port]`；`strip_prefix` 缺省 false；`timeout` 缺省 10 秒。
- 文件缺失/为空/解析失败 = 代理功能整体关闭（不影响既有管线）；解析失败记 warning。

**插入点与安全**：`proxy_handler` 挂在管线第一个业务位（`core/server.py`，
echo 调试器之后、主路由 `Handler.handle_req` 之前），位于 `BasicAuth` 包裹之内——
**未认证请求到不了代理**（认证在 `core/wsgi.py:run_wsgi` 最外层）。命中规则则转发，
否则返回 None 落回既有管线，未命中行为零变化；若代理前缀与既有路径重叠，代理优先。

**转发语义**：保留原方法与请求体；请求头透传（剔除 hop-by-hop：
Connection/Keep-Alive/Transfer-Encoding/TE/Trailers/Upgrade/Proxy-Auth* 等），
补 `X-Forwarded-For`（追加）/`X-Forwarded-Proto`，`Host` 改写为上游；上游响应的
status/头/体原样回传（同样剔 hop-by-hop）。上游不可达/超时 → 502 Bad Gateway；
上游返回什么就透传什么。

**限制**：非流式响应一次性读完中转（大文件会占内存、延迟到完整才回包）；
流式响应（Content-Type=text/event-stream 或无 Content-Length）按块生成器透传，
wsgi 层出 chunked（任务 xt2sj3，SSE 聊天流经代理嵌入的前提）；`timeout` 对流式连接
即空闲超时（每操作独立计时），SSE 类路由须配大于上游 keep-alive 周期的值；
无连接池复用，每请求新建到上游的连接；不支持 WebSocket 等协议升级。
请求头按规范透传（含客户端 `Authorization`，非 hop-by-hop）——因此上游只应配给可信目标，
避免把本站凭据带到不可信第三方。
响应面由 `handle_request` 统一出头的约定不破：代理把上游头放入 `meta['extra_headers']`
由 wsgi 层追加（0831-0937-sh7l）。

## playback state broadcast

`servers/timestamp-server.py` 独立启动，监听 TCP 23554。
浏览器端的 `ext/media/view/state_reporter.js` 在播放/暂停/定时回报时把状态写入
`playing-state.json` ，server 直接监视该文件并向所有连接的客户端广播实时进度，
用于多端同步播放。
