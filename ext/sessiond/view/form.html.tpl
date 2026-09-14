<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>bot 登记表单</title>
<script>
// ---- 服务端注入（任务 9xn4wa）----
// 本视图由 rpc/api.py 以 **script** 形态渲染（vmap.frag 的 `form:` 行）：模板
// view/form.html.tpl 经 string.Template.safe_substitute 注入两个占位符后即成 HTML。
//   $META_JSON   = op=bot_form_meta 的同源载荷（host / profiles 服务端枚举 /
//                  existing 现役 spec 现值 / botName / commandPreview / commandTemplate
//                  / defaultReaper / restartPolicies / descriptionMax）
//   $ARGS_JSON   = 本次请求的 query 参数（JSON；含 src = 上游看到的原始路径，代理已剥
//                  前缀）——占位符名沿用 text/html 视图的约定（core/handler.py:do_view），
//                  本视图走 script 形态由 rpc/api.py 自己 safe_substitute
// 服务端渲染 ⇒ curl/禁用 JS 也能拿到 profile 枚举与 command 预览（不依赖客户端 fetch）。
window.__META__ = $META_JSON;
window.__ARGS_JSON__ = $ARGS_JSON;
// 代理前缀感知（与 index.html:8-24 同一套推导，任务 xt2sj3 口径）：浏览器
// location.pathname 带代理前缀 → 前缀 = pathname 去掉 src 尾巴的差。无前缀直开时
// src == pathname → __PREFIX__ = ""。表单页必须复用同一套推导：否则经 /dev/、/mac/
// 前缀访问时 POST 会打到错的机器。
window.__SRCPATH__ = "";
try {
  var __qa__ = window.__ARGS_JSON__;
  if (__qa__ && typeof __qa__.src === "string") window.__SRCPATH__ = __qa__.src;
} catch (e) { /* 注入缺失/损坏 → 退化为无前缀（本机直开行为） */ }
(function () {
  var p = location.pathname, s = window.__SRCPATH__ || "";
  window.__PREFIX__ = (s && p.length > s.length && p.slice(p.length - s.length) === s)
    ? p.slice(0, p.length - s.length) : "";
})();
</script>
<style>
/* 调色板沿用 index.html（同一套 CSS 变量），本页不引任何外部依赖/CDN。 */
:root {
  --bg: #ffffff; --panel: #f4f6f8; --panel2: #e9edf2; --fg: #1a1f26;
  --dim: #5a6472; --acc: #0969da; --ok: #1a7f37; --warn: #9a6700;
  --err: #cf222e; --line: #d0d7de;
}
* { box-sizing: border-box; }
body { margin: 0; padding: 16px; background: var(--bg); color: var(--fg);
  font: 14px/1.5 -apple-system, "Segoe UI", "PingFang SC", sans-serif; }
h1 { font-size: 17px; margin: 0 0 4px; }
.sub { color: var(--dim); font-size: 12px; margin: 0 0 14px; word-break: break-all; }
.wrap { max-width: 860px; margin: 0 auto; }
table.form { border-collapse: collapse; width: 100%; }
table.form th, table.form td { border: 1px solid var(--line); padding: 6px 8px;
  vertical-align: top; text-align: left; }
table.form th { width: 150px; background: var(--panel); font-weight: 600;
  white-space: nowrap; }
table.form td.hint { color: var(--dim); font-size: 12px; }
input[type=text], select { width: 100%; padding: 4px 6px; font-size: 13px;
  border: 1px solid var(--line); border-radius: 6px; background: var(--bg);
  color: var(--fg); font-family: inherit; }
input[readonly], select:disabled, input:disabled { background: var(--panel2);
  color: var(--dim); }
button { background: var(--panel2); color: var(--fg); border: 1px solid var(--line);
  border-radius: 6px; padding: 6px 14px; cursor: pointer; font-size: 13px; }
