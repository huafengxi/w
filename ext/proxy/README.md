# 前缀代理

`proxy.py` 提供 WSGI 管线 handler；配置由 `W_PROXY_ROUTES` 注入，不自带部署路由。格式可为数组或 `{"routes": [...]}`：

```json
{"routes": [{"prefix": "/node/", "upstream": "http://127.0.0.1:9000", "strip_prefix": true, "timeout": 65, "local_on": ["node"]}]}
```

`prefix` 以 `/` 开头，最长字符串前缀匹配；`upstream` 接受 http(s) 或 `unix:///absolute/socket`。unix 路径中的 `${RSH_FWD_DIR}` 从同名环境变量展开，必须是绝对目录。`strip_prefix` 缺省 false；`timeout` 缺省 10 秒，是每操作的空闲超时。

`local_on` 使用 `W_HOST_ID_FILE` 的 hostname→规范名映射，未命中使用 hostname。本机匹配时剥前缀直读。未设置 routes 变量时关闭代理；显式配置不可读或非法则抛配置错误。按 mtime 热加载，空文件/空数组关闭代理。

透传原方法、体、状态与非 hop-by-hop 头，追加 X-Forwarded-For/Proto；Authorization 透传，因此上游须可信。SSE 或无 Content-Length 响应逐块传送；不可达/超时返回 502。无连接池、不支持 WebSocket 升级。全局 BasicAuth 包裹代理。

隔离回归：`python test/proxy_unix_test.py`、`bash test/proxy_test.sh`，不修改调用方路由。
