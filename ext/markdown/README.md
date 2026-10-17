# ext/markdown — Markdown 视图（text/md）

`.md` 按 GFM Markdown 渲染；`?v=text/md` 可强制任意文件使用本视图。
文档第一个 `# 一级标题` 用作页面标题；列表紧凑渲染；代码以外的 `_` 按字面显示，强调使用 `*…*`。
站内路径与视图路由见根 `README.md`「shell 路由」。顶栏 edit 读写源文件，保存后重新渲染。
普通链接默认新 tab 打开；本视图在 iframe 内时隐藏顶栏。

## 扩展 anchor 协议

扩展由独立库 `/w/view/anchor-widgets.js` 处理 HTML anchor，与 Markdown 解析器无关。
支持普通 HTML、Markdown 或模板系统生成的 anchor；不处理 `td` / `th`、`pre` / `code`
和 `[data-anchor-widgets="off"]` 内的 anchor。表格内使用普通导航链接。

```md
[当前时间](cmd:date)
[服务状态](<cmd:python3 status.py>)
### [服务状态](<cmd:python3 status.py>)
[同步服务](<cmd:!make some-service.sync>)
[内嵌概览](inline:/overview.md)
[按需查看](inline:!/details.md)
[打开原页](/details.md)
```

- `cmd:`：命令在服务端以 `bash -c` 执行，stdout 交给输出 renderer，stderr 进入结果末尾的「诊断输出」折叠区。
- `inline:`：URL 以 iframe 就地加载。仅接受 HTTP(S) 目标；相对 URL 相对宿主页解析。
- 前缀后第一个字面 `!`：该项只能手动触发，不参与自动加载或全部刷新；允许标记两侧前导空白。
- 链接文本：执行／重新加载，并展开结果；运行中点击不重复启动。
- 标题（`h1`–`h6`）内可直接放扩展 anchor，例如 `### [状态](<cmd:python3 status.py>)`。生成时间留在标题内，结果区插在整个标题之后，与标题同级；标题中多个扩展项的结果按链接顺序排列。自动加载与手动标记语义不变。
- 链接旁的生成时间：点击只控制结果显隐，不重新执行／加载；隐藏后仍保留时间供再次展开，不另设三角按钮。未加载时不显示时间；加载中／失败时，同位置的状态文字也可控制显隐。隐藏不取消命令、不销毁 iframe，执行结束不会强制展开。时间控件支持键盘 Enter／空格，并提供展开状态与结果区关联。
- 有 stderr 时自动显示并展开「诊断输出」，用户可自行收起；无 stderr 时不显示该区。stderr 本身不判为失败。
- 请求／加载失败直接在结果区显示错误，不折叠；重新加载清空上一轮诊断和错误。点击生成时间／状态文字同时控制正文、诊断与错误。
- 未加载扩展 JS 时，链接按普通 Markdown/HTML 显示；特殊协议不保证可以打开。链接文本应有可读标签。

### 编码

`cmd:` 后的正文按 URI 编码承载，库读取 `getAttribute('href')` 并只解码一次。
字面 `!` 标记在解码之前识别；命令正文开头的 shell `!` 应编码成 `%21`。
`?` / `#` 不作控件参数或 fragment 解释，均属于命令正文。原命令中的 `%` 必须编码为 `%25`。

短命令可直写；目标含空格时 Markdown 使用 `<…>`，例如 `[状态](<cmd:echo hello>)`。
复杂命令优先移入脚本；程序生成链接时使用 `encodeURIComponent(command)`，并将正文中的 `!` 编码为 `%21`。
`inline:` 目标沿用普通 URL 编码，库不对整个 URL 解码，保留 query、fragment 和转义路径。

### 自动与批量加载

页面 URL 的 `_v=autorun` 让本视图在每次渲染后自动加载普通扩展项。
顶栏 run-all / refresh-all 按 DOM 顺序串行加载普通项；两者和 autorun 一律跳过 `!` 项。
无普通扩展项时隐藏对应顶栏按钮。

动态插入的 anchor 自动增强且增强幂等。命令结果区中的扩展 anchor 可以手动触发，
但不参与 autorun / run-all，避免输出递归执行。表格内的 anchor 始终跳过。

## 独立 HTML 使用

```html
<script>
window.AnchorWidgetsConfig = {
  autoRun: false,
  source: '/example.html',
  shellEndpoint: '/w/ext/shell/rpc/sh.py'
};
</script>
<script src="/w/view/anchor-widgets.js" defer></script>
<a href="cmd:date">当前时间</a>
<a href="inline:!/details.md">查看详情</a>
```

只加载此库即可自动扫描及观察后插入的 DOM，无需 `my.js` 或 Markdown 库。
缺省输出 renderer 使用纯文本；宿主页可显式提供 `renderOutput(node, text)`。
Markdown 视图用自己的 renderer 将 stdout 渲染为 Markdown，库本身不依赖 marked。

公共接口：

- `AnchorWidgets.configure(options)`：设置 `autoRun`、`source`、`shellEndpoint`、`renderOutput`、`onChange(widgets)`；
  启动前也可通过 `window.AnchorWidgetsConfig` 指定扫描/观察根 `root`（缺省 body）。
- `AnchorWidgets.enhance(root)`：立即增强该区域，重复调用不重复造控件。
- `AnchorWidgets.runAll()`：串行加载当前普通项，返回 Promise。
- `AnchorWidgets.getWidgets()`：返回当前仍连接在文档中的控件。

代理页面的 RPC 路由与源文件路径由宿主页分别提供。Markdown 视图从服务端注入的 `src`
确定源文件路径，从浏览器路径差值保留代理前缀；库不推断机器名或部署配置。
iframe 高度在同源时随内容调整，最大 80vh；异源时保持固定视口。显式重新加载可替换 iframe，
仅展开／隐藏保留原 iframe 和内部状态。

## 执行协议与安全边界

shell 端点 `/w/ext/shell/rpc/sh.py` 接受 `v=script`、`src`、`cmd`、`stream=2`。
`src` 环境变量指向源文件。响应为带行标签的流：`1` = stdout、`2` = stderr，流关闭表示结束。
协议不提供退出码，不能把请求结束等同于命令成功。

命令权限等同 web 服务进程，能够读写其账号可访问的文件并执行任意程序。
只对可信页面和可信输出 renderer 启用扩展；`!` 只是自动加载排除标记，不是权限或副作用检查。

## 回归

`node test/markdown_widget.cjs` 检查 Markdown 编码与代理上下文。
`PLAYWRIGHT_MODULE=/path/to/playwright CHROMIUM_BIN=/path/to/chromium node test/anchor_widgets_browser.cjs`
在隔离浏览器夹具中检查独立 HTML、增量增强、表格排除、手动集合、流式输出与 iframe 生命周期，
不连接部署服务或执行真实命令。
