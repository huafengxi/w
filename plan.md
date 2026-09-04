# plan — markdown 视图 `?v=iframe` 页内预览（task 9ey94j，M 级）

## 需求回顾
`?v=text/md` 页面（`w/ext/markdown/view/markdown.html`）中，链接 URL 带 `?v=iframe`/`&v=iframe`
时点击不导航，改为在页内就近内联展开 iframe 预览（src = 去掉该参数后的 URL）；再点收起；
可同时展开多个；普通链接行为不变（`<base target="_blank">` 弹新 tab）。

## 交互细节
- **触发**：事件委托绑在 `#doc` 上（一次绑定，renderDoc 重渲染不失效，`?refresh=` 轮询场景安全）。
  点击目标向上找最近 `a[href]`；href 的 query 含 `v=iframe` 才拦截（`preventDefault`），否则放行。
- **展开位置**：锚点所在的最近 `li` 之后；无 `li` 则最近 `p` 之后；再无则锚点自身之后。
  即列表项/段落下方就地展开。
- **toggle**：拦截的锚点记 `__iframeBox` 属性（指向已插入的容器 div）。已存在 → 移除（收起）；
  不存在 → 新建容器插入。多个链接各自独立，可同时展开多个。
- **iframe src 构造**：`new URL(a.getAttribute('href'), location.href)` 解析（兼容相对链接），
  用 `URLSearchParams` 删除 `v=iframe`（仅当值为 `iframe` 时；值非 iframe 的 `?v=` 视为服务端
  视图参数不动、不拦截），其余参数（如 `refresh=`）原样保留。容器带 `data-src` 记录真实地址便于验证。
- **样式**：容器 `.iframe-preview`：上下 margin、`1px solid #999` 边框感由既有 my.css `iframe{}`
  规则承担（宽 100%、黑边）；本任务仅内联 `<style>` 补 `height: 32em`（固定高度 + iframe 自身滚动）
  与容器 margin。不引外部依赖、不改 my.css。
- **iframe 内 markdown 页**：`initApp()` 既有 `inIframe() → hide head-bar`（`my.js:inIframe`
  判 `window.self !== window.top`），iframe 载入即自动生效，验证时断言。

## 改动点（逐项）
1. `w/ext/markdown/view/markdown.html`（唯一代码改动）
   - `<style>` 追加 `.iframe-preview iframe { height: 32em; display:block; }` 与容器 margin；
   - 新增 `iframePreviewInit()`：`#doc` click 委托（逻辑如上）；`initApp()` 末尾调用（仅一次绑定）。
2. `w/README.md`：shell 路由条目（引用块）补一句 `?v=iframe` 页内预览约定（稳定结论式措辞）。
3. `~/m/index.md`（主仓）：挑 1-2 个小文件链接（`notes/README.md`、`bin/README.md`）加 `?v=iframe` 示范。
4. `w/ext/markdown/README.md`：若实施时已存在（前序文档任务产出），在其参数小节补 `?v=iframe`
   约定；不存在则跳过并在报告说明（调度员 inform 追加项）。

## 影响面
- 仅 markdown 视图模板（按请求读取，无需重启服务）；不动其他视图、不动 my.css、不动服务端。
- 无 `v=iframe` 参数的页面行为零变化（委托逻辑先判参数再拦截）。
- `v=iframe` 纯客户端消费：剥离后才进 iframe，绝不会原样发给服务端。

## 验证方案
1. 语法自查：node 解析内联脚本（提取 `<script>` 跑 `node --check`）。
2. **线上 8080 真实页面 E2E**（沿用前序任务 harness）：mac headless Chrome + CDP
   （Node ≥22 原生 WebSocket 驱动），Basic auth 经 `Network.setExtraHTTPHeaders` 注入
   （凭据取 dev `~/.auth/passwd`，**不**走 URL 内嵌凭据——前序任务已证会破坏页内 fetch）。
   导航到含 `?v=iframe` 示范链接的页面，`Runtime.evaluate` 程序化点击并断言：
   - 点击后容器/iframe 出现于锚点所属 `li` 之后；再点收起（toggle）；
   - 两个链接分别点击 → 同时展开 2 个 iframe；
   - iframe `src` 不含 `v=iframe`、其余参数保留；
   - 无 `v=iframe` 的普通链接不被拦截（点击未被 preventDefault，target 继承 `_blank`）；
   - iframe 内页面 `inIframe()` 成立、`#head-bar` 隐藏、正文渲染非空。
3. 人工验证清单写入 report.md。

## 提交
- w 仓：plan.md + markdown.html + README.md（+ ext/markdown/README.md 若适用）
  分逻辑提交，message 含 9ey94j，push origin。
- 主仓：index.md，message 含 9ey94j，push origin。
- 均基于最新 HEAD 操作（先 git pull --ff-only / 确认无冲突）。
