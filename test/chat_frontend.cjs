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
      style: {}, dataset: {}, children: [], textContent: '', value: '',
      get innerHTML() { return this._html || ''; },
      set innerHTML(value) { this._html = value; this.children = []; },
      classList: {add(c) { classes.add(c); }, remove(c) { classes.delete(c); },
        contains(c) { return classes.has(c); }, toggle(c, on) { on ? classes.add(c) : classes.delete(c); }},
      get nextSibling() { const a = this.parentNode?.children || []; return a[a.indexOf(this) + 1] || null; },
      appendChild(n) { return this.insertBefore(n, null); },
      insertBefore(n, anchor) {
        if (n.parentNode) n.parentNode.removeChild(n);
        const i = anchor ? this.children.indexOf(anchor) : -1;
        if (i < 0) this.children.push(n); else this.children.splice(i, 0, n);
        n.parentNode = this; return n;
      },
      removeChild(n) { this.children = this.children.filter(x => x !== n); n.parentNode = null; },
      remove() { if (this.parentNode) this.parentNode.removeChild(this); },
      setAttribute(k, v) { this[k] = v; },
      focus() {}, scrollIntoView() {}, addEventListener() {}, querySelectorAll() { return []; },
      getBoundingClientRect() { return {top: 100}; },
    };
  }
  const document = {
    hidden: false,
    getElementById(id) { if (!elements.has(id)) elements.set(id, element()); return elements.get(id); },
    createElement: element,
    createTextNode(text) { const n = element(); n.textContent = text; return n; },
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

for (const message of ['important message', '']) {
  test(`failed prompt retains recoverable payload (${message ? 'text + images' : 'images only'})`, async () => {
    const h = harness();
    await h.run(`(async()=>{
      attached='/test.jsonl';globalThis.sent=[];
      api=async(op,extra)=>{sent.push(JSON.parse(extra.cmd));throw new Error('network unavailable');};
      pendingImages=[{mime:'image/png',b64:'AAAA',dataUrl:'data:image/png;base64,AAAA'}];
      $('promptInput').value=${JSON.stringify(message)};sendPrompt();await Promise.resolve();
    })()`);
    assert.equal(h.run('Object.values(pendingSends)[0].rejected'), true);
    const panel = h.elements.get('queuedPanel');
    assert.equal(panel.classList.contains('hidden'), false);
    assert.equal(panel.children[1].children[1].textContent.includes('1 张图片'), true);
    assert.equal(panel.children[1].children[2].textContent, '恢复到输入框');
    // Baseline healing must not discard a failed payload. Restore appends to a newer draft.
    h.run(`applyBaseline(${emptyBaseline},false);
      $('promptInput').value='new draft';
      pendingImages=[{mime:'image/jpeg',b64:'BBBB',dataUrl:'data:image/jpeg;base64,BBBB'}];`);
    panel.children[1].children[2].onclick();
    assert.equal(h.elements.get('promptInput').value, 'new draft' + (message ? '\n' + message : ''));
    assert.equal(h.run('pendingImages.length'), 2);
    assert.equal(h.run('pendingImages[1].b64'), 'AAAA');
    assert.equal(h.run('Object.keys(pendingSends).length'), 0);
    assert.equal(h.run('sent.length'), 1, 'restore must not resend automatically');
  });
}

test('RPC rejection exposes a normal prompt and can restore its text', () => {
  const h = harness();
  h.run(`attached='/test.jsonl';globalThis.sent=[];
    api=async(op,extra)=>{sent.push(JSON.parse(extra.cmd));return {ok:true};};
    $('promptInput').value='rejected text';sendPrompt();
    handleCmdResponse({id:sent[0].id,command:'prompt',success:false,error:'permission denied'});`);
  assert.equal(h.elements.get('queuedPanel').classList.contains('hidden'), false);
  assert.equal(h.run('Object.values(pendingSends)[0].error'), 'permission denied');
  h.run('restoreFailedSend(sent[0].id);restoreFailedSend(sent[0].id);');
  assert.equal(h.elements.get('promptInput').value, 'rejected text', 'restore is single-use');
  assert.equal(h.run('sent.length'), 1);
});

test('sending before attach leaves a recoverable failure instead of losing the draft', () => {
  const h = harness();
  h.run(`$('promptInput').value='early message';sendPrompt();`);
  assert.equal(h.run('Object.values(pendingSends)[0].rejected'), true);
  assert.equal(h.elements.get('queuedPanel').classList.contains('hidden'), false);
  h.run('restoreFailedSend(Object.keys(pendingSends)[0]);');
  assert.equal(h.elements.get('promptInput').value, 'early message');
});

test('successful normal prompts remain absent from the queue panel', () => {
  const h = harness();
  h.run(`attached='/test.jsonl';api=async()=>({ok:true});
    $('promptInput').value='normal message';sendPrompt();renderQueuedPanel();`);
  assert.equal(h.elements.get('queuedPanel').classList.contains('hidden'), true);
});

test('batch recall uses returned text, appends draft, ignores images, no abort', async () => {
  const h = harness();
  await h.run(`(async()=>{attached='/test.jsonl'; $('promptInput').value='draft';
    queueState={steering:['stale'],followUp:['stale2']}; globalThis.calls=[];
    api=async(op,x)=>{calls.push([op,x]);return {ok:true,data:{steering:['s1','s2'],followUp:['f1']}};};
    pendingImages=[{mime:'image/png',b64:'AAAA',dataUrl:'data:image/png;base64,AAAA'}];
    await queueBatch(false);})()`);
  assert.equal(h.run("$('promptInput').value"), 'draft\n\ns1\n\ns2\n\nf1');
  assert.equal(h.run('pendingImages.length'), 1);
  assert.equal(h.run('calls.length'), 1);
  assert.equal(h.run('calls[0][1].action'), 'clear_queue');
});

test('batch buttons exclude repeated clicks while clear or steer is pending', async () => {
  const h = harness();
  await h.run(`(async()=>{attached='/test.jsonl';globalThis.calls=[];let resolve;
    queueState={steering:['s'],followUp:[]};
    api=(op,x)=>{calls.push(x.action);return new Promise(r=>resolve=r);};
    const p=queueBatch(false);await queueBatch(true);
    if (!$('queuedPanel').children[0].children.every(b=>b.disabled)) throw Error('buttons enabled');
    resolve({ok:true,data:{steering:['s'],followUp:[]}});await p;})()`);
  assert.equal(h.run('calls.join()'), 'clear_queue');
  assert.equal(h.run('queueBatchBusy'), false);
});

test('batch steer respects per-queue order and recovers failed plus unsent texts', async () => {
  const h = harness();
  await h.run(`(async()=>{attached='/test.jsonl';$('promptInput').value='draft';globalThis.calls=[];
    api=async(op,x)=>{calls.push(x);if(x.action==='clear_queue')return {ok:true,data:{steering:['s1','s2'],followUp:['f1','f2']}};
      return x.message==='s2'?{ok:false,error:'reject'}:{ok:true};};
    await queueBatch(true);})()`);
  assert.equal(h.run('calls.map(x=>x.message||x.action).join()'), 'clear_queue,s1,s2');
  assert.equal(h.run("$('promptInput').value"), 'draft\n\ns2\n\nf1\n\nf2');
  assert.equal(h.run('queueBatchBusy'), false);
});

test('batch steer success forwards each returned text once and preserves draft', async () => {
  const h = harness();
  await h.run(`(async()=>{attached='/test.jsonl';$('promptInput').value='draft';globalThis.calls=[];
    api=async(op,x)=>{calls.push(x);return x.action==='clear_queue'?{ok:true,data:{steering:['a','b'],followUp:['c']}}:{ok:true};};
    await queueBatch(true);})()`);
  assert.equal(h.run('calls.map(x=>x.message||x.action).join()'), 'clear_queue,a,b,c');
  assert.equal(h.run("$('promptInput').value"), 'draft');
});

test('clear rejection or network failure does not fabricate recovered queue text', async () => {
  for (const fail of ["return {ok:false,error:'unsupported'}", "throw Error('network')"]) {
    const h = harness();
    await h.run(`(async()=>{attached='/test.jsonl';$('promptInput').value='draft';queueState={steering:['not-receipted'],followUp:[]};
      api=async()=>{${fail}};await queueBatch(false);})()`);
    assert.equal(h.run("$('promptInput').value"), 'draft');
    assert.equal(h.run('queueState.steering[0]'), 'not-receipted');
    assert.equal(h.run('queueBatchBusy'), false);
  }
});

test('status stays busy across agent_end, tool runs, retry and queued continuation', () => {
  const h = harness();
  for (const event of ["{type:'agent_start'}", "{type:'tool_execution_start',toolCallId:'x',toolName:'bash'}",
    "{type:'agent_end'}", "{type:'auto_retry_start'}", "{type:'agent_start'}", "{type:'agent_end'}"]) {
    h.run(`handleEvent(${event});`);
    assert.equal(h.run('chatState'), 'busy');
    assert.equal(h.run('turnActive'), true);
  }
  h.run("handleEvent({type:'agent_settled'});");
  assert.equal(h.run('chatState'), 'success');
  h.run("handleEvent({type:'queue_update',steering:[],followUp:[]});");
  assert.equal(h.run('chatState'), 'success');
});

test('terminal status distinguishes final error, aborted and waiting, not just color', () => {
  const h = harness();
  h.run("updateChatStatus({type:'agent_start'});updateChatStatus({type:'message_end',message:{role:'assistant',stopReason:'error'}});updateChatStatus({type:'agent_settled'});");
  assert.equal(h.elements.get('chatStatus').textContent, '!');
  assert.equal(h.elements.get('chatStatus')['aria-label'], '最终失败');
  h.run("updateChatStatus({type:'agent_start'});updateChatStatus({type:'message_end',message:{role:'assistant',stopReason:'aborted'}});updateChatStatus({type:'agent_settled'});");
  assert.equal(h.run('chatState'), 'stopped');
  h.run("pendingDialogs={ask:{id:'ask'}};paintChatStatus();");
  assert.equal(h.elements.get('chatStatus').textContent, '?');
  h.run("setChatStatus('unknown');");
  assert.equal(h.elements.get('chatStatus').textContent, '◇');
});

test('authoritative reconnect snapshot restores busy or success without polling', async () => {
  const h = harness();
  for (const state of ['busy','success','failed','stopped','ready']) {
    await h.run(`(async()=>{attached='/test.jsonl';api=async()=>({ok:true,state:'${state}',busy:${state==='busy'},pendingDialogs:[]});await refreshChatStatus();})()`);
    assert.equal(h.run('chatState'), state);
    assert.equal(h.run('turnActive'), state === 'busy');
  }
  await h.run("(async()=>{api=async()=>{throw Error('disconnected')};await refreshChatStatus();})()");
  assert.equal(h.run('chatState'), 'unknown');
});

test('late snapshot cannot overwrite a newer lifecycle event', async () => {
  const h = harness();
  await h.run(`(async()=>{attached='/test.jsonl';let resolve;api=()=>new Promise(r=>resolve=r);
    const p=refreshChatStatus();updateChatStatus({type:'agent_start'});resolve({ok:true,state:'success',busy:false});await p;})()`);
  assert.equal(h.run('chatState'), 'busy');
});

test('manual compaction stays busy then uses authority; idle abort becomes neutral stopped', async () => {
  const h = harness();
  await h.run(`(async()=>{attached='/test.jsonl';api=async()=>({ok:true,state:'ready',busy:false,pendingDialogs:[]});
    handleEvent({type:'compaction_start'});
    if(chatState!=='busy')throw Error('not busy during compaction');
    handleEvent({type:'compaction_end',result:{}});await Promise.resolve();await Promise.resolve();
    handleEvent({type:'response',command:'abort',success:true});})()`);
  assert.equal(h.run('chatState'), 'stopped');
});

test('new queue snapshot and new optimistic send survive an in-flight clear', async () => {
  const h = harness();
  await h.run(`(async()=>{attached='/test.jsonl';let resolve;
    api=()=>new Promise(r=>resolve=r);queueState={steering:['old'],followUp:[]};
    const p=queueBatch(false);pendingSends.new={mode:'queue',text:'new draft message'};
    handleEvent({type:'queue_update',steering:[],followUp:['new draft message']});
    resolve({ok:true,data:{steering:['old'],followUp:[]}});await p;})()`);
  assert.equal(h.run('queueState.followUp[0]'), 'new draft message');
  assert.equal(h.run('pendingSends.new.text'), 'new draft message');
  assert.equal(h.run("$('promptInput').value"), 'old');
});

test('history edit loads text/images, cancellation restores original draft/images', async () => {
  const h = harness();
  await h.run(`(async()=>{attached='/test.jsonl';$('promptInput').value='draft';pendingImages=[{b64:'DRAFT',mime:'image/png'}];
    await beginHistoryEdit({content:[{type:'text',text:'old'},{type:'image',data:'OLD',mimeType:'image/jpeg'}]},'entry-1');})()`);
  assert.equal(h.run("$('promptInput').value"), 'old');
  assert.equal(h.run('historyEdit.entryId'), 'entry-1');
  assert.equal(h.run('pendingImages[0].b64'), 'OLD');
  h.run('cancelHistoryEdit();');
  assert.equal(h.run("$('promptInput').value"), 'draft');
  assert.equal(h.run('pendingImages[0].b64'), 'DRAFT');
});

test('busy or unknown history edit cannot submit or become follow-up', async () => {
  const h = harness();
  await h.run(`(async()=>{attached='/test.jsonl';globalThis.calls=[];api=async(op,x)=>{calls.push(op);return {ok:true};};
    await beginHistoryEdit({content:'old'},'entry-1');$('promptInput').value='modified';
    turnActive=true;setChatStatus('busy');sendPrompt();await submitHistoryEdit();
    turnActive=false;setChatStatus('unknown');await submitHistoryEdit();})()`);
  assert.equal(h.run('calls.length'), 0);
  assert.equal(h.run("$('promptInput').value"), 'modified');
});

test('history submit uses stable entryId and dedicated operation; draft survives success', async () => {
  const h = harness();
  await h.run(`(async()=>{attached='/test.jsonl';$('promptInput').value='draft';globalThis.calls=[];
    api=async(op,x)=>{calls.push([op,x]);return {ok:true};};heal=async()=>{};setChatStatus('ready');
    await beginHistoryEdit({content:'old'},'persistent-id');$('promptInput').value='new';
    await submitHistoryEdit();})()`);
  assert.equal(h.run('calls[0][0]'), 'regenerate');
  assert.equal(h.run('calls[0][1].entryId'), 'persistent-id');
  assert.equal(h.run('calls[0][1].message'), 'new');
  assert.equal(h.run("$('promptInput').value"), 'draft');
  assert.equal(h.run('historyEdit'), null);
});

test('historical rejection preserves edited payload, no automatic follow-up', async () => {
  const h = harness();
  await h.run(`(async()=>{attached='/test.jsonl';setChatStatus('ready');globalThis.calls=0;
    api=async()=>{calls++;return {ok:false,error:'busy'};};await beginHistoryEdit({content:'old'},'entry-1');
    $('promptInput').value='new';await submitHistoryEdit();})()`);
  assert.equal(h.run('calls'), 1);
  assert.equal(h.run('historyEdit.entryId'), 'entry-1');
  assert.equal(h.run("$('promptInput').value"), 'new');
});

function status(h, label, active) {
  assert.equal(h.elements.has('chatStatusText'), false, 'no separate status text is rendered');
  assert.equal(h.run("$('chatStatus').title"), label);
  assert.equal(h.run("$('chatStatus')['aria-label']"), label);
  if (active !== undefined) assert.equal(h.run('turnActive'), active);
}

test('run hooks remain starting until an assistant message actually starts or updates', () => {
  const h = harness();
  for (const type of ['message_start', 'before_agent_start', 'agent_start']) {
    h.run(`handleEvent({type:'${type}',message:{role:'user',content:'fixture'}});`);
    status(h, '启动中', true);
  }
  h.run("handleEvent({type:'message_start',message:{role:'assistant'}});");
  status(h, '模型生成中', true);
  h.run("handleEvent({type:'agent_start'});handleEvent({type:'message_update',assistantMessageEvent:{type:'text_delta',delta:'new'}});");
  status(h, '模型生成中', true);
});

test('pure text turn_end and agent_end retain indicator finalization until settled', () => {
  const h = harness();
  h.run("handleEvent({type:'agent_start'});handleEvent({type:'message_start',message:{role:'assistant'}});");
  status(h, '模型生成中', true);
  h.run("handleEvent({type:'message_end',message:{role:'assistant',content:'answer',stopReason:'stop'}});handleEvent({type:'turn_end',toolResults:[]});");
  status(h, '收尾中', true);
  h.run("handleEvent({type:'agent_end'});");
  status(h, '收尾中', true);
  h.run("handleEvent({type:'agent_settled'});");
  status(h, '已成功完成', false);
});

test('current tools, long silence, ask/answer and notify never use old DOM as a phase', () => {
  const h = harness();
  h.run(`handleEvent({type:'agent_start'});
    handleEvent({type:'tool_execution_start',toolCallId:'a',toolName:'bash'});
    handleEvent({type:'tool_execution_start',toolCallId:'b',toolName:'bash'});`);
  status(h, '工具执行中', true);
  h.run("handleEvent({type:'tool_execution_end',toolCallId:'a',result:{content:[]}});");
  status(h, '工具执行中', true);
  h.run("handleEvent({type:'extension_ui_request',id:'ask',method:'confirm',title:'Continue?'});collapseDialog();");
  status(h, '等待用户回答', true);
  h.run("handleEvent({type:'sessiond.dialog_resolved',id:'ask'});");
  status(h, '处理中', true);
  h.run("handleEvent({type:'tool_execution_end',toolCallId:'b',result:{content:[]}});");
  status(h, '处理中', true);
  h.run("handleEvent({type:'extension_ui_request',method:'notify',message:'notice'});");
  assert.equal(h.run('Object.keys(pendingDialogs).length'), 0);
  status(h, '处理中', true);
  h.run("handleEvent({type:'message_update',assistantMessageEvent:{type:'text_delta',delta:'new'}});");
  status(h, '模型生成中', true);
});

test('structured current ask_dispatcher wait lasts until its tool ends, not a historical text hint', async () => {
  const h = harness();
  h.run(`attached='/test.jsonl';handleEvent({type:'agent_start'});
    handleEvent({type:'tool_execution_start',toolName:'ask_dispatcher',toolCallId:'ask-call'});
    handleEvent({type:'tool_execution_update',toolName:'ask_dispatcher',toolCallId:'ask-call',
      partialResult:{content:[{type:'text',text:'waiting words alone'}]}});`);
  status(h, '工具执行中', true);
  h.run(`handleEvent({type:'tool_execution_update',toolName:'ask_dispatcher',toolCallId:'ask-call',
    partialResult:{details:{agentdWait:{askId:'current-ask',question:'Which option?',recipient:'queue/dispatcher',since:1,deadline:null}}}});
    handleEvent({type:'tool_execution_update',toolName:'bash',toolCallId:'other',partialResult:{}});`);
  status(h, '等待协调者答复', true);
  await h.run(`(async()=>{api=async()=>({ok:true,state:'busy',busy:true,pendingDialogs:[]});await refreshChatStatus();})()`);
  status(h, '等待协调者答复', true);
  h.run("handleEvent({type:'tool_execution_end',toolName:'bash',toolCallId:'other',result:{}});");
  status(h, '等待协调者答复', true);
  h.run("handleEvent({type:'tool_execution_end',toolName:'ask_dispatcher',toolCallId:'ask-call',result:{}});");
  status(h, '处理中', true);
  h.run("handleEvent({type:'agent_settled'});handleEvent({type:'agent_start'});");
  status(h, '启动中', true);
  assert.equal(h.run('statusCoordinatorWaits.size'), 0);
});

test('retry, compression, errors and queued continuation are phases, not terminal guesses', async () => {
  const h = harness();
  h.run(`attached='/test.jsonl';api=()=>new Promise(()=>{});
    handleEvent({type:'agent_start'});
    handleEvent({type:'message_end',message:{role:'assistant',stopReason:'error',errorMessage:'old error'}});
    handleEvent({type:'agent_end',errorMessage:'old error'});`);
  status(h, '收尾中', true);
  h.run("handleEvent({type:'auto_retry_start',attempt:1,maxAttempts:3,errorMessage:'old error'});");
  status(h, '自动重试中', true);
  h.run("handleEvent({type:'auto_retry_end',success:true});");
  status(h, '处理中', true);
  h.run("handleEvent({type:'auto_compaction_start'});");
  status(h, '上下文压缩中', true);
  h.run("handleEvent({type:'auto_compaction_end'});");
  status(h, '处理中', true);
  h.run("handleEvent({type:'agent_start'});handleEvent({type:'message_end',message:{role:'assistant',stopReason:'stop'}});handleEvent({type:'agent_end'});handleEvent({type:'queue_update',followUp:['next']});");
  status(h, '收尾中', true);
  h.run("handleEvent({type:'message_start',message:{role:'user',content:'next'}});");
  status(h, '启动中', true);
  h.run("handleEvent({type:'agent_settled'});");
  status(h, '已成功完成', false);
  assert.equal(h.run('statusTools.size'), 0);
  h.run("handleEvent({type:'agent_start'});handleEvent({type:'auto_retry_end',success:false});handleEvent({type:'agent_settled'});");
  status(h, '最终失败', false);
  h.run("handleEvent({type:'agent_start'});handleEvent({type:'message_end',message:{role:'assistant',stopReason:'aborted'}});handleEvent({type:'agent_settled'});");
  status(h, '已停止', false);
  h.run("handleEvent({type:'agent_start'});");
  status(h, '启动中', true);
});

test('settled clears current dialogs/tools and a new run cannot inherit a stale phase/error', () => {
  const h = harness();
  h.run(`handleEvent({type:'agent_start'});handleEvent({type:'tool_execution_start',toolCallId:'old'});
    handleEvent({type:'extension_ui_request',id:'old-ask',method:'confirm'});
    handleEvent({type:'agent_settled'});`);
  status(h, '已成功完成', false);
  assert.equal(h.run('Object.keys(pendingDialogs).length + statusTools.size'), 0);
  h.run("handleEvent({type:'agent_start'});");
  status(h, '启动中', true);
});

test('metadata get_state cannot terminate the retry/finally window', () => {
  const h = harness();
  h.run(`handleEvent({type:'agent_start'});handleEvent({type:'agent_end'});
    handleEvent({type:'response',command:'get_state',success:true,data:{isStreaming:false,isCompacting:false,pendingMessageCount:0}});`);
  status(h, '收尾中', true);
});

test('each current phase/result/dialog event fences an older authoritative idle response', async () => {
  for (const event of [
    "{type:'message_update',assistantMessageEvent:{type:'text_delta',delta:'x'}}",
    "{type:'message_end',message:{role:'assistant',stopReason:'error'}}",
    "{type:'tool_execution_end',toolCallId:'a',result:{}}",
    "{type:'turn_end',toolResults:[]}", "{type:'agent_end'}",
    "{type:'auto_retry_end',success:true}",
    "{type:'extension_ui_request',id:'ask',method:'confirm'}",
    "{type:'sessiond.dialog_resolved',id:'ask'}",
  ]) {
    const h = harness();
    await h.run(`(async()=>{attached='/test.jsonl';handleEvent({type:'agent_start'});
      showDialog({id:'ask',method:'confirm'});let resolve;api=()=>new Promise(r=>resolve=r);
      const p=refreshChatStatus();handleEvent(${event});
      resolve({ok:true,state:'success',busy:false,pendingDialogs:[]});await p;})()`);
    assert.equal(h.run('chatState'), 'busy', event);
    assert.equal(h.run('turnActive'), true, event);
  }
});

test('latest authoritative request wins, including late errors and replaced connections', async () => {
  const h = harness();
  await h.run(`(async()=>{attached='/test.jsonl';let resolves=[];api=()=>new Promise((resolve,reject)=>resolves.push({resolve,reject}));
    const old=refreshChatStatus(), fresh=refreshChatStatus();
    resolves[1].resolve({ok:true,state:'busy',busy:true,pendingDialogs:[]});await fresh;
    resolves[0].resolve({ok:true,state:'success',busy:false,pendingDialogs:[]});await old;
  })()`);
  status(h, '处理中', true);
  await h.run(`(async()=>{let reject;api=()=>new Promise((r,j)=>reject=j);const oldCtrl=new AbortController();streamCtrl=oldCtrl;
    const p=refreshChatStatus(oldCtrl);streamCtrl=new AbortController();setChatStatus('ready');reject(Error('old connection'));await p;})()`);
  status(h, '就绪');
  await h.run(`(async()=>{let resolves=[];api=()=>new Promise((resolve,reject)=>resolves.push({resolve,reject}));
    const old=refreshChatStatus(),fresh=refreshChatStatus();
    resolves[1].resolve({ok:true,state:'ready',busy:false});await fresh;
    resolves[0].reject(Error('old request'));await old;})()`);
  status(h, '就绪', false);
});

test('local prompt invalidates in-flight idle snapshot, and its late HTTP failure cannot overwrite new events', async () => {
  const h = harness();
  await h.run(`(async()=>{attached='/test.jsonl';let resolve,rejectSend;
    api=(op)=>op==='chat_state'?new Promise(r=>resolve=r):new Promise((r,j)=>rejectSend=j);
    const p=refreshChatStatus();$('promptInput').value='test';sendPrompt();
    resolve({ok:true,state:'success',busy:false});await p;
    handleEvent({type:'agent_start'});rejectSend(Error('old send failure'));await Promise.resolve();await Promise.resolve();})()`);
  status(h, '启动中', true);
});

test('local follow-up admission fences an older idle read without resetting the current phase', async () => {
  const h = harness();
  await h.run(`(async()=>{attached='/test.jsonl';handleEvent({type:'agent_start'});
    let resolve;api=op=>op==='chat_state'?new Promise(r=>resolve=r):Promise.resolve({ok:true});
    const p=refreshChatStatus();$('promptInput').value='queue fixture';sendPrompt();
    resolve({ok:true,state:'success',busy:false,pendingDialogs:[]});await p;})()`);
  status(h, '启动中', true);
  assert.equal(h.run('Object.values(pendingSends)[0].cmd.type'), 'follow_up');
});

test('settled after a busy reconnect reads the final authority rather than inheriting an old outcome', async () => {
  const h = harness();
  await h.run(`(async()=>{attached='/test.jsonl';stopStream();
    api=async()=>({ok:true,state:'busy',busy:true,pendingDialogs:[]});await refreshChatStatus();
    api=async()=>({ok:true,state:'failed',busy:false,pendingDialogs:[]});
    handleEvent({type:'agent_settled'});await Promise.resolve();await Promise.resolve();})()`);
  status(h, '最终失败', false);
});

test('late explicit prompt rejection cannot terminate a newer run', async () => {
  const h = harness();
  await h.run(`(async()=>{attached='/test.jsonl';api=()=>new Promise(()=>{});
    $('promptInput').value='fixture';sendPrompt();const id=Object.keys(pendingSends)[0];
    handleEvent({type:'agent_start'});handleEvent({type:'tool_execution_start',toolCallId:'new'});
    handleEvent({type:'response',command:'prompt',id,success:false,error:'fixture rejection'});})()`);
  status(h, '工具执行中', true);
});

test('SSE disconnect/reconnect calibrates phase unknown, restores live dialogs and idle results', async () => {
  const h = harness();
  let failRead;
  h.context.fetch = async () => ({ok:true,body:{getReader:()=>({read:()=>new Promise((r,j)=>{failRead=j;})})}});
  await h.run(`(async()=>{attached='/test.jsonl';api=async()=>({ok:true,state:'busy',busy:true,pendingDialogs:[]});
    openStream();await Promise.resolve();await Promise.resolve();})()`);
  status(h, '处理中', true);
  h.run("handleEvent({type:'tool_execution_start',toolCallId:'live'});");
  failRead(Error('fixture disconnected'));
  for (let i=0;i<8;i++) await Promise.resolve();
  status(h, '连接状态未知', true);
  h.run(`api=async()=>({ok:true,state:'busy',busy:true,pendingDialogs:[{id:'live-ask',method:'confirm'}]});`);
  h.timers.get(h.run('reconnectTimer'))();
  for (let i=0;i<8;i++) await Promise.resolve();
  status(h, '等待用户回答', true);
  await h.run(`(async()=>{api=async()=>({ok:true,state:'failed',busy:false,pendingDialogs:[]});openStream();
    await Promise.resolve();await Promise.resolve();})()`);
  status(h, '最终失败', false);
  assert.equal(h.run('statusTools.size + Object.keys(pendingDialogs).length'), 0);
  status(h, '最终失败', false);
});

test('busy-only snapshot does not borrow an old error or tool phase; missing authority stays unknown', async () => {
  const h = harness();
  await h.run(`(async()=>{attached='/test.jsonl';handleEvent({type:'agent_start'});
    handleEvent({type:'tool_execution_start',toolCallId:'historical'});lastRenderedErrorMsg='old';
    api=async()=>({ok:true,state:'busy',busy:true,pendingDialogs:[]});await refreshChatStatus();})()`);
  status(h, '处理中', true);
  await h.run(`(async()=>{api=async()=>({ok:true});await refreshChatStatus();})()`);
  status(h, '连接状态未知');
});

test('silent generation keeps process status without a standalone model-waiting line', () => {
  const h = harness();
  h.run("handleEvent({type:'agent_start'});handleEvent({type:'message_start',message:{role:'assistant'}});");
  h.drainTimers();
  status(h, '模型生成中', true);
  assert.equal(h.run('currentProcessGroup.label.textContent'), '过程 · 模型生成中');
  assert.equal(h.elements.get('stream').children.some(n => /(?:^|\s)waiting(?:\s|$)/.test(n.className || '')), false);
  assert.equal(h.run('typeof gapTimerFire'), 'undefined', 'obsolete waiting timer was removed');
});

require('./chat_process_headers.cjs')({test, harness, assert, html, emptyBaseline});

(async () => {
  for (const [name, fn] of tests) { await fn(); console.log('PASS ' + name); }
  console.log('ALL PASS');
})().catch(e => { console.error(e); process.exitCode = 1; });
