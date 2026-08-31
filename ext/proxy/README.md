# ext/proxy — reverse proxy 路由（0831-0937-sh7l）

把 8080 服务的路径前缀反向代理到上游服务，规则声明在 `routes.json`，
按文件 mtime 热加载（改规则不用重启）。实现：`proxy.py`（纯标准库
`http.client`），挂载点见 `core/server.py` 与 `../../design.md`「reverse proxy」。

## routes.json

```json
{
  "routes": [
    {"prefix": "/proxy/demo", "upstream": "http://127.0.0.1:18099",
     "strip_prefix": false, "timeout": 10}
  ]
}
```

- `prefix`（必填）：路径前缀，以 `/` 开头；多条规则取**最长前缀**匹配。
- `upstream`（必填）：`http://host[:port]` 或 `https://...`。
- `strip_prefix`（可选，缺省 `false`）：转发时是否剥掉前缀。
- `timeout`（可选，缺省 `10`）：上游连接/读取超时（秒）。
- 顶层也可以直接写规则数组；顶层 `_comment` 等非标字段会被忽略。

文件缺失 / 空 / 解析失败 = 代理功能整体关闭（其余管线不受影响）；
解析失败会在服务日志记 warning。

## 行为

- 代理在全局 BasicAuth **之内**：未认证请求到不了代理。
- 未命中任何规则 → 落回既有路由，行为零变化。
- 方法与请求体原样转发；请求头透传（剔除 hop-by-hop），补
  `X-Forwarded-For` / `X-Forwarded-Proto`，`Host` 改写为上游。
- 上游响应的 status / 头 / 体原样回传（剔除 hop-by-hop）。
  响应体一次性读完中转，不做流式透传。
- 上游不可达 / 超时 → `502 Bad Gateway`。

## 测试

`w/test/proxy_test.sh`（需先 `make web.start`）：起临时上游，覆盖
转发/方法体透传/响应透传/502/未命中回落/热更新/解析失败降级。
