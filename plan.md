# plan — markdown.html base target + edit/split 修复（w1uh3g，M 级）

## 定位结论

### 根因 1：edit（及 head-bar 所有 `javascript:` 锚点）被 `<base target="target">` 吞掉
`<a href="javascript:toggle_editor()">` 的点击是一次超链接导航，目标浏览上下文由
`<base target>` 决定（HTML 规范：following hyperlinks → navigate targetNavigable）。
名为 "target" 的上下文不存在 → 每次点击新建/复用一个名为 "target" 的窗口，
`javascript:` 在**那个窗口**（空白 about:blank，无 toggle_editor）里求值，主页面毫无反应。
这也正是用户看到的「内容链接复用同一个名为 target 的 tab」的同源行为。

**实证**（mac Chrome headless，fixture 页程序化点击锚点后读标志位）：
- `<base target="target">` + `javascript:` 锚点 → 主文档函数未执行（`ran=false`）。
- `<base target="_blank">` + `javascript:` 锚点 → 主文档函数仍未执行（`ran=0`）——
  所以**仅改 base target 不能修好 edit**，必须同时改锚点写法。
- `href="#" onclick="...; return false;"` → 正常执行（`ranFix=1`）。

同仓 `ext/org/view/org.html` 的 edit/split 锚点用的就是可用写法（`href="#" onclick=...; return false;`），
markdown.html 是残留的旧写法。

### 根因 2：split 双重失效
1. 锚点点击同被 base target 吞（同根因 1）；
2. 即使点击生效，`split()` 跳 `getUrl() + "?type=split"`：
   - `getUrl()` 只取 pathname，丢弃现有查询参数；
   - 服务端（`core/handler.py do_req`）只认 `?v=`，`type` 无路由；
   - 老 `split:` vmap 条目与 `view/split.html`（左导航 iframe + 右 "target" iframe）
     已在 ea1613a「drop obsolete split/hsplit/tsplit views」被有意删除。
   → `?type=split` 从 2021 首版起就是只会原样重载的死链接。
3. 语义基础消失：老 split 的意义是「链接落入右栏 target 窗格」，本任务已拍板
   base target → `_blank`（链接弹新 tab），该语义不复存在。

**用户拍板（ask 2026-09-04-17-02）**：选 A —— 删除 split 按钮与 `split()` 函数
（确认无其他引用：全仓仅 markdown.html / org.html 各一处，org 不在本任务范围）。

### 附带发现（同文件同缺陷）
head-bar 的 `run-all` / `refresh-all` / `log` 三个按钮同为 `javascript:` 锚点，
同被 base target 吞（平时 `display:none`，有内联 `${cmd}` widget 的 md 才出现）。
同根因、同文件、同一行内修法，一并修复（不修则 base 改 `_blank` 后依然全灭）。

## 改动点（全部在 `ext/markdown/view/markdown.html`）
1. `<base target="target">` → `<base target="_blank">`（用户拍板①）。
2. edit 锚点：`<a href="javascript:toggle_editor()">` → `<a href="#" onclick="toggle_editor(); return false;">`（org.html 同款模式）。
3. 删除 split 锚点与 `function split()` 一行（用户拍板②）。
4. run-all/refresh-all/log 锚点同改 `href="#" onclick="...; return false;"`。

## inform 追加项（3 条，已 ack，以最后一条为准）
5. `README.md` 第 6 行改写为（措辞以 16:58 inform 最终稿为准）：
   > **shell 路由**：GET 已映射视图的扩展名路径（如 `.md`）即使文件不存在也返 200 + HTML 外壳（内容真实性由客户端取内容时才见分晓）；其余不存在路径返 404（`w/view/404.html`）。页面内容里的链接必须写**绝对路径**，相对链接会被当作页面路径吃掉；`.md` 链接无需 `?v=text/md` 后缀（服务端按扩展名推断视图），仅 `.py` 等非 md 文件渲 md 视图时才带 `?v=...`。
   即：删「SPA」提法、删「服务端无 404」、删行尾历史叙述（任务 oln8xf 归 git history）。
   附带检查 `grep -n SPA README.md design.md` 无残留（现仅 README 第 6 行一处）。
   注：不存在目录路径返 500 的问题已另行登记任务，不处理。

## base target 改动影响面
- `grep -rn '"target"' w/`（w 仓内）现存依赖：`ext/media/view/album.html`、
  `ext/encrypt/view/encrypt.html`、`ext/org/view/org.html`（运行时注入）、
  `core/rpc/core_read.py`（dir 列表链接 `target="target"`）。
  这些是**其他视图**，本任务只动 markdown 视图；markdown 渲染出的正文链接
  改 `_blank` 后每次点击弹新 tab，不影响上述视图各自的 target 语义。
  曾消费 "target" 窗格的 `view/split.html` 已删除，无代码依赖 markdown 页的 "target" 窗。
  风险说明入报告，不阻塞（用户已拍板）。

## 验证方式
1. 语法：抽出 markdown.html 内联 `<script>` → `node --check`。
2. grep 验收：`grep -n 'base target' ext/markdown/view/markdown.html` 仅剩 `_blank` 一行；
   `grep -n '?v=text/md' README.md` 不再出现在推荐风格描述中；`grep -n SPA README.md` 无。
3. 行为：mac Chrome headless 对**线上 8080 真实页面**（改动即生效，模板按请求读取）
   程序化点击 edit → 断言 `#editor` 面板可见 / 再点隐藏；确认无 split 残留、
   正文链接 `target=_blank` 生效。凭据取 `~/.auth/passwd`。
4. git：commit（message 含 w1uh3g）+ push origin。

## 服务影响
静态模板按请求读取，无需重启（w 服务 version 不动）。若浏览器实测发现旧缓存，
在报告说明（预期无：8080 对视图未加特殊缓存头，普通刷新即回源）。
