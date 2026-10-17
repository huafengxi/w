// Persistent footer regressions, loaded by chat_frontend.cjs.
module.exports = ({test,harness,assert,html,emptyBaseline}) => {
test('footer is below the input; queue, slash and attachments remain above it; actions reuse slash handlers', () => {
 const order=['stream','slashNotice','attachBar','queuedPanel','inputbar','chatFooter'];
 for(let i=1;i<order.length;i++) assert(html.indexOf(`id="${order[i-1]}"`)<html.indexOf(`id="${order[i]}"`));
 assert.match(html,/id="imageButton"[^>]*onclick="handleSlash\('\/image'\)"/);
 assert.match(html,/id="clearButton"[^>]*onclick="handleSlash\('\/clear'\)"/);
 assert.match(html,/id="deleteButton"[^>]*onclick="handleSlash\('\/delete'\)"/);
 assert.match(html,/id="sendButton"[^>]*onclick="sendPrompt\(\)"/);
 assert.doesNotMatch(html,/id="chatStatus"[^>]*onclick=/);
});

test('delete footer action follows /delete availability and disables after deletion', async () => {
 const other=harness();other.run(`attached='/test.jsonl';setChatStatus('ready');`);
 assert.equal(other.elements.get('deleteButton').classList.contains('hidden'),true);
 assert.equal(other.elements.get('deleteButton').disabled,true);
 const h=harness('/run/sessiond/fixture.agent');
 h.run(`setChatStatus('ready');`);
 assert.equal(h.elements.get('deleteButton').classList.contains('hidden'),false);
 assert.equal(h.elements.get('deleteButton').disabled,true,'no attached session');
 h.run(`attached='/run/sessiond/fixture.jsonl';setChatStatus('ready');`);
 assert.equal(h.elements.get('deleteButton').disabled,false);
 h.run(`healBusy=true;paintChatStatus();`);assert.equal(h.elements.get('deleteButton').disabled,true);
 await h.run(`(async()=>{healBusy=false;globalThis.calls=[];
  api=async(op,x)=>{calls.push({op,x});return {ok:true};};await doDelete();})()`);
 assert.equal(h.run('calls[0].op'),'delete_session');assert.equal(h.run('calls[0].x.confirm'),'1');
 assert.equal(h.run('sessionGone'),true);assert.equal(h.elements.get('deleteButton').disabled,true);
});

test('send action never becomes an indicator; busy sends queue and image picker preserves the draft', async () => {
 const h=harness();
 h.run(`attached='/test.jsonl';setChatStatus('ready');paintSendButton();`);
 assert.equal(h.elements.get('sendButton').disabled,true);
 await h.run(`globalThis.sent=[];api=async(op,x)=>{sent.push(JSON.parse(x.cmd));return {ok:true};};
  $('promptInput').value='first';sendPrompt();$('promptInput').value='second';sendPrompt();`);
 assert.deepEqual(Array.from(h.run('sent.map(c=>c.type)')),['prompt','follow_up']);
 assert.equal(h.elements.get('sendButton').disabled,true);
 assert.equal(h.elements.get('sendButton')['aria-label'],'加入待发（follow-up）');
 h.run(`$('promptInput').value='draft';globalThis.picked=0;pickImages=()=>picked++;handleSlash('/image');`);
 assert.equal(h.run('picked'),1);assert.equal(h.elements.get('promptInput').value,'draft');
 h.run(`pendingImages=[{mime:'image/png',b64:'AAAA'}];$('promptInput').value='';renderAttachBar();`);
 assert.equal(h.elements.get('sendButton').disabled,false,'image-only sends enabled');
});

test('context is authoritative, unknown after compaction/missing data, and updates without opening slash help', () => {
 const h=harness();
 h.run(`ctxStatsRequestId='one';handleCmdResponse({id:'one',command:'get_session_stats',success:true,
  data:{contextUsage:{tokens:42000,contextWindow:200000,percent:21},tokens:{total:999999}}});`);
 assert.equal(h.elements.get('contextUsage').textContent,'ctx≈42k / 200k');
 assert.match(h.elements.get('contextUsage').title,/21%/);
 h.run(`ctxStatsRequestId='two';handleCmdResponse({id:'two',command:'get_session_stats',success:true,
  data:{contextUsage:{tokens:180000,contextWindow:200000,percent:90}}});`);
 assert.equal(h.elements.get('contextUsage').dataset.level,'high');
 h.run(`handleEvent({type:'auto_compaction_start'});`);
 assert.equal(h.elements.get('contextUsage').textContent,'ctx≈?');
 h.run(`ctxStatsRequestId='new';handleCmdResponse({id:'two',command:'get_session_stats',success:true,data:{contextUsage:{tokens:180000}}});`);
 assert.equal(h.elements.get('contextUsage').textContent,'ctx≈?','old request cannot restore pre-compaction context');
 h.run(`handleCmdResponse({id:'new',command:'get_session_stats',success:true,data:{contextUsage:{tokens:null,contextWindow:200000,percent:null}}});`);
 assert.equal(h.elements.get('contextUsage').textContent,'ctx≈? / 200k');
 h.run(`ctxStatsRequestId='missing';handleCmdResponse({id:'missing',command:'get_session_stats',success:true,data:{tokens:{total:999999}}});`);
 assert.equal(h.elements.get('contextUsage').textContent,'ctx≈?','never substitute accumulated session tokens');
});

test('context query coalesces, ignores old timeout/reply, fails to unknown, and refreshes at message/settled boundaries', async () => {
 const h=harness();
 await h.run(`attached='/test.jsonl';sendCtxStats=realSendCtxStats;globalThis.calls=[];
  api=async(op,x)=>{calls.push(JSON.parse(x.cmd));return {ok:true};};
  sendCtxStats();sendCtxStats();`);
 assert.equal(h.run('calls.length'),1);
 h.run(`globalThis.old=calls[0].id;handleCmdResponse({id:old,command:'get_session_stats',success:true,
  data:{contextUsage:{tokens:42000,contextWindow:200000,percent:21}}});`);
 h.drainTimers();assert.equal(h.elements.get('contextUsage').textContent,'ctx≈42k / 200k');
 h.run(`handleEvent({type:'message_end',message:{role:'assistant',content:'answer'}});`);
 assert.equal(h.run('calls.length'),2);
 h.run(`handleCmdResponse({id:calls[1].id,command:'get_session_stats',success:true,data:{contextUsage:{tokens:43000,contextWindow:200000}}});
  handleEvent({type:'agent_settled'});`);
 assert.equal(h.run('calls.length'),3);h.drainTimers();
 assert.equal(h.elements.get('contextUsage').textContent,'ctx≈?');
 await h.run(`(async()=>{api=async()=>{throw Error('fixture disconnected');};sendCtxStats();await Promise.resolve();await Promise.resolve();})()`);
 assert.equal(h.run('ctxStatsInFlight'),false);
});

test('message completion supersedes an in-flight context read; late pre-message usage cannot win', () => {
 const h=harness();
 h.run(`attached='/test.jsonl';sendCtxStats=realSendCtxStats;globalThis.calls=[];
  api=async(op,x)=>{calls.push(JSON.parse(x.cmd));return {ok:true};};sendCtxStats();
  handleEvent({type:'message_end',message:{role:'assistant',content:'answer'}});
  handleCmdResponse({id:calls[0].id,command:'get_session_stats',success:true,data:{contextUsage:{tokens:1,contextWindow:200000}}});`);
 assert.equal(h.run('calls.length'),2);
 assert.equal(h.run('curCtx'),null);
 h.run(`handleCmdResponse({id:calls[1].id,command:'get_session_stats',success:true,data:{contextUsage:{tokens:43000,contextWindow:200000}}});`);
 h.drainTimers();assert.equal(h.elements.get('contextUsage').textContent,'ctx≈43k / 200k');
});

test('socket clear keeps the existing control/heal route instead of attaching directly', async () => {
 const h=harness();
 await h.run(`(async()=>{attached='/test.jsonl';globalThis.calls=[];globalThis.heals=[];
  api=async op=>{calls.push(op);return op==='status'?{mode:'socket'}:{ok:true};};
  heal=async reason=>heals.push(reason);await doClear();})()`);
 assert.equal(h.run('calls.join()'),'status,clear');
 assert.equal(h.run('heals.join()'),'clear (agentd control)');
});

test('clear uses the existing clear/attach flow once, preserves draft, resets context and excludes duplicate clicks', async () => {
 const h=harness();
 await h.run(`(async()=>{attached='/test.jsonl';$('promptInput').value='unsent draft';
  curCtx={tokens:42000,contextWindow:200000};paintContextUsage();globalThis.calls=[];let resolve;
  api=(op)=>{calls.push(op);if(op==='status')return new Promise(r=>resolve=r);
   return Promise.resolve(op==='clear'?{ok:true,gen:2}:${emptyBaseline});};
  const p=doClear();await doClear();resolve({mode:'stdio'});await p;})()`);
 assert.equal(h.run('calls.join()'),'status,clear,attach');
 assert.equal(h.elements.get('promptInput').value,'unsent draft');
 assert.equal(h.elements.get('contextUsage').textContent,'ctx≈?');
 assert.equal(h.run('healBusy'),false);
 assert.equal(h.elements.get('clearButton').disabled,false);
});

test('failed clear does not wipe history/context or fabricate a successful baseline', async () => {
 const h=harness();
 await h.run(`(async()=>{attached='/test.jsonl';curCtx={tokens:42000,contextWindow:200000};paintContextUsage();
  globalThis.calls=[];api=async op=>{calls.push(op);return op==='status'?{mode:'stdio'}:{ok:false,error:'fixture denied'};};await doClear();})()`);
 assert.equal(h.run('calls.join()'),'status,clear');assert.equal(h.elements.get('contextUsage').textContent,'ctx≈42k / 200k');
 assert.equal(h.run('healBusy'),false);
});
};
