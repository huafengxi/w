// Isolated state regressions against the actual chat script (no server/browser required).
// Run: node w/test/chat_frontend.cjs
// Expect: ALL PASS
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const html = fs.readFileSync(path.join(__dirname, '../ext/sessiond/view/index.html'), 'utf8');
const start = html.indexOf('"use strict";');
const end = html.indexOf('// ---------------- 启动：', start);
assert(start >= 0 && end > start, 'chat script boundaries');
const source = html.slice(start, end);

function harness() {
  const elements = new Map(), listeners = {}, streams = [], timers = new Map(), intervals = [];
  let timerId = 0;
  function element() {
    const classes = new Set();
    return {
      style: {}, dataset: {}, children: [], textContent: '', value: '', innerHTML: '',
      classList: {add(c) { classes.add(c); }, remove(c) { classes.delete(c); },
        contains(c) { return classes.has(c); }, toggle(c, on) { on ? classes.add(c) : classes.delete(c); }},
      appendChild(n) { this.children.push(n); n.parentNode = this; return n; },
      removeChild(n) { this.children = this.children.filter(x => x !== n); n.parentNode = null; },
      remove() { if (this.parentNode) this.parentNode.removeChild(this); },
      addEventListener() {}, querySelectorAll() { return []; },
      getBoundingClientRect() { return {top: 100}; },
    };
  }
  const document = {
    hidden: false,
    getElementById(id) { if (!elements.has(id)) elements.set(id, element()); return elements.get(id); },
    createElement: element,
    addEventListener(name, cb) { listeners[name] = cb; },
    querySelectorAll() { return []; },
  };
  const context = vm.createContext({
    document, window: {addEventListener() {}, innerHeight: 800}, location: {pathname: '/test.jsonl'},
    console, URLSearchParams, AbortController, TextDecoder, Date, Set, Map, Promise,
    setTimeout(cb) { const id = ++timerId; timers.set(id, cb); return id; },
    clearTimeout(id) { timers.delete(id); },
    setInterval(cb) { intervals.push(cb); return intervals.length; }, clearInterval() {},
    requestAnimationFrame() {},
    fetch(url, opts) { streams.push({url, signal: opts.signal}); return new Promise(() => {}); },
    __listeners: listeners,
  });
  const run = code => vm.runInContext(code, context);
  run(source);
  // Disable only notices and unrelated baseline metadata queries, not the state under test.
  run('flash=()=>{}; sysLine=()=>{}; sendGetState=()=>{}; sendCtxStats=()=>{}; sendStatusQuery=()=>{}; refreshLoadedCommands=()=>{};');
  return {run, context, elements, streams, listeners, intervals, timers,
    drainTimers() {
      let guard = 100;
      while (timers.size) {
        assert(guard-- > 0, 'timer loop terminated');
        const [id, cb] = timers.entries().next().value;
        timers.delete(id); cb();
      }
    },
  };
}

const tests = [];
function test(name, fn) { tests.push([name, fn]); }
function activeStreams(h) { return h.streams.filter(s => !s.signal.aborted); }
const emptyBaseline = '{entries:[],leafId:null,watermark:"ns:1",pendingDialogs:[]}';

test('visibility during heal cannot open an early SSE stream', async () => {
  const h = harness();
  await h.run(`(async()=>{
    attached='/test.jsonl'; let resolve;
    api=()=>new Promise(r=>resolve=r);
    const p=heal('test'); __listeners.visibilitychange();
    resolve(${emptyBaseline}); await p;
  })()`);
  assert.equal(h.streams.length, 1);
  assert.equal(activeStreams(h).length, 1);
});

test('repeated openStream aborts every replaced controller', () => {
  const h = harness();
  h.run("attached='/test.jsonl'; openStream(); openStream(); openStream();");
  assert.equal(h.streams.length, 3);
  assert.equal(activeStreams(h).length, 1);
  h.run('stopStream()');
  assert.equal(activeStreams(h).length, 0);
});

test('foreground visibility still reconnects a normal live stream', () => {
  const h = harness();
  h.run("attached='/test.jsonl'; openStream();");
  h.listeners.visibilitychange();
  assert.equal(h.streams.length, 2);
  assert(h.streams[0].signal.aborted);
  assert.equal(activeStreams(h).length, 1);
});

test('visibility and watchdog do not interleave streaming with sliced history', () => {
  const h = harness();
  // Synthetic clock guarantees one entry per slice, without real timing dependencies.
  h.context.Date = {now: (() => { let t = 0; return () => t += 6; })()};
  h.run(`attached='/test.jsonl'; renderEntry=()=>{};
    applyBaseline({entries:[{id:'a'},{id:'b',parentId:'a'},{id:'c',parentId:'b'}],leafId:'c',pendingDialogs:[]},false);`);
  assert.equal(h.run('batchDepth'), 1);
  h.listeners.visibilitychange();
  h.run('streamAlive=true;lastStreamAt=-100000;');
  h.intervals.forEach(cb => cb());
  assert.equal(h.streams.length, 0);
  h.drainTimers();
  assert.equal(h.run('batchDepth'), 0);
  assert.equal(activeStreams(h).length, 1);
});

