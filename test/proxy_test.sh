#!/usr/bin/env bash
# reverse proxy 端到端测试（0831-0937-sh7l）
# 前置：8080 web 服务已启动（make web.start）且加载了代理代码。
# 用法：bash w/test/proxy_test.sh
#
# ⚠ 本脚本会替换现网热加载的路由规则：用例把 `w/ext/proxy/routes.json`（web 每请求按 mtime
#   热加载）临时换成 demo 规则，退出时才从 `.bak` 恢复 → 在承载真实流量的 8080 上直接跑，
#   测试期间现网机器前缀路由（`/dev/`、`/nv1/`、`/mac/`、`/nv2/`）会短暂消失。
#   执行前二选一：① 先 `make web.stop`，改在隔离实例上跑（另起一份 web，路由文件用临时副本）；
#   ② 把脚本改指临时副本的 routes.json —— 当前做不到：路由文件路径由 `w/ext/proxy/proxy.py`
#   的 `_routes_file` 按自身 realpath 定死，没有可重定向的变量，脚本无法在不改现网文件的前提下
#   验证热加载。故保持现形态，以「不在现网 8080 上跑」为前置。
set -u
cd "$(dirname "$0")/../.." || exit 1   # ~/m（web 服务 cwd）

AUTH=$(head -1 ~/.auth/passwd)
if [[ -f ~/.auth/fullchain.pem ]]; then BASE=https://127.0.0.1:8080; CURL="curl -sk"; else BASE=http://127.0.0.1:8080; CURL="curl -s"; fi
ROUTES=w/ext/proxy/routes.json
UP_PORT=18099
pass=0; fail=0
say(){ echo; echo "== $*"; }
ok(){ echo "PASS: $*"; pass=$((pass+1)); }
bad(){ echo "FAIL: $*"; fail=$((fail+1)); }
cleanup(){
  [[ -n "${UP_PID:-}" ]] && kill "$UP_PID" 2>/dev/null
  [[ -n "${UP_SOCK_PID:-}" ]] && kill "$UP_SOCK_PID" 2>/dev/null
  [[ -n "${UP_SOCK:-}" ]] && rm -f "$UP_SOCK"
  if [[ -f "$ROUTES.bak" ]]; then mv "$ROUTES.bak" "$ROUTES"; echo "== routes.json restored"; fi
}
trap cleanup EXIT

say "start test upstream on 127.0.0.1:$UP_PORT"
# 端口被残留进程占用时上游起不来，后续用例会拿到别人的 404 页面而难定位
# （lzful1 实遇：一个 4 天前的孤儿 `python3 -m http.server 18099`）
if ss -ltn 2>/dev/null | grep -q ":$UP_PORT "; then
  echo "FATAL: port $UP_PORT already in use — kill the stale listener first:"
  ss -ltnp 2>/dev/null | grep ":$UP_PORT "
  exit 1
fi
python3 w/test/proxy_upstream.py $UP_PORT &
UP_PID=$!
sleep 0.5
cp "$ROUTES" "$ROUTES.bak"

write_routes(){ printf '%s' "$1" > "$ROUTES"; }

# 用例 0-3 靠一条指向测试上游的 demo 规则：仓内 routes.json 只含真实机器路由
# （48306a2 起已无 /proxy/demo），测试自带规则、不依赖仓内内容（lzful1 修正既有漂移）
DEMO_ROUTES='{"routes": [{"prefix": "/proxy/demo", "upstream": "http://127.0.0.1:'$UP_PORT'"}]}'
write_routes "$DEMO_ROUTES"
sleep 0.2

say "0. 未认证请求不得代理（代理在 BasicAuth 之后）"
code=$($CURL -o /dev/null -w '%{http_code}' "$BASE/proxy/demo/echo")
[[ "$code" == "401" ]] && ok "unauth => 401" || bad "unauth => $code (want 401)"

say "1. GET 转发成功 + X-Forwarded-* 注入"
resp=$($CURL -u "$AUTH" "$BASE/proxy/demo/echo?a=1")
echo "$resp" | grep -q '"method": "GET"' && echo "$resp" | grep -q '"path": "/proxy/demo/echo?a=1"' \
  && echo "$resp" | grep -q 'x-forwarded-for' && echo "$resp" | grep -q 'x-forwarded-proto' \
  && ok "GET forwarded, path/query/XFF ok" || bad "GET forward: $resp"

say "2. POST 方法与请求体透传"
resp=$($CURL -u "$AUTH" -X POST --data 'hello=proxy&n=2' "$BASE/proxy/demo/echo")
echo "$resp" | grep -q '"method": "POST"' && echo "$resp" | grep -q 'hello=proxy&n=2' \
  && ok "POST method+body passthrough" || bad "POST passthrough: $resp"

say "3. 上游响应 status/自定义头/体透传"
resp=$($CURL -i -u "$AUTH" "$BASE/proxy/demo/custom")
echo "$resp" | head -1 | grep -q ' 201 ' && echo "$resp" | grep -qi '^x-upstream-test: proxy-ok' \
  && echo "$resp" | grep -q '"custom": true' \
  && ok "upstream 201 + custom header + body passthrough" || bad "resp passthrough: $resp"

