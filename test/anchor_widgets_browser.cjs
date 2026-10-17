// Standalone HTML + actual Markdown view, deterministic browser fixtures only.
// PLAYWRIGHT_MODULE=/path/to/playwright[-core] CHROMIUM_BIN=/path/to/chromium node test/anchor_widgets_browser.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = path.join(__dirname, '..');
const library = fs.readFileSync(path.join(root, 'view/anchor-widgets.js'), 'utf8');
const view = fs.readFileSync(path.join(root, 'ext/markdown/view/markdown.html'), 'utf8');
(async () => {
  const browser = await chromium.launch({headless: true, executablePath: process.env.CHROMIUM_BIN});
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    let iframeLoads = 0;
    await page.route('http://fixture.test/**', async route => {
      if (route.request().url().includes('/preview')) iframeLoads++;
      await route.fulfill({contentType: 'text/html', body: '<p>iframe content</p>'});
    });
    await page.goto('http://fixture.test/page.html');
    await page.setContent(`<p><a id="auto" href="cmd:date">时间</a></p>
      <p><a id="manual" href="cmd:!change">变更</a></p>
      <p><a id="preview" href="inline:!/preview?q=%2F#part">详情</a></p>
      <table><thead><tr><th><a id="th" href="cmd:header">标题</a></th></tr></thead>
      <tbody><tr><td><a id="td" href="cmd:cell">单元格</a>
      <a id="td-inline" href="inline:/preview">预览</a></td></tr></tbody></table>
      <code><a href="cmd:code">代码</a></code><pre><a href="cmd:pre">代码块</a></pre>
      <a id="old" href="/preview?_v=inline">旧链接</a>
      <a href="cmd:">空</a><a href="cmd:!%20">空手动</a><a href="cmd:%ZZ">坏编码</a>
      <a href="inline:javascript:alert(1)">非法页面</a>
      <a href="cmd:!%0A">空白</a>`);
    await page.evaluate(() => {
      window.calls = [];
      window.AnchorWidgetsConfig = {source: '/document.md', shellEndpoint: '/worker/shell', autoRun: false};
      window.fetch = async (url, opts) => {
        const args = Object.fromEntries(new URLSearchParams(opts.body));
        calls.push({url, ...args});
        if (args.cmd === 'http-error') return new Response('denied', {status: 403});
        if (args.cmd === 'bad-stream') return new Response('Exception: shell failed\n');
        return new Response(new ReadableStream({start(controller) {
          window.release = () => {
            const bytes = new TextEncoder().encode('1**ok** 雪\n2warning\n1last');
            controller.enqueue(bytes.slice(0, 10));
            controller.enqueue(bytes.slice(10, 13));
            controller.enqueue(bytes.slice(13)); controller.close();
          };
          if (args.cmd !== 'hold') release();
        }}));
      };
    });
    await page.addScriptTag({content: library});
    assert.equal(await page.evaluate(() => typeof marked), 'undefined', 'no Markdown dependency');
    assert.equal(await page.locator('.aw-label').count(), 3);
    assert.equal(await page.evaluate(() => calls.length), 0, 'enhancement does not execute by default');
    assert.equal(await page.locator('#td + .aw-controls, #th + .aw-controls').count(), 0);
    assert.equal(await page.locator('#old').getAttribute('class'), null, 'no old URL compatibility');
    await page.evaluate(() => { AnchorWidgets.enhance(); AnchorWidgets.enhance(); });
    assert.equal(await page.locator('.aw-label').count(), 3, 'idempotent');
    assert.equal(await page.locator('#auto + .aw-controls button').first().isDisabled(), true);
    await page.click('#auto');
    await page.waitForFunction(() => !document.querySelector('#auto').hasAttribute('aria-busy'));
    const autoOutput = page.locator('.aw-output').nth(0);
    assert.equal(await autoOutput.locator('.aw-result').innerText(), '**ok** 雪\nlast', 'plain renderer and chunk decoder');
    assert.equal(await autoOutput.locator('.aw-log').innerText(), 'warning\n');
    assert.equal(await autoOutput.locator('.aw-log').isVisible(), true);
    await page.locator('#auto + .aw-controls button').first().click();
    assert.equal(await autoOutput.isVisible(), false);
    await page.locator('#auto + .aw-controls button').first().click();
    assert.equal(await autoOutput.isVisible(), true);
    assert.equal(await page.evaluate(() => calls.length), 1, 'toggle never runs');
    await page.click('#auto');
    await page.waitForFunction(() => calls.length === 2 && !document.querySelector('#auto').hasAttribute('aria-busy'));
    await page.evaluate(() => AnchorWidgets.runAll());
    assert.equal(await page.evaluate(() => calls.length), 3);
    assert(!await page.evaluate(() => calls.some(c => ['change', 'cell', 'header', 'code', 'pre'].includes(c.cmd))));
    assert.deepEqual(await page.evaluate(() => [calls[0].url, calls[0].src, calls[0].stream]),
      ['/worker/shell', '/document.md', '2']);
    await page.click('#manual');
    await page.waitForFunction(() => calls.some(c => c.cmd === 'change'));
    assert(await page.locator('#manual').evaluate(a => a.classList.contains('aw-manual')));

    // New nodes automatically enhance. Output nodes never join autorun/run-all.
    await page.evaluate(() => {
      AnchorWidgets.configure({autoRun: true});
      const p = document.createElement('p'); p.innerHTML = '<a id="dynamic" href="cmd:dynamic">动态</a>';
      document.body.append(p);
    });
    await page.waitForFunction(() => calls.some(c => c.cmd === 'dynamic'));
    await page.evaluate(() => {
      AnchorWidgets.configure({renderOutput: (node) => {
        node.innerHTML = '<a id="nested" href="cmd:nested">输出里的动作</a>';
      }});
      return AnchorWidgets.runAll();
    });
    await page.waitForSelector('#nested.aw-label');
    assert(!await page.evaluate(() => calls.some(c => c.cmd === 'nested')), 'no recursive autorun');
    await page.evaluate(() => AnchorWidgets.runAll());
    assert(!await page.evaluate(() => calls.some(c => c.cmd === 'nested')), 'no recursive batch');

    // Strict one-time decoding; #/? are command text, never URL components.
    const command = "printf '%s\\n' '雪 %20 #? & (x)'";
    await page.evaluate(command => {
      AnchorWidgets.configure({autoRun: false});
      const p = document.createElement('p'), a = document.createElement('a');
      a.id = 'encoded'; a.href = 'cmd:' + encodeURIComponent(command); a.textContent = '编码';
      p.append(a); document.body.append(p);
    }, command);
    await page.waitForSelector('#encoded.aw-label'); await page.click('#encoded');
    await page.waitForFunction(expected => calls.some(c => c.cmd === expected), command);

    // Running clicks deduplicate; hiding does not cancel or reveal on completion.
    await page.evaluate(() => {
      const p = document.createElement('p'); p.innerHTML = '<a id="hold" href="cmd:hold">慢命令</a>';
      document.body.append(p);
    });
    await page.waitForSelector('#hold.aw-label'); await page.click('#hold'); await page.click('#hold');
    assert.equal(await page.evaluate(() => calls.filter(c => c.cmd === 'hold').length), 1);
    await page.locator('#hold + .aw-controls button').first().click();
    await page.evaluate(() => release());
    await page.waitForFunction(() => !document.querySelector('#hold').hasAttribute('aria-busy'));
    assert.equal(await page.locator('#hold + .aw-controls button').first().getAttribute('aria-expanded'), 'false');

    for (const cmd of ['http-error', 'bad-stream']) {
      await page.evaluate(cmd => {
        const p = document.createElement('p'); p.innerHTML = `<a id="failure" href="cmd:${cmd}">失败</a>`;
        document.querySelector('#failure')?.closest('p').remove(); document.body.append(p);
      }, cmd);
      await page.waitForSelector('#failure.aw-label'); await page.click('#failure');
      await page.waitForFunction(() => document.querySelector('#failure + .aw-controls .aw-status').textContent === '加载失败');
    }

    await page.click('#preview'); await page.waitForSelector('.aw-output iframe');
    await page.waitForFunction(() => !document.querySelector('#preview').hasAttribute('aria-busy'));
    assert.equal(iframeLoads, 1);
    const frame = await page.locator('.aw-output iframe').elementHandle();
    await page.locator('#preview + .aw-controls button').click();
    await page.locator('#preview + .aw-controls button').click();
    assert.equal(iframeLoads, 1, 'hide/show never reloads iframe');
    assert(await frame.evaluate(f => f.isConnected), 'same iframe retained');
    assert((await frame.getAttribute('src')).includes('?q=%2F#part'), 'URL encoding preserved');
    await page.click('#preview');
    await page.waitForFunction(() => !document.querySelector('#preview').hasAttribute('aria-busy'));
    assert.equal(iframeLoads, 2, 'label explicitly reloads iframe');
    assert.deepEqual(errors, []);
    console.log('PASS standalone HTML, dynamic DOM, tables, encoding, manual/batch, visibility, streams, iframe lifecycle');

    // Actual Markdown view and editor wiring; all IO stays in fixture routes.
    const mdPage = await browser.newPage();
    mdPage.on('pageerror', e => errors.push(e.message));
    const requests = [];
    const fixture = '# Dashboard\n\n[刷新](<cmd:echo snake_case>)\n\n[变更](<cmd:!change>)\n\n| 操作 |\n|---|\n| [表内](cmd:cell) |\n\n${date}\n';
    await mdPage.route('http://fixture.test/**', async route => {
      const req = route.request(), url = new URL(req.url());
      if (req.method() === 'POST') {
        const args = Object.fromEntries(new URLSearchParams(req.postData()));
        if (url.pathname.endsWith('/sh.py')) {
          requests.push({path: url.pathname, ...args});
          return route.fulfill({body: '1| 名称 |\n1|---|\n1| fixture |\n'});
        }
        return route.fulfill({body: fixture});
      }
      if (url.pathname === '/worker/example.md') return route.fulfill({contentType: 'text/html',
        body: view.replace('$ARGS_JSON', JSON.stringify({src: '/example.md'}))});
      if (url.pathname.startsWith('/w/')) {
        const file = path.join(root, url.pathname.slice(3));
        return route.fulfill({contentType: file.endsWith('.js') ? 'application/javascript' : 'text/css', body: fs.readFileSync(file)});
      }
      return route.fulfill({status: 404, body: 'unexpected fixture request'});
    });
    await mdPage.goto('http://fixture.test/worker/example.md?_v=autorun');
    await mdPage.waitForSelector('#doc .aw-result table');
    assert.equal(await mdPage.locator('#doc .aw-label').count(), 2);
    assert.equal(requests.length, 1);
    assert.deepEqual([requests[0].path, requests[0].src, requests[0].cmd],
      ['/worker/w/ext/shell/rpc/sh.py', '/example.md', 'echo snake_case']);
    assert((await mdPage.locator('#doc').innerText()).includes('${date}'));
    await mdPage.evaluate(() => renderDoc('# Saved\n\n[刷新](cmd:date)'));
    await mdPage.waitForFunction(() => !document.querySelector('#doc .aw-label').hasAttribute('aria-busy'));
    assert.equal(requests.length, 2, 'save/rerender autoruns once');
    assert.equal(await mdPage.locator('#doc .aw-output').count(), 1);
    assert.deepEqual(errors, []);
    console.log('PASS actual Markdown view, proxy source, autorun, table exclusion, old syntax literal, rerender');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
