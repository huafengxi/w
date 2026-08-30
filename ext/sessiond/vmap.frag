# vmap: ?v=chat → URL 路径驱动聊天窗口（任务 0829-1958-od0t；R1：路径路由）。
# 会话 = 请求路径指向的站内任意 .jsonl（/assistant/foo.jsonl?v=chat、
# /a/b/x.jsonl?v=chat 均可）；前端从 location.pathname 解析，后端经
# session 参数路由；路径校验锁定 ~/m 内（proc.resolve_session_path）。
chat: /sessiond/view/index.html

# .jsonl 默认 chat（任务 0829-2103-dpe4）：mime.frag 把 .jsonl 映射到该
# 专用 mime，无 ?v= 直接打开任意 .jsonl 即聊天窗；?v= 显式覆盖不受影响。
application/x-sessiond-jsonl: /sessiond/view/index.html