say "4. 未命中规则回落既有管线（行为零变化）"
code=$($CURL -o /dev/null -w '%{http_code}' -u "$AUTH" "$BASE/index.md")
[[ "$code" == "200" ]] && ok "/index.md => 200 via existing pipeline" || bad "/index.md => $code"

say "5. 上游宕 → 502"
write_routes '{"routes": [{"prefix": "/proxy/demo", "upstream": "http://127.0.0.1:18098", "timeout": 3}]}'
sleep 0.2
resp=$($CURL -i -u "$AUTH" "$BASE/proxy/demo/echo")
echo "$resp" | head -1 | grep -q ' 502 ' && ok "dead upstream => 502" || bad "502 case: $(echo "$resp" | head -1)"

say "6. 规则热更新（不重启服务）"
write_routes '{"routes": [{"prefix": "/proxy/demo", "upstream": "http://127.0.0.1:'$UP_PORT'"}]}'
sleep 0.2
code=$($CURL -o /dev/null -w '%{http_code}' -u "$AUTH" "$BASE/proxy/hot/x")
[[ "$code" != "200" ]] && ok "before hot-update /proxy/hot => $code (not proxied)" || bad "unexpected 200 before update"
write_routes '{"routes": [{"prefix": "/proxy/demo", "upstream": "http://127.0.0.1:'$UP_PORT'"},
                          {"prefix": "/proxy/hot", "upstream": "http://127.0.0.1:'$UP_PORT'", "strip_prefix": true}]}'
sleep 0.2
resp=$($CURL -u "$AUTH" "$BASE/proxy/hot/x?z=9")
echo "$resp" | grep -q '"path": "/x?z=9"' && ok "hot-reload new rule + strip_prefix works" || bad "hot-reload: $resp"

say "7. 最长前缀匹配"
write_routes '{"routes": [{"prefix": "/proxy", "upstream": "http://127.0.0.1:18098", "timeout": 2},
                          {"prefix": "/proxy/demo", "upstream": "http://127.0.0.1:'$UP_PORT'"}]}'
sleep 0.2
resp=$($CURL -u "$AUTH" "$BASE/proxy/demo/echo")
echo "$resp" | grep -q '"method": "GET"' && ok "longest prefix wins" || bad "longest prefix: $resp"

say "8. 解析失败降级（代理关闭，回落既有管线）"
write_routes '{broken json'
sleep 0.2
resp=$($CURL -i -u "$AUTH" "$BASE/proxy/demo/echo")
echo "$resp" | head -1 | grep -q ' 502 ' && bad "broken routes still proxying" || ok "broken routes => proxy disabled ($(echo "$resp" | head -1 | awk '{print $2}'))"
cp "$ROUTES.bak" "$ROUTES"
sleep 0.2
write_routes "$DEMO_ROUTES"   # .bak = 仓内真实路由（无 demo 规则），恢复后重写测试规则
sleep 0.2
resp=$($CURL -u "$AUTH" "$BASE/proxy/demo/echo")
echo "$resp" | grep -q '"method": "GET"' && ok "valid routes restored, proxy back" || bad "restore: $resp"

say "8b. unix:// upstream（AF_UNIX 转发，任务 lzful1）"
UP_SOCK="/tmp/lzful1-proxy-up-$$.sock"
python3 w/test/proxy_upstream.py "$UP_SOCK" &
UP_SOCK_PID=$!
sleep 0.5
write_routes '{"routes": [{"prefix": "/proxy/unix", "upstream": "unix://'"$UP_SOCK"'", "strip_prefix": true, "timeout": 10}]}'
sleep 0.2
resp=$($CURL -u "$AUTH" "$BASE/proxy/unix/echo?u=1")
echo "$resp" | grep -q '"path": "/echo?u=1"' && echo "$resp" | grep -q '"host": "localhost"' \
  && echo "$resp" | grep -q 'x-forwarded-for' \
  && ok "unix upstream forwarded (strip_prefix + Host=localhost + XFF)" || bad "unix forward: $resp"
resp=$($CURL -u "$AUTH" -X POST --data 'unix=1' "$BASE/proxy/unix/echo")
echo "$resp" | grep -q '"method": "POST"' && echo "$resp" | grep -q 'unix=1' \
  && ok "unix upstream POST method+body passthrough" || bad "unix POST: $resp"
write_routes '{"routes": [{"prefix": "/proxy/unix", "upstream": "unix:///tmp/lzful1-nope-'"$$"'.sock", "timeout": 3}]}'
sleep 0.2
resp=$($CURL -i -u "$AUTH" "$BASE/proxy/unix/echo")
echo "$resp" | head -1 | grep -q ' 502 ' && echo "$resp" | grep -q 'unix:/tmp/lzful1-nope' \
  && ok "dead unix socket => 502 with unix: label" || bad "unix 502: $(echo "$resp" | head -1)"
kill "$UP_SOCK_PID" 2>/dev/null; rm -f "$UP_SOCK"
cp "$ROUTES.bak" "$ROUTES"
sleep 0.2

say "9. 规则文件缺失 = 代理关闭"
rm -f "$ROUTES"
sleep 0.2
code=$($CURL -o /dev/null -w '%{http_code}' -u "$AUTH" "$BASE/proxy/demo/echo")
[[ "$code" != "200" ]] && ok "routes file gone => fallthrough ($code)" || bad "routes gone still proxied"

echo
echo "===== proxy_test: $pass passed, $fail failed ====="
exit $((fail > 0))