test('healed empty authoritative dialog snapshot removes stale dialog', () => {
  const h = harness();
  h.run(`attached='/test.jsonl'; showDialog({id:'old',method:'confirm'}); applyBaseline(${emptyBaseline},false);`);
  assert.equal(h.run('Object.keys(pendingDialogs).length'), 0);
  assert.equal(h.elements.get('dialogBox').style.display, 'none');
  assert.equal(h.elements.get('dialogPill').style.display, 'none');
});

test('snapshot preserves valid current answer and collapsed display', () => {
  const h = harness();
  h.run(`showDialog({id:'live',method:'input'}); $('dialogInput').value='unfinished answer';
    showDialog({id:'old',method:'confirm'}); collapseDialog();
    syncPendingDialogs([{id:'live',method:'input'},{id:'new',method:'confirm'}]);`);
  assert.equal(h.run('Object.keys(pendingDialogs).sort().join()'), 'live,new');
  assert.equal(h.elements.get('dialogInput').value, 'unfinished answer');
  assert.equal(h.elements.get('dialogBox').style.display, 'none');
  assert.equal(h.elements.get('dialogPill').style.display, 'flex');
});

test('snapshot replaces stale queue head with an actually pending question', () => {
  const h = harness();
  h.run(`showDialog({id:'old',method:'input'}); syncPendingDialogs([{id:'new',method:'confirm'}]);`);
  assert.equal(h.elements.get('dialogBox').dataset.did, 'new');
  assert.equal(h.elements.get('dialogBox').style.display, 'flex');
  h.run("handleEvent({type:'sessiond.dialog_resolved',id:'new'});");
  assert.equal(h.run('Object.keys(pendingDialogs).length'), 0);
});

for (const [label, status, message] of [
  ['network failure', null, 'Failed to fetch'],
  ['server failure', 503, 'temporarily unavailable'],
  ['unrelated rejection', 403, 'session process stdin unavailable'],
]) {
  test(`dialog answer survives ${label} and can be retried`, async () => {
    const h = harness();
    await h.run(`(async()=>{ attached='/test.jsonl'; showDialog({id:'live',method:'input'});
      $('dialogInput').value='my answer';
      api=async()=>{ const e=new Error(${JSON.stringify(message)}); e.status=${status}; throw e; };
      await replyDialog({value:$('dialogInput').value}); })()`);
    assert.equal(h.run('Object.keys(pendingDialogs).join()'), 'live');
    assert.equal(h.elements.get('dialogBox').style.display, 'flex');
    assert.equal(h.elements.get('dialogInput').value, 'my answer');
    await h.run('(async()=>{api=async()=>({ok:true});await replyDialog({value:$("dialogInput").value});})()');
    assert.equal(h.run('Object.keys(pendingDialogs).length'), 0);
  });
}

test('explicit no-pending-dialog HTTP rejection closes a stale question', async () => {
  const h = harness();
  h.context.fetch = async () => ({ok: false, status: 403,
    json: async () => ({error: "no pending dialog with id 'old'"})});
  await h.run(`(async()=>{attached='/test.jsonl';showDialog({id:'old',method:'input'});await replyDialog({value:'answer'});})()`);
  assert.equal(h.run('Object.keys(pendingDialogs).length'), 0);
  assert.equal(h.elements.get('dialogBox').style.display, 'none');
});

for (const message of ['look at these', '']) {
  test(`busy fallback preserves all images (${message ? 'text + images' : 'images only'})`, () => {
    const h = harness();
    h.run(`attached='/test.jsonl'; globalThis.sent=[];
      api=async(op,extra)=>{sent.push(JSON.parse(extra.cmd));return {ok:true};};
      pendingImages=[{mime:'image/png',b64:'AAAA',dataUrl:'data:image/png;base64,AAAA'},
        {mime:'image/jpeg',b64:'BBBB',dataUrl:'data:image/jpeg;base64,BBBB'}];
      $('promptInput').value=${JSON.stringify(message)};sendPrompt();
      handleCmdResponse({id:sent[0].id,command:'prompt',success:false,error:'Agent is already processing'});`);
    const sent = JSON.parse(h.run('JSON.stringify(sent)'));
    assert.equal(sent.length, 2);
    assert.equal(sent[1].type, 'follow_up');
    assert.notEqual(sent[1].id, sent[0].id);
    assert.equal(sent[1].message, message);
    assert.deepEqual(sent[1].images, sent[0].images);
    assert.equal(h.run('pendingImages.length'), 0);
    assert.equal(h.run('pendingSends[sent[1].id].cmd.images.length'), 2);
    h.run(`resolvePendingByMessage({content:sent[1].message});`);
    assert.equal(h.run('Object.keys(pendingSends).length'), 0);
  });
}

(async () => {
  for (const [name, fn] of tests) { await fn(); console.log('PASS ' + name); }
  console.log('ALL PASS');
})().catch(e => { console.error(e); process.exitCode = 1; });