button:hover:not(:disabled) { border-color: var(--acc); }
button:disabled { cursor: not-allowed; opacity: .55; }
button.primary { background: var(--acc); border-color: var(--acc); color: #ffffff; }
.actions { margin: 14px 0; display: flex; gap: 8px; align-items: center;
  flex-wrap: wrap; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px; white-space: pre-wrap; word-break: break-all; }
.notice { border: 1px solid var(--line); border-radius: 8px; background: var(--panel);
  padding: 8px 10px; margin: 0 0 12px; font-size: 13px;
  white-space: pre-wrap; word-break: break-word; }
.notice.warn { color: var(--warn); border-color: var(--warn); }
.notice.err { color: var(--err); border-color: var(--err); }
.notice.ok { color: var(--ok); border-color: var(--ok); }
.hidden { display: none; }
.badge { display: inline-block; padding: 0 6px; border-radius: 8px; font-size: 11px;
  border: 1px solid var(--line); color: var(--dim); margin-left: 6px; }
.badge.ro { color: var(--warn); border-color: var(--warn); }
.fielderr { color: var(--err); font-size: 12px; margin-top: 2px; }
h2 { font-size: 14px; margin: 18px 0 6px; }
</style>
</head>
<body>
<div class="wrap">
  <h1>bot 登记表单 <span id="modeBadge" class="badge hidden"></span></h1>
  <p class="sub" id="pagePath">…</p>

  <div id="notice" class="notice hidden"></div>

  <!-- 只读形态（已登记）：现役 spec 全字段铺陈，含未识别键 -->
  <div id="existingBox" class="hidden">
    <h2>现役 spec.json（只读）</h2>
    <table class="form"><tbody id="existingBody"></tbody></table>
  </div>

  <form id="regForm" autocomplete="off">
  <table class="form">
    <tbody>
      <tr>
        <th>name（bot 裸名）</th>
        <td>
          <input type="text" id="fName" spellcheck="false">
          <div class="fielderr hidden" id="eName"></div>
        </td>
        <td class="hint">目录名 = <span class="mono">agents/bot/&lt;名&gt;/</span>；文法
          <span class="mono">[A-Za-z0-9._-]+</span>、不以 <span class="mono">.</span> 开头、
          不含 <span class="mono">..</span>、≤64 字节。改名会重新拉取该名的登记现值。</td>
      </tr>
      <tr>
        <th>profile</th>
        <td>
          <select id="fProfile"></select>
          <div class="fielderr hidden" id="eProfile"></div>
        </td>
        <td class="hint">选项由服务端枚举 <span class="mono">bots/profiles/*.json</span>
          （不硬编码）；决定注入哪些能力（caps）与模型档。</td>
      </tr>
      <tr>
        <th>workdir</th>
        <td>
          <input type="text" id="fWorkdir" placeholder="~/m/assistant" spellcheck="false">
          <div class="fielderr hidden" id="eWorkdir"></div>
        </td>
        <td class="hint">会话工作目录（<span class="mono">~/</span> 或
          <span class="mono">/</span> 开头，须已存在、不得在 <span class="mono">agents/</span>
          内）。<span class="mono">~/m/assistant</span> ⇒ 项目级扩展
          <span class="mono">assistant/.pi/extensions/agentd/</span> 在场
          （send_message / dispatch / task_status + 收件 receiver）。</td>
      </tr>
      <tr>
        <th>restartPolicy</th>
        <td><select id="fRestart"></select></td>
        <td class="hint">崩溃自愈策略：<span class="mono">auto</span>（自动拉起，缺省）/
          <span class="mono">manual</span> / <span class="mono">one-shot</span>。</td>
      </tr>
      <tr>
        <th>description</th>
        <td>
          <input type="text" id="fDescription" maxlength="200"
                 placeholder="一句人类可读描述（可空）">
          <div class="fielderr hidden" id="eDescription"></div>
        </td>
        <td class="hint">写入 spec 的 <span class="mono">name</span> 字段（协议 §4.1：
          <span class="mono">name</span> = 人类可读描述，不是目录名）；≤200 字符，可空
          （空 = 不写该键）。</td>
      </tr>
      <tr>
        <th>subscribes</th>
        <td>
          <input type="text" id="fSubscribes" spellcheck="false"
                 placeholder="topic/foo, topic/bar（可空）">
          <div class="fielderr hidden" id="eSubscribes"></div>
        </td>
        <td class="hint">登记期订阅（通道 B），逗号分隔；<b>只接受
          <span class="mono">topic/</span> 族</b>两段 id（task/bot 信箱收件归属 =
          会话名的纯函数，不得被第三方绑定）。可空 = 不写该键。运行期增删订阅走通道 A
          （<span class="mono">topic/&lt;id&gt;/watcher/&lt;裸名&gt;</span> 条目）。</td>
      </tr>
      <tr>
        <th>reaper</th>
        <td>
          <input type="text" id="fReaper" spellcheck="false">
          <div class="fielderr hidden" id="eReaper"></div>
        </td>
        <td class="hint">终态通知唯一收件面，两段路径式 id
          （<span class="mono">task|bot|topic</span> / <span class="mono">&lt;名&gt;</span>）。
          <b>留空 = 服务端缺省填职位信箱</b>（<span class="mono" id="defReaper">topic/dispatcher</span>）
          ——登记侧恒写该键，避免 runner 报「spec 缺 reaper 字段」异常。非法值 → 400
          （不静默回落）。</td>
      </tr>
      <tr>
        <th>command（只读）</th>
        <td colspan="2"><div class="mono" id="roCommand">…</div>
          <div class="hint" style="margin-top:4px">服务端按 profile + 裸名生成（随上两字段实时预览）；
            <b>不接受客户端提供</b>——传 <span class="mono">command</span> 参数一律 400，
            表单不得成为任意命令执行入口。</div></td>
      </tr>
      <tr>
        <th>host（只读）</th>
        <td colspan="2"><span class="mono" id="roHost">…</span>
          <div class="hint" style="margin-top:4px">本机规范名（<span class="mono">env/host-id</span>
            查表）；<span class="mono">createdByHost</span> 取同值。bot 只在登记机被 agentd 认领拉起。</div></td>
      </tr>
    </tbody>
  </table>

  <div class="actions">
    <button type="submit" id="btnSubmit" class="primary" disabled>submit（登记并拉起）</button>
    <button type="button" id="btnChat" class="hidden">打开聊天窗</button>
    <span id="busy" class="hint hidden">提交中…</span>
  </div>
  </form>

  <p class="sub">登记入口的第二形态（<span class="mono">?v=form</span>）：与 URL 直创
    <span class="mono">?v=chat&amp;profile=…&amp;workdir=…</span> 共用同一后端 op
    （<span class="mono">create_bot</span>）与同一写盘函数；严格 create-only——已登记的
    bot 只读展示现值、submit 禁用（spec.json 是协议不可变档）。spawn 单点归 agentd runner，
    web 只写 <span class="mono">spec.json</span> + <span class="mono">enable.json</span>。</p>
</div>

<script>
// ---- 与 index.html 同源的基础件（前缀推导见 head；本页不引外部依赖） ----
function byId(id) { return document.getElementById(id); }
var PREFIX = window.__PREFIX__ || "";
var PAGEPATH = PREFIX ? location.pathname.slice(PREFIX.length) : location.pathname;
var API = PREFIX + "/sessiond/rpc/api.py";
var BOT_SPEC_RE = /^\/agents\/bot\/([^\/]+)\/spec\.json$/;

function api(op, extra) {
  var body = new URLSearchParams(Object.assign({op: op}, extra || {}));
  return fetch(API, {method: "POST", credentials: "same-origin",
    headers: {"Content-Type": "application/x-www-form-urlencoded"},
    body: body.toString()}).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (!r.ok) throw new Error(j.error || ("" + r.status));
        return j;
      });
    });
}

function notice(text, cls) {
  var el = byId("notice");
  if (!text) { el.className = "notice hidden"; el.textContent = ""; return; }
  el.textContent = text;
  el.className = "notice" + (cls ? " " + cls : "");
}
function fieldErr(id, msg) {
  var el = byId(id);
  if (!el) return;
  el.textContent = msg || "";
  el.className = msg ? "fielderr" : "fielderr hidden";
}

// ---- URL 解析：入口路径 + 预填参数 ----
var sp = new URLSearchParams(location.search);
var URL_PROFILE = sp.get("profile") || "";
var URL_WORKDIR = sp.get("workdir") || "";
var m = BOT_SPEC_RE.exec(PAGEPATH);
var URL_NAME = m ? decodeURIComponent(m[1]) : null;

// META 首屏来自服务端注入（$META_JSON，与 op=bot_form_meta 同源同载荷）⇒ 无 JS/无网络
// 往返也已渲染；改名时再经 RPC 重拉该名的登记现值。
var META = (window.__META__ && window.__META__.ok) ? window.__META__ : null;
var CUR_NAME = URL_NAME;    // 当前表单针对的 bot 名（可被用户改名）
var READONLY = false;       // 已登记 → 只读形态

byId("pagePath").textContent = "入口 " + PAGEPATH
  + (PREFIX ? "（代理前缀 " + PREFIX + "）" : "（无前缀直开）");

if (!URL_NAME) {
  notice("本视图只服务 bot 登记入口：路径须为 /agents/bot/<名>/spec.json?v=form"
    + "（当前 " + PAGEPATH + "）", "err");
  byId("regForm").classList.add("hidden");
}

// ---- 客户端校验（只做体验层；服务端校验是唯一权威） ----
var NAME_RE = /^[A-Za-z0-9._-]+$/;
var PID_RE = /^(task|bot|topic)\/[A-Za-z0-9._-]+$/;
function validName(s) {
  return !!s && NAME_RE.test(s) && s.charAt(0) !== "." && s.indexOf("..") < 0
    && s.length <= 64;
}
function validPid(s) {
  if (!PID_RE.test(s)) return false;
  var nm = s.split("/")[1];
  return nm.charAt(0) !== "." && nm.indexOf("..") < 0;
}
function validate() {
  var ok = true;
  var nm = byId("fName").value.trim();
  if (!validName(nm)) {
    fieldErr("eName", "bot 名非法：须 " + "[A-Za-z0-9._-]+" + "、不以 . 开头、不含 ..、≤64 字节");
    ok = false;
  } else fieldErr("eName", "");
  if (!byId("fProfile").value) {
    fieldErr("eProfile", "请选择 profile"); ok = false;
  } else fieldErr("eProfile", "");
  var wd = byId("fWorkdir").value.trim();
  if (!wd || (wd.indexOf("~/") !== 0 && wd.charAt(0) !== "/")) {
    fieldErr("eWorkdir", "workdir 必须是绝对路径（~/ 或 / 开头）"); ok = false;
  } else fieldErr("eWorkdir", "");
  var d = byId("fDescription").value;
  if (d.length > 200) {
    fieldErr("eDescription", "description 过长（" + d.length + " > 200 字符）"); ok = false;
  } else fieldErr("eDescription", "");
  var subs = byId("fSubscribes").value.trim();
  if (subs) {
    var bad = subs.split(",").map(function (x) { return x.trim(); })
      .filter(function (x) { return x && (!validPid(x) || x.split("/")[0] !== "topic"); });
    if (bad.length) {
      fieldErr("eSubscribes", "只接受 topic/ 族两段 id，非法项：" + bad.join(", "));
      ok = false;
    } else fieldErr("eSubscribes", "");
  } else fieldErr("eSubscribes", "");
  var rp = byId("fReaper").value.trim();
  if (rp && !validPid(rp)) {
    fieldErr("eReaper", "reaper 须为两段路径式 id（task|bot|topic / <名>），留空 = 缺省 "
      + (META && META.defaultReaper ? META.defaultReaper : "topic/dispatcher"));
    ok = false;
  } else fieldErr("eReaper", "");
  return ok;
}

// ---- command 预览：模板来自服务端（单一事实源），本页不硬编码命令串 ----
function refreshPreview() {
  if (!META) return;
  var tpl = META.commandTemplate || "";
  var nm = byId("fName").value.trim() || "<名>";
  var pf = byId("fProfile").value || "<profile>";
  byId("roCommand").textContent =
    tpl.split("{profile}").join(pf).split("{name}").join(nm);
}

// ---- 只读形态（已登记）：铺陈现役 spec 全字段（含未识别键），submit 禁用 ----
function renderExisting(spec) {
  var tb = byId("existingBody");
  tb.textContent = "";
  Object.keys(spec).forEach(function (k) {
    var tr = document.createElement("tr");
    var th = document.createElement("th");
    th.textContent = k;
    var td = document.createElement("td");
    td.colSpan = 2;
    var d = document.createElement("div");
    d.className = "mono";
    var v = spec[k];
    d.textContent = (typeof v === "object" && v !== null)
      ? JSON.stringify(v, null, 1) : String(v);
    td.appendChild(d);
    tr.appendChild(th); tr.appendChild(td);
    tb.appendChild(tr);
  });
  byId("existingBox").classList.remove("hidden");
}

function setReadonly(on, spec) {
  READONLY = on;
  ["fName", "fProfile", "fWorkdir", "fRestart", "fDescription", "fSubscribes", "fReaper"]
    .forEach(function (id) {
      var el = byId(id);
      if (el.tagName === "SELECT") el.disabled = on;
      else { el.readOnly = on; }
    });
  var badge = byId("modeBadge");
  badge.classList.toggle("hidden", !on);
  if (on) {
    badge.textContent = "已登记 · 只读";
    badge.className = "badge ro";
    byId("btnSubmit").disabled = true;
    byId("btnChat").classList.remove("hidden");
    notice("已登记，本表单不改既有 spec（spec.json 是协议不可变档）；要改字段 = 人工编辑后 "
      + "python3 agentd/agentctl.py --root <工作区根> control bot/<名> restart。", "warn");
    if (spec) renderExisting(spec);
  } else {
    badge.textContent = "";
    badge.className = "badge hidden";
    byId("btnSubmit").disabled = false;
    byId("btnChat").classList.add("hidden");
    byId("existingBox").classList.add("hidden");
    notice("");
  }
}

// ---- 渲染表单（元数据 → DOM）；首屏用服务端注入的 META，改名时用 RPC 重拉的 ----
function renderMeta(meta, botName) {
  {
      META = meta;
      CUR_NAME = botName;
      byId("roHost").textContent = meta.host || "（env/host-id 未命中本机 → 创建会被拒）";
      byId("defReaper").textContent = meta.defaultReaper || "topic/dispatcher";
      byId("fReaper").placeholder = "留空 = " + (meta.defaultReaper || "topic/dispatcher");
      // profile 下拉：服务端枚举，option 文本带 summary
      var sel = byId("fProfile");
      sel.textContent = "";
      (meta.profiles || []).forEach(function (p) {
        var o = document.createElement("option");
        o.value = p.name;
        o.textContent = p.name + (p.summary ? " — " + p.summary : "");
        if (p.model) o.title = "model: " + p.model + " · caps: " + p.capsCount;
        sel.appendChild(o);
      });
      if (!(meta.profiles || []).length) {
        var o2 = document.createElement("option");
        o2.value = ""; o2.textContent = "（bots/profiles/ 不可读或为空）";
        sel.appendChild(o2);
      }
      // restartPolicy 下拉：取值集也由服务端下发（与 agentctl choices 同源）
      var rs = byId("fRestart");
      rs.textContent = "";
      (meta.restartPolicies || ["manual", "auto", "one-shot"]).forEach(function (v) {
        var o = document.createElement("option");
        o.value = v; o.textContent = v;
        rs.appendChild(o);
      });
      // 预填：已登记 → 现役 spec 现值；未登记 → URL 参数
      var ex = meta.existing;
      if (ex) {
        byId("fName").value = botName;
        var cm = /DISPATCH_PROFILE=(\S+)/.exec(ex.command || "");
        byId("fProfile").value = cm ? cm[1] : "";
        byId("fWorkdir").value = ex.workdir || "";
        byId("fRestart").value = ex.restartPolicy || "auto";
        byId("fDescription").value = ex.name || "";
        byId("fSubscribes").value = (ex.subscribes || []).join(", ");
        byId("fReaper").value = ex.reaper || "";
        setReadonly(true, ex);
      } else {
        byId("fName").value = botName;
        byId("fProfile").value = URL_PROFILE
          && Array.prototype.some.call(sel.options, function (o) { return o.value === URL_PROFILE; })
          ? URL_PROFILE : (sel.value || "");
        byId("fWorkdir").value = URL_WORKDIR || "~/m/assistant";
        byId("fRestart").value = "auto";
        byId("fDescription").value = "";
        byId("fSubscribes").value = "";
        byId("fReaper").value = "";
        setReadonly(false, null);
      }
      refreshPreview();
  }
}

function loadMeta(botName) {
  return api("bot_form_meta", {session: "/agents/bot/" + encodeURIComponent(botName)
                               + "/spec.json", profile: URL_PROFILE})
    .then(function (meta) { renderMeta(meta, botName); });
}

// ---- submit ----
function specPathOf(name) {
  return "/agents/bot/" + encodeURIComponent(name) + "/spec.json";
}
function goChat(name) {
  // 跳转聊天窗并带 justCreated=1：index.html 据此走**有界重试 attach**（复用 6k39t0 的
  // 重试链路，本页不另写一份 attach）。
  location.href = PREFIX + specPathOf(name) + "?v=chat&justCreated=1";
}

byId("regForm").addEventListener("submit", function (ev) {
  ev.preventDefault();
  if (READONLY) return;
  notice("");
  if (!validate()) { notice("表单校验未通过（见字段下红字）；服务端校验才是权威。", "err"); return; }
  var name = byId("fName").value.trim();
  var payload = {
    session: specPathOf(name),
    profile: byId("fProfile").value,
    workdir: byId("fWorkdir").value.trim(),
    restartPolicy: byId("fRestart").value
  };
  var d = byId("fDescription").value.trim();
  if (d) payload.description = d;
  var s = byId("fSubscribes").value.trim();
  if (s) payload.subscribes = s;
  var r = byId("fReaper").value.trim();
  if (r) payload.reaper = r;
  // 硬约束：command 绝不随表单上行（服务端生成）；这里连键都不带。
  byId("btnSubmit").disabled = true;
  byId("busy").classList.remove("hidden");
  api("create_bot", payload).then(function (res) {
    byId("busy").classList.add("hidden");
    if (res.created) {
      notice("已登记 bot/" + name + "（profile=" + res.profile + ", workdir=" + res.workdir
        + ", host=" + res.host + ", restartPolicy=" + res.restartPolicy
        + ", reaper=" + res.reaper + (res.reaperDefaulted ? "（缺省）" : "") + "）"
        + "，转聊天窗等待 agentd 拉起…", "ok");
      setTimeout(function () { goChat(name); }, 600);
    } else {
      // created:false = 竞态（页面加载后才被别人登记）：就地显示现值，不跳转
      byId("btnSubmit").disabled = false;
      var eff = res.effective || {};
      var msg = "已有登记（profile=" + (eff.profile || "?") + ", workdir="
        + (eff.workdir || "?") + "），本次提交未生效";
      if (res.mismatch && res.mismatch.length) msg += "；与提交值不同：" + res.mismatch.join(", ");
      loadMeta(name).then(function () {
        notice(msg + "。表单已切只读形态显示现役 spec（create-only：本表单不改既有 spec）。",
               "warn");
      }).catch(function (e) { notice(msg + "；重取现值失败：" + e.message, "err"); });
    }
  }).catch(function (e) {
    byId("busy").classList.add("hidden");
    byId("btnSubmit").disabled = !READONLY;
    // 服务端错误原文，不静默
    notice("create_bot 失败：" + e.message, "err");
  });
});

byId("btnChat").addEventListener("click", function () {
  goChat(byId("fName").value.trim() || CUR_NAME);
});

// 改名 → 重新拉取该名的登记现值（防「表单说未登记、其实已登记」）
byId("fName").addEventListener("change", function () {
  var nm = byId("fName").value.trim();
  if (!nm || nm === CUR_NAME || !validName(nm)) { refreshPreview(); return; }
  loadMeta(nm).catch(function (e) {
    notice("拉取 bot/" + nm + " 的登记现值失败：" + e.message, "err");
  });
});
["fName", "fProfile"].forEach(function (id) {
  byId(id).addEventListener("input", refreshPreview);
  byId(id).addEventListener("change", refreshPreview);
});
byId("fWorkdir").addEventListener("input", validate);
byId("fSubscribes").addEventListener("input", validate);
byId("fReaper").addEventListener("input", validate);
byId("fDescription").addEventListener("input", validate);

if (URL_NAME) {
  document.title = "bot/" + URL_NAME + " · 登记表单";
  if (META) {
    renderMeta(META, URL_NAME);          // 首屏：服务端注入，零往返
  } else {
    loadMeta(URL_NAME).catch(function (e) {
      notice("加载表单元数据失败（op=bot_form_meta）：" + e.message, "err");
    });
  }
} else if (META && META.error) {
  notice(META.error, "err");             // 服务端渲染期即判非法路径/族
}
</script>
</body>
</html>
