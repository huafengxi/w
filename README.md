# 文档编辑服务器

本仓只住 HTTP 文档服务机制、通用协议、示例和隔离测试。真实配置、凭据落点、机器清单和运维脚本由调用方维护；改变部署值不需要本仓 diff。实现结构见 [design.md](design.md)。

## start service

依赖：`WSGIserver`、`wsgi-basic-auth`、`WebOb`、`chardet`；Python >=3.13 还需要 `legacy-cgi`。WebDAV 挂载需要 `webdav4`。启动前可用所选解释器执行 `-c 'import cgi, wsgiserver, wsgi_basic_auth, webob, chardet'` 自检。

调用方设定配置后执行 `python core/server.py HOST:PORT [LOG_FILE]`。有日志文件时服务 fork 为后台进程；生命周期管理由调用方负责。相对挂载参数、日志路径相对 workspace；扩展资源采用 workspace 中的 `/w/` 站内挂载，见 `examples/fstab`。

| 环境变量 | 接口 |
|---|---|
| `W_WORKSPACE` | 必填，已存在的绝对工作区目录；服务 cwd 与 sessiond 安全根 |
| `W_FSTAB` | 必填，绝对挂载配置路径；根挂载必需，其余失败告警跳过 |
| `W_AUTH_FILE` | 必填，绝对凭据文件路径，非空 `user:password`；缺失或格式错误拒启 |
| `W_TLS_CERT` / `W_TLS_KEY` | 均未设置时 HTTP；设置时必须同时指向存在的文件，否则拒启；`nossl=1` 显式禁用 TLS |
| `W_PROXY_ROUTES` | 可选，绝对路由文件；未设置关闭代理；指定但不可读或非法时抛配置错误，启动拒启，热加载请求失败 |
| `W_HOST_ID_FILE` | 本机 hostname 到调用方规范名的映射文件，每行两列；代理缺映射使用 hostname，sessiond 缺映射拒绝打开受宿主约束的会话 |
| `RSH_FWD_DIR` | 路由使用 `${RSH_FWD_DIR}` 时必填绝对 socket 目录 |
| `W_SESSION_PROFILE` | 调用方的缺省交互 profile 名；未设置时新建须显式传 profile，仍验证 manifest 的 interactive 白名单 |
| `W_AGENTD_DIR` | 使用 sessiond 时必填，提供 `envscrub` 模块的绝对目录；身份洗刷由该模块负责 |
| `W_SESSION_PI_BIN` | 可选，sessiond 交互会话的 pi 可执行文件；缺省按 PATH 查找。整批队列操作要求 pi >=0.84.4 的 `clear_queue` RPC，安装与版本由调用方管理 |
| `W_ORG_LOAD_PATH` | 可选，以路径分隔符连接的 Emacs 包目录 |
| `W_ORG_AUTHOR` / `W_ORG_EMAIL` / `W_REVEAL_ROOT` | 可选，org 导出元数据和 reveal 资源 URL |

BasicAuth 包裹所有业务请求（含代理）。DEBUG 日志复用 handler 的 secret 脱敏。sessiond 接受站内路径并保留 realpath 根校验：workspace 与其 run 目录是会话白名单，项目 cwd 还须属于顶层 git 项目；软链逃逸拒绝。探针继承 `W_WORKSPACE` 并在 workspace/run 下写侧车。

## shell 路由

GET 已映射扩展名的路径（如 `.md`）即使文件不存在也返回 HTML 外壳，内容真实性由客户端读内容时检查；其它不存在路径返回 404。页面链接使用绝对站内路径；`.md` 无需 `?v=text/md`。`?_v=inline` 在 markdown 中点击链接时就地展开 iframe（客户端参数，服务端不消费）。

## 扩展与视图

feature-free `core/` 加 `ext/<feature>/`：mime、vmap、shell rc 和 bin 按目录约定组成，无需注册。常用视图：`?v=read`、`?v=head`、`?v=code`、目录 `?v=dir`，上传/写入/移动由 fileops 扩展提供；org、媒体、表格、shell 示例在 `demo/`。原始文件不存在时视图外壳不代表内容存在。

## iframe show many doc

`.itab` / `.iframe` 的 `@dynamic <站内路径>` 行在加载时 fetch 一次，把端点返回的 `名字 URL [k=v …]` 行原位展开。失败展开零行，超时上界 10s，不轮询、不递归。fetch 自动保留页面代理前缀，保证经代理打开时仍请求原机器。示例：`demo/dyn.itab`、`demo/dyn-rows.py`。

## Tests

从仓根执行 `python test/smoke.py`、`python test/proxy_unix_test.py`、`bash test/proxy_test.sh`、`node test/chat_frontend.cjs`。代理测试自建临时配置/上游和隔离 WSGI 实例，不覆盖部署配置。

聊天新增回归：`node test/chat_frontend.cjs`（包含原有回归）、`python test/chat_backend.py`；真实 pi/确定性本地模型夹具：`PI_TEST_BIN=/path/to/pi python test/chat_pi_rpc.py`，不访问真实模型或修改全局配置，临时证据留在新建的测试目录。浏览器夹具：`PLAYWRIGHT_MODULE=/path/to/playwright CHROMIUM_BIN=/path/to/chromium node test/chat_browser.cjs`，可用 `CHAT_SCREENSHOT` 指定截图落点。

sessiond 测试（`resolve_agent_edge.py`、`session_ops_edge.py`、`session_lifecycle.py`、`dormant_send_revive.py`）由调用方设置 `W_AGENTD_DIR` 提供身份洗刷依赖，工作区由 test/sandbox.py 创建，不使用真实 workspace。其余回归在 `test/test_*.py`；测试只读仓内机制资源，写入自建临时树。现场部署断言及截图入口归调用方运维仓。
