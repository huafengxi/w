// Isolated frame-view regression; no deployment endpoints or real sessions.
// PLAYWRIGHT_MODULE=/path/to/playwright CHROMIUM_BIN=/path/to/chromium node test/frame_separator_browser.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = path.join(__dirname, '..');
const template = fs.readFileSync(path.join(root, 'ext/frame/view/iframe.html'), 'utf8');
const source = '# fixture\nStatic /static.html\n  @separator  \n@dynamic /tabs.py\n';
(async () => {
  const browser = await chromium.launch({headless: true, executablePath: process.env.CHROMIUM_BIN});
  try {
    for (const scenario of ['dynamic', 'empty', 'failure', 'static', 'flow', 'narrow']) {
      const page = await browser.newPage({viewport: {width: scenario === 'narrow' ? 360 : 1200, height: 800}});
      const errors = [], requests = [];
      page.on('pageerror', error => errors.push(error.message));
      const mode = scenario === 'flow' ? 'flow' : 'tab';
      const dynamic = !['empty', 'failure', 'static'].includes(scenario);
      await page.route('http://fixture.test/**', async route => {
        const url = new URL(route.request().url());
        requests.push(url.pathname + url.search);
        if (url.pathname === '/proxy/fixture.itab') {
          if (url.searchParams.get('v') === 'read') {
            return route.fulfill({contentType: 'text/plain', body: scenario === 'static'
              ? 'First /static.html\n@separator\nSecond /second.html\n' : source});
          }
          return route.fulfill({contentType: 'text/html', body: template
            .replace('$QUERY_ARGS', JSON.stringify({src: '/fixture.itab', mode}))
            .replace('$ARGS_JSON', JSON.stringify({src: '/fixture.itab'}))});
        }
        if (url.pathname === '/proxy/tabs.py') {
          return route.fulfill({status: scenario === 'failure' ? 503 : 200,
            contentType: 'text/plain', body: dynamic ? 'Dynamic /dynamic.html\n' : ''});
        }
        if (url.pathname.startsWith('/w/')) {
          const file = path.join(root, url.pathname.slice(3));
          return route.fulfill({contentType: file.endsWith('.js') ? 'application/javascript' : 'text/css',
            body: fs.readFileSync(file)});
        }
        if (/^\/(static|second|dynamic)\.html$/.test(url.pathname)) {
          return route.fulfill({contentType: 'text/html', body: '<p>fixture content</p>'});
        }
        return route.fulfill({status: 404, body: 'unexpected fixture request'});
      });
      await page.goto('http://fixture.test/proxy/fixture.itab');
      const expected = dynamic || scenario === 'static' ? 2 : 1;
      await page.waitForFunction(count => items.length === count &&
        (layoutMode === 'flow' || currentTab === 0), expected);
      assert.equal(await page.locator('#iframe-list iframe').count(), expected);
      assert.equal(await page.locator('#check-list input').count(), expected);
      assert.equal(await page.locator('#tab-scroll .tab').count(), expected);
      assert.equal(requests.filter(url => url === '/proxy/tabs.py').length, scenario === 'static' ? 0 : 1);
      assert(!requests.some(url => url.includes('@separator')), 'separator never navigates or fetches');
      if (mode === 'flow') {
        assert.equal(await page.locator('.tab-separator').count(), 0, 'flow ignores separators');
        await page.waitForFunction(() => items.every(it => it.active.src.endsWith(it.link)));
      } else {
        const separator = page.locator('#tab-scroll .tab-separator');
        assert.equal(await separator.count(), 1);
        assert.equal(await separator.isVisible(), true);
        assert.equal(await separator.getAttribute('aria-hidden'), 'true');
        assert.equal(await separator.getAttribute('tabindex'), null);
        assert.equal(await separator.evaluate(node => node.onclick), null);
        const box = await separator.boundingBox();
        assert(box.width >= 1 && box.height > 0, 'visible vertical line even on narrow screens');
        assert.deepEqual(await page.locator('#tab-scroll > *').evaluateAll(nodes => nodes.map(n => n.className)),
          expected === 2 ? ['tab selected', 'tab-separator', 'tab'] : ['tab selected', 'tab-separator']);
        assert.equal(await page.evaluate(() => items[0].active.src.endsWith('/static.html')), true);
        if (expected === 2) {
          assert.equal(await page.evaluate(() => iframeLoaded(items[1])), false, 'second tab remains lazy');
          await page.locator('#tab-scroll .tab').nth(1).click();
          assert.equal(await page.evaluate(() => currentTab), 1, 'separator does not offset tab index');
          assert.equal(await page.evaluate(() => items[1].active.src.endsWith(items[1].link)), true);
          assert.equal(await separator.isVisible(), true, 'selection preserves separator');
        }
      }
      assert.deepEqual(errors, []);
      await page.close();
      console.log('PASS frame separator:', scenario);
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
