// Read-only browser acceptance against caller-supplied existing session pages.
// SESSIOND_TEST_PAGES is a JSON array of paths; WEB_BASE and WEB_AUTH_FILE select the site.
'use strict';
const assert = require('node:assert/strict'), fs = require('node:fs');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
(async () => {
  const credentials = fs.readFileSync(process.env.WEB_AUTH_FILE, 'utf8').trim();
  const colon = credentials.indexOf(':');
  assert(colon > 0);
  const pages = JSON.parse(process.env.SESSIOND_TEST_PAGES);
  assert(pages.length >= 7, 'exercise more than the HTTP/1.1 connection limit');
  const browser = await chromium.launch({executablePath: process.env.CHROMIUM_BIN,
    headless: true, args: ['--no-sandbox']});
  const context = await browser.newContext({httpCredentials: {
    username: credentials.slice(0, colon), password: credentials.slice(colon + 1)}});
  context.setDefaultTimeout(10000);
  const errors = [], measurements = [];
  try {
    let page;
    for (const path of pages) {
      page = await context.newPage();
      page.on('pageerror', error => errors.push(error.message));
      const start = Date.now();
      await page.goto(process.env.WEB_BASE + path, {waitUntil: 'domcontentloaded', timeout: 15000});
      await page.waitForFunction(() => attached && streamAlive && chatState !== 'unknown');
      const state = await page.evaluate(() => ({state:chatState,
        title:document.querySelector('#chatStatus').title,
        worker:!!sharedStreamWorker, fallback:sharedStreamFailed}));
      assert(state.worker && !state.fallback, JSON.stringify(state));
      measurements.push({path, ms:Date.now()-start, ...state});
      console.log(JSON.stringify(measurements.at(-1)));
    }
    const start = Date.now();
    const state = await page.evaluate(() => api('chat_state', {session:attached}));
    const controlMs = Date.now()-start;
    assert(state.ok && typeof state.busy === 'boolean', JSON.stringify(state));
    assert(controlMs < 3000, 'browser control request starved behind event streams');
    assert.deepEqual(errors, []);
    const cdp = await browser.newBrowserCDPSession();
    const {targetInfos} = await cdp.send('Target.getTargets');
    const workers = targetInfos.filter(target=>target.type==='shared_worker').map(target=>target.url);
    assert.equal(workers.length, 1, 'all test pages share one host worker');
    const result = {measurements, controlMs, workers, errors};
    if (process.env.CHAT_MULTIPLEX_EVIDENCE) fs.writeFileSync(process.env.CHAT_MULTIPLEX_EVIDENCE, JSON.stringify(result,null,2));
    console.log('PASS multiple pages retain live status and a responsive browser control connection:', JSON.stringify({controlMs,workers}));
  } finally {await browser.close();}
})().catch(error => {console.error(error);process.exitCode=1;});
