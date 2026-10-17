// Full list refresh after a successful opt-in cmd anchor; isolated browser IO only.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = path.join(__dirname, '..');
const frameView = fs.readFileSync(path.join(root, 'ext/frame/view/iframe.html'), 'utf8');
const mdView = fs.readFileSync(path.join(root, 'ext/markdown/view/markdown.html'), 'utf8');
const markdown = '# Sessions\n\n<input id="draft" value="unsaved">\n\n' +
  '<a id="create" href="cmd:!create" data-frame-refresh="tabs">Create</a>\n\n' +
  '[Ordinary](cmd:!ordinary)\n';
(async () => {
  const browser = await chromium.launch({headless: true, executablePath: process.env.CHROMIUM_BIN});
  try {
    const page = await browser.newPage();
    const errors = []; page.on('pageerror', e => errors.push(e.message));
    let version = 0, mode = 'success', sourceReads = 0, dynamicReads = 0, shellCalls = 0, mdLoads = 0;
    let removeOld = false, empty = false, sourceFailure = false;
    await page.route('http://fixture.test/**', async route => {
      const req = route.request(), url = new URL(req.url());
      if (url.pathname === '/proxy/dashboard.itab') {
        if (url.searchParams.get('v') === 'read') {
          sourceReads++;
          if (sourceFailure) return route.fulfill({status: 503, body: 'unavailable'});
          return route.fulfill({body: 'Sessions /proxy/sessions.md?_v=autorun\nStatic /static.html\n' +
            (version ? 'Extra /extra.html\n' : '') + '@separator\n@dynamic /tabs.py\n@dynamic /tabs2.py\n'});
        }
        return route.fulfill({contentType: 'text/html', body: frameView
          .replace('$QUERY_ARGS', JSON.stringify({src: '/dashboard.itab', mode: 'tab'}))
          .replace('$ARGS_JSON', JSON.stringify({src: '/dashboard.itab'}))});
      }
      if (['/proxy/tabs.py', '/proxy/tabs2.py'].includes(url.pathname)) {
        dynamicReads++;
        return route.fulfill({body: url.pathname.endsWith('tabs2.py') || empty ? '' :
          (removeOld ? '' : (version ? 'Renamed' : 'Old') + ' /chat.html\n') +
          (version ? 'New /new.html\n' : '')});
      }
      if (url.pathname === '/proxy/sessions.md') {
        if (req.method() === 'POST') return route.fulfill({body: markdown});
        mdLoads++;
        return route.fulfill({contentType: 'text/html', body: mdView
          .replace('$ARGS_JSON', JSON.stringify({src: '/sessions.md'}))});
      }
      if (url.pathname === '/proxy/w/ext/shell/rpc/sh.py') {
        shellCalls++;
        const args = new URLSearchParams(req.postData());
        assert.equal(args.get('stream'), '3');
        const cmd = args.get('cmd');
        if (mode === 'http-error') return route.fulfill({status: 403, body: 'denied'});
        if (mode === 'success' && cmd === 'create') version++;
        const terminal = {
          success: '3{"exitcode":0}\n', failure: '3{"exitcode":7}\n', missing: '',
          invalid: '3{"exitcode":"0"}\n', duplicate: '3{"exitcode":0}\n3{"exitcode":0}\n',
          after: '3{"exitcode":0}\n1after-status\n', malformed: '3not-json\n'
        }[mode];
        return route.fulfill({body: '1✅ created (display text is not a success signal)\n2diagnostic\n' + terminal});
      }
      if (url.pathname.startsWith('/w/')) {
        const file = path.join(root, url.pathname.slice(3));
        return route.fulfill({contentType: file.endsWith('.js') ? 'application/javascript' : 'text/css',
          body: fs.readFileSync(file)});
      }
      return route.fulfill({contentType: 'text/html', body: '<input id="chat-draft" value="kept">'});
    });
    await page.goto('http://fixture.test/proxy/dashboard.itab');
    await page.waitForFunction(() => items.length === 3 && currentTab === 0);
    await page.waitForFunction(() => items[0].active.contentDocument?.querySelector('#create.aw-label'));
    const sessions = page.frames().find(f => f.url().includes('/proxy/sessions.md'));
    await sessions.waitForSelector('#create.aw-label');
    assert.equal(shellCalls, 0, 'manual create never autoruns');
    const originalWindow = await page.evaluateHandle(() => items[0].active.contentWindow);
    await sessions.locator('#draft').fill('preserve this input');
    await page.locator('#tab-scroll .tab').nth(2).click();
    await page.waitForFunction(() => items[2].active.contentDocument?.querySelector('#chat-draft'));
    const chat = page.frames().find(f => f.url().endsWith('/chat.html'));
    await chat.waitForSelector('#chat-draft');
    await chat.locator('#chat-draft').fill('chat unsaved');
    await page.locator('#tab-scroll .tab').nth(0).click();
    const call = async selector => {
      await sessions.locator(selector).click();
      await sessions.waitForFunction(() => !document.querySelector('[aria-busy]'));
    };
    for (mode of ['failure', 'missing', 'invalid', 'duplicate', 'after', 'malformed', 'http-error']) {
      await call('#create');
      assert.equal(sourceReads, 1, mode + ': no refresh on failed/invalid command completion');
      assert.equal(dynamicReads, 2);
      assert.equal(await sessions.locator('.aw-error:visible').count(), 1);
    }
    mode = 'success';
    await call('a:has-text("Ordinary")');
    assert.equal(sourceReads, 1, 'non-opt-in command does not notify');
    await page.evaluate(() => {
      window.dispatchEvent(new MessageEvent('message', {origin: 'http://evil.test',
        source: items[0].active.contentWindow, data: {type: 'frame:tabs-changed'}}));
      window.postMessage({type: 'frame:tabs-changed'}, location.origin); // parent is not a registered child
    });
    await page.waitForTimeout(50);
    assert.equal(sourceReads, 1, 'reject foreign origin and unregistered source');
    await call('#create');
    await page.waitForFunction(() => items.length === 5 && items.some(it => it.name === 'New'));
    assert.equal(sourceReads, 2, 're-read the entire itab source once');
    assert.equal(dynamicReads, 4, 'fetch every @dynamic once');
    assert.equal(mdLoads, 1, 'existing child document never reloads');
    assert.equal(await page.evaluate(win => items[0].active.contentWindow === win, originalWindow), true);
    assert.equal(await sessions.locator('#draft').inputValue(), 'preserve this input');
    assert.equal(await chat.locator('#chat-draft').inputValue(), 'chat unsaved');
    assert.equal(await page.evaluate(() => currentTab), 0, 'no auto-selection of the new tab');
    assert.deepEqual(await page.evaluate(() => items.map(it => it.name)), ['Sessions', 'Static', 'Extra', 'Renamed', 'New']);
    assert.equal(await page.locator('.tab-separator').count(), 1);
    assert.equal(await page.locator('#fs-toggle').count(), 1);
    assert.equal(await page.evaluate(() => items[4].active.getAttribute('src')), '', 'new tab stays lazy');
    // A source fetch failure leaves the current complete list untouched.
    sourceFailure = true;
    await page.evaluate(() => refreshTabs());
    assert.equal(await page.evaluate(() => items.length), 5);
    assert.equal(dynamicReads, 4);
    sourceFailure = false;
    // Selection is preserved by URL when earlier entries change; a removed active tab falls back.
    await page.locator('#tab-scroll .tab').nth(3).click();
    await page.evaluate(() => refreshTabs());
    assert.equal(await page.evaluate(() => items[currentTab].link), '/chat.html');
    assert.equal(await chat.locator('#chat-draft').inputValue(), 'chat unsaved');
    removeOld = true;
    await page.evaluate(() => refreshTabs());
    assert.equal(await page.evaluate(() => items[currentTab].name), 'Sessions');
    assert.equal(await page.locator('#iframe-list iframe').count(), 4);
    empty = true;
    await page.evaluate(() => refreshTabs());
    assert.deepEqual(await page.evaluate(() => items.map(it => it.name)), ['Sessions', 'Static', 'Extra']);
    assert.equal(await sessions.locator('#draft').inputValue(), 'preserve this input');
    assert.equal(await page.locator('#fs-toggle').count(), 1);
    assert.deepEqual(errors, []);
    console.log('PASS completion status, success-only notification, origin/source checks, full-list reload, iframe/input/selection retention, rename/removal/empty/failure');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
