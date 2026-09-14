# vmap: ?v=chat → URL 路径驱动聊天窗口（任务 0829-1958-od0t；R1：路径路由）。
# 会话 = 请求路径指向的站内任意 .jsonl（/assistant/foo.jsonl?v=chat、
# /a/b/x.jsonl?v=chat 均可）；agentd 登记会话（/agents/(task|bot)/<名>/）入口唯一 =
# spec.json 声明者路径（任务 60grqq，服务端归一，见 ARCHITECTURE.md §10）；前端从 location.pathname 解析，后端经
# session 参数路由；路径校验锁定 ~/m 内（proc.resolve_session_path）。
chat: /sessiond/view/index.html

# ?v=form → bot 登记表单视图（任务 9xn4wa）：登记入口的第二形态，与 URL 直创
# （?v=chat&profile=…&workdir=…）共用同一后端 op（create_bot）与同一写盘函数
# （proc.ensure_bot_registration），不另开写盘路径。入口仍限
# /agents/bot/<名>/spec.json；严格 create-only（已登记的 bot 只读展示现值、submit 禁用），
# 且 command 串由服务端生成、不接受客户端提供。
# 映射到 **script** 而非 text/html 视图（同 vmap/vmap.frag 的 `script:` 行）：表单需要服务端
# 枚举的 profile 清单与现役 spec 现值，由 rpc/api.py:_render_form_view 读模板
# view/form.html.tpl 后一次性渲染（注入 $META_JSON + $ARGS_JSON）⇒ curl/禁用 JS 也能拿到
# 服务端枚举证据，首屏不多一次往返。模板后缀 .tpl 无 mime 映射 ⇒ 直开该路径按 text/plain
# 原样下发（实测 200 + Content-Type: text/plain，占位符未替换），**不会被当 HTML 渲染**；
# 渲染只发生在 ?v=form 经 api.py 的这一条路径上。只读元数据的 RPC 形态（op=bot_form_meta）
# 仍保留：同载荷，供前端改名时重拉该名的登记现值。
# 注：w/core/wsgi.py 对重复 query key 取最后一个值 ⇒ ?v=chat&…&v=form 命中 form。
form: /sessiond/rpc/api.py?v=form

# .jsonl 默认 chat（任务 0829-2103-dpe4）：mime.frag 把 .jsonl 映射到该
# 专用 mime，无 ?v= 直接打开任意 .jsonl 即聊天窗；?v= 显式覆盖不受影响。
application/x-sessiond-jsonl: /sessiond/view/index.html

# .agent 文件类型（任务 kcywpy）：xxx.agent = agent 规格 JSON（host + workdir），
# 复用聊天视图；会话启动参数（cwd=workdir）经 rpc/api.py op=agent 单点解析。
?v=agent 别名同视图。
application/x-sessiond-agent: /sessiond/view/index.html
agent: /sessiond/view/index.html
