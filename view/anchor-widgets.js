/* Standalone cmd:/inline: anchor enhancement. No Markdown or my.js dependency.
 * Load with defer; optionally set window.AnchorWidgetsConfig before loading.
 * Protocol/configuration: ../ext/markdown/README.md. Only enhance trusted HTML.
 */
(function (global) {
  'use strict';
  const doc = global.document;
  const options = Object.assign({
    root: null, autoRun: false, source: global.location.pathname,
    shellEndpoint: '/w/ext/shell/rpc/sh.py',
    renderOutput: (node, text) => { node.textContent = text; },
    onChange: () => {}
  }, global.AnchorWidgetsConfig || {});
  const widgets = new Set();
  const enhanced = new WeakMap();
  let observer, batch = Promise.resolve(), nextId = 0;

  function parse(href) {
    if (!href) return null;
    const match = /^(cmd|inline):([\s\S]*)$/.exec(href);
    if (!match) return null;
    const kind = match[1];
    // Markdown encodes spaces in link destinations. Normalize those too before
    // reading the literal manual marker, without mistaking encoded %21 for it.
    const leadingSpace = /^(?:\s|%(?:20|09|0[abcd]))+/i;
    let body = match[2].replace(leadingSpace, '');
    const manual = body.startsWith('!');
    if (manual) body = body.slice(1).replace(leadingSpace, '');
    if (kind === 'cmd') {
      try { body = decodeURIComponent(body); } catch (_) { return null; }
    }
    if (!body.trim()) return null;
    if (kind === 'inline') {
      try {
        const url = new URL(body, global.location.href);
        if (!/^https?:$/.test(url.protocol)) return null;
        body = url.href;
      } catch (_) { return null; }
    }
    return {kind, body, manual};
  }

  function element(tag, className, text) {
    const node = doc.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function connected() {
    for (const w of widgets) {
      if (!w.anchor.isConnected) {
        if (w.resizeObserver) w.resizeObserver.disconnect();
        w.output.remove(); widgets.delete(w);
      }
    }
    return Array.from(widgets);
  }
  function changed() { options.onChange(connected()); }
  function setVisible(w, visible) {
    w.output.hidden = !visible;
    w.status.setAttribute('aria-expanded', String(visible));
    w.status.title = visible ? '隐藏结果（不停止执行）' : '显示已有结果（不重新加载）';
    w.status.setAttribute('aria-label', w.status.title);
    if (visible && w.fit) w.fit();
  }
  function fitFrame(w) {
    const frame = w.frame;
    function fit() {
      if (w.output.hidden) return;
      try {
        const d = frame.contentDocument;
        if (!d || !d.body) return;
        frame.style.height = '1px';
        frame.style.height = Math.max(d.documentElement.scrollHeight, d.body.scrollHeight, 1) + 'px';
      } catch (_) { /* Cross-origin frames keep a useful fixed viewport. */ }
    }
    w.fit = fit;
    frame.addEventListener('load', () => {
      fit();
      if (w.resizeObserver) w.resizeObserver.disconnect();
      try {
        const d = frame.contentDocument;
        if (d && d.body && global.ResizeObserver) {
          w.resizeObserver = new global.ResizeObserver(fit);
          w.resizeObserver.observe(d.body);
          w.resizeObserver.observe(d.documentElement);
        }
      } catch (_) { /* Same-origin measurement only. */ }
    });
  }
  async function command(w) {
    const args = new URLSearchParams({v: 'script', src: options.source || '', cmd: w.body, stream: '3'});
    const token = doc.cookie.match(/(?:^|;\s*)token=([^;]*)/);
    if (token) args.set('token', decodeURIComponent(token[1]));
    const response = await global.fetch(options.shellEndpoint, {
      method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/x-www-form-urlencoded'}, body: args.toString()
    });
    if (!response.ok) throw new Error('HTTP ' + response.status);
    let stdout = '', stderr = '', buffer = '', exitcode = null;
    function line(text) {
      if (exitcode !== null) throw new Error('Shell stream continued after exit status');
      if (text[0] === '1') {
        stdout += text.slice(1);
        options.renderOutput(w.result, stdout);
      } else if (text[0] === '2') {
        stderr += text.slice(1); w.log.textContent = stderr;
        if (w.diagnostics.hidden) {
          w.diagnostics.hidden = false; w.diagnostics.open = true;
        }
      } else if (text[0] === '3') {
        const status = JSON.parse(text.slice(1));
        if (!status || !Number.isInteger(status.exitcode)) throw new Error('Invalid shell exit status');
        exitcode = status.exitcode;
      } else {
        throw new Error('Invalid shell stream: ' + text.trim().slice(0, 160));
      }
    }
    function accept(text) {
      buffer += text;
      let end;
      while ((end = buffer.indexOf('\n')) !== -1) {
        line(buffer.slice(0, end + 1)); buffer = buffer.slice(end + 1);
      }
    }
    const reader = response.body.getReader(), decoder = new TextDecoder();
    try {
      while (true) {
        const part = await reader.read();
        if (part.done) break;
        accept(decoder.decode(part.value, {stream: true}));
      }
      accept(decoder.decode());
      if (buffer) line(buffer);
    } finally { reader.releaseLock(); }
    if (exitcode === null) throw new Error('Shell stream missing exit status');
    if (exitcode !== 0) throw new Error('命令退出码 ' + exitcode);
  }
  function run(w) {
    if (w.running) return w.running;
    w.status.disabled = false;
    setVisible(w, true);
    w.anchor.setAttribute('aria-busy', 'true');
    w.status.textContent = '加载中…';
    w.log.textContent = ''; w.diagnostics.hidden = true; w.diagnostics.open = false;
    w.error.textContent = ''; w.error.hidden = true;
    w.running = Promise.resolve().then(async () => {
      if (w.kind === 'cmd') {
        w.result.replaceChildren();
        await command(w);
        if (w.anchor.isConnected && w.anchor.getAttribute('data-frame-refresh') === 'tabs' && global.parent !== global) {
          global.parent.postMessage({type: 'frame:tabs-changed'}, global.location.origin);
        }
      } else {
        // Only explicit loading replaces the frame. Hide/show retains it.
        // Replacing also guarantees reload for URLs ending in the same fragment.
        if (w.resizeObserver) w.resizeObserver.disconnect();
        if (w.frame) w.frame.remove();
        w.frame = element('iframe'); w.frame.title = w.anchor.textContent || '内嵌页面';
        fitFrame(w); w.result.appendChild(w.frame);
        await new Promise((resolve, reject) => {
          const timer = global.setTimeout(() => finish(new Error('页面加载超时')), 65000);
          function finish(error) {
            global.clearTimeout(timer);
            w.frame.removeEventListener('load', loaded);
            w.frame.removeEventListener('error', failed);
            error ? reject(error) : resolve();
          }
          const loaded = () => finish(), failed = () => finish(new Error('页面加载失败'));
          w.frame.addEventListener('load', loaded);
          w.frame.addEventListener('error', failed);
          w.frame.src = w.body;
        });
      }
      w.status.textContent = new Date().toLocaleTimeString([], {hour12: false});
    }).catch(error => {
      w.status.textContent = '加载失败';
      w.error.textContent = String(error);
      w.error.hidden = false;
    }).finally(() => {
      w.anchor.removeAttribute('aria-busy');
      w.running = null;
    });
    return w.running;
  }
  function build(anchor, parsed) {
    const w = Object.assign({anchor}, parsed);
    anchor.classList.add('aw-label');
    if (w.manual) anchor.classList.add('aw-manual');
    anchor.title = w.body + (w.manual ? '\n仅手动，不参与自动加载或全部刷新' : '') + '\n点击执行／重新加载';
    const controls = element('span', 'aw-controls');
    w.status = element('button', 'aw-status'); w.status.type = 'button'; w.status.disabled = true;
    controls.append(w.status);
    anchor.after(controls);
    w.output = element('div', 'aw-output'); w.output.id = 'anchor-output-' + ++nextId;
    // Renderer-produced anchors may be enhanced, but never automatically execute.
    w.output.setAttribute('data-anchor-output', '');
    w.result = element('div', 'aw-result');
    w.diagnostics = element('details', 'aw-diagnostics'); w.diagnostics.hidden = true;
    w.log = element('pre', 'aw-log');
    w.diagnostics.append(element('summary', '', '诊断输出'), w.log);
    w.error = element('pre', 'aw-error'); w.error.hidden = true;
    w.error.setAttribute('role', 'alert');
    w.output.append(w.result, w.diagnostics, w.error);
    const block = anchor.closest('h1, h2, h3, h4, h5, h6, p') || controls;
    let tail = block;
    while (tail.nextElementSibling && tail.nextElementSibling.classList.contains('aw-output')) tail = tail.nextElementSibling;
    tail.after(w.output);
    w.status.setAttribute('aria-controls', w.output.id);
    setVisible(w, false);
    w.status.addEventListener('click', () => setVisible(w, w.output.hidden));
    anchor.addEventListener('click', event => {
      event.preventDefault(); run(w);
    });
    enhanced.set(anchor, w); widgets.add(w);
    return w;
  }
  function enhance(root) {
    root = root || options.root || doc;
    connected();
    const anchors = root.matches && root.matches('a[href]') ? [root] : [];
    anchors.push(...root.querySelectorAll('a[href]'));
    const added = [];
    for (const anchor of anchors) {
      if (enhanced.has(anchor) || anchor.closest('td, th, pre, code, [data-anchor-widgets="off"]')) continue;
      const parsed = parse(anchor.getAttribute('href'));
      if (!parsed) continue;
      const w = build(anchor, parsed); added.push(w);
    }
    if (added.length) {
      changed();
      if (options.autoRun) enqueue(added.filter(w => !w.manual && !w.anchor.closest('[data-anchor-output]')));
    }
    return added;
  }
  function enqueue(items) {
    batch = batch.then(async () => {
      for (const w of items) if (w.anchor.isConnected) await run(w);
    });
    return batch;
  }
  function runAll() {
    return enqueue(connected().filter(w => !w.manual && !w.anchor.closest('[data-anchor-output]')));
  }
  function configure(config) { Object.assign(options, config); }
  function boot() {
    const style = element('style');
    style.textContent = '.aw-controls{white-space:nowrap;margin-left:.35em}' +
      '.aw-label[aria-busy="true"]{opacity:.65}.aw-manual{color:#b35900}' +
      '.aw-status{color:#888;font-family:inherit;font-size:11px;border:0;padding:0;background:none;cursor:pointer}' +
      '.aw-status:disabled{cursor:default}.aw-status:not(:disabled):hover{text-decoration:underline}' +
      '.aw-output{margin:.4em 0 .8em}.aw-output[hidden],.aw-diagnostics[hidden],.aw-error[hidden]{display:none!important}' +
      '.aw-result{white-space:pre-wrap}.aw-result:has(>*){white-space:normal}' +
      '.aw-output iframe{display:block;width:100%;height:24em;max-height:80vh;box-sizing:border-box}' +
      '.aw-diagnostics{margin-top:.5em}.aw-diagnostics summary{cursor:pointer}' +
      '.aw-log,.aw-error{white-space:pre-wrap;overflow-wrap:anywhere}.aw-error{color:#a33}';
    doc.head.appendChild(style);
    const root = options.root || doc.body;
    enhance(root);
    observer = new MutationObserver(records => {
      for (const record of records) for (const node of record.addedNodes) {
        if (node.nodeType === 1 && node.isConnected && root.contains(node)) enhance(node);
      }
      connected();
    });
    observer.observe(root, {childList: true, subtree: true});
  }
  global.AnchorWidgets = {configure, enhance, runAll, parse,
    getWidgets: connected};
  if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded', boot, {once: true});
  else boot();
})(window);
