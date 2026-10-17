// Real HTML/CSS/script in Chromium with isolated API/SSE fixtures (no live session).
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const html=fs.readFileSync(path.join(__dirname,'../ext/sessiond/view/index.html'),'utf8');
const source=html.slice(html.indexOf('"use strict";'),html.indexOf('// ---------------- 启动：'));
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_BIN});
 const errors=[];
 try {
  const page=await browser.newPage({viewport:{width:390,height:844}});
  page.on('pageerror',e=>errors.push(e.message));
  await page.setContent(html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,''));
  await page.addScriptTag({content:source});
  await page.evaluate(()=>{
   attached='/fixture.jsonl';setChatStatus('ready');window.calls=[];
   api=async(op,x)=>{calls.push({op,x});return op==='queue_rpc'?{ok:true,data:{steering:['returned one'],followUp:['returned two']}}:{ok:true};};
   $('promptInput').value='draft';queueState={steering:['one'],followUp:['two']};renderQueuedPanel();
   renderUserMessage({content:'historical user text'},'stable-id');
  });
  // Preserve existing queue, history-edit and image-link behavior.
  await page.getByRole('button',{name:'全部撤回',exact:true}).click();
  assert.equal(await page.locator('#promptInput').inputValue(),'draft\n\nreturned one\n\nreturned two');
  const draft=await page.locator('#promptInput').inputValue();
  const bubble=page.locator('.role-user');
  assert.equal(await bubble.locator('button, svg').count(),0);
  assert.deepEqual(await bubble.evaluate(e=>[getComputedStyle(e).paddingLeft,getComputedStyle(e).paddingRight]),['10px','10px']);
  await bubble.click({position:{x:20,y:14}});
  assert.equal(await page.locator('#promptInput').inputValue(),'historical user text');
  await page.evaluate(()=>{handleEvent({type:'agent_start'});handleEvent({type:'agent_end'});});
  assert(await page.locator('#editSubmit').isDisabled());
  assert(await page.locator('#sendButton').isDisabled(),'history edits never queue');
  await page.locator('#editCancel').click();
  assert.equal(await page.locator('#promptInput').inputValue(),draft);
  assert(!(await page.locator('#sendButton').isDisabled()));
  await page.evaluate(()=>{
   window.openedImages=[];window.open=url=>openedImages.push(url);
   renderUserMessage({content:[{type:'image',data:'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jWZkAAAAASUVORK5CYII=',mimeType:'image/png'}]},'image-id');
  });
  await page.locator('.role-user .msg-img').click();
  assert.equal(await page.evaluate(()=>openedImages.length),1);
  assert.equal(await page.evaluate(()=>historyEdit),null);
  await page.evaluate(()=>{handleEvent({type:'agent_settled'});window.sent=[];api=async(op,x)=>{if(op==='cmd')sent.push(JSON.parse(x.cmd));return {ok:true};};});
  assert.equal(await page.locator('#chatStatusText').innerText(),'就绪');
  await page.locator('#promptInput').fill('click to send');await page.locator('#sendButton').click();
  await page.locator('#promptInput').fill('click while busy');await page.locator('#sendButton').click();
  assert.deepEqual(await page.evaluate(()=>sent.map(c=>[c.type,c.message])),[['prompt','click to send'],['follow_up','click while busy']]);
  assert(await page.locator('#sendButton').isDisabled(),'empty input disables submission');
  assert.equal(await page.locator('#sendButton').innerText(),'↑');
  // The + button uses the real /image file chooser; it does not submit the draft.
  await page.locator('#promptInput').fill('keep this draft');
  const chooser=page.waitForEvent('filechooser');await page.locator('#imageButton').click();
  await (await chooser).setFiles({name:'fixture.png',mimeType:'image/png',buffer:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jWZkAAAAASUVORK5CYII=','base64')});
  await page.waitForFunction(()=>pendingImages.length===1);
  assert.equal(await page.locator('#promptInput').inputValue(),'keep this draft');
  await page.locator('#promptInput').fill('');assert(!(await page.locator('#sendButton').isDisabled()));
  await page.locator('#sendButton').click();
  assert.equal(await page.evaluate(()=>sent.filter(c=>c.type==='follow_up').at(-1).images.length),1);
  assert.equal(await page.evaluate(()=>pendingImages.length),0);
  await page.locator('#promptInput').fill('single line');
  await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
  const before=await page.locator('#sendButton').boundingBox(),single=await page.locator('#promptInput').boundingBox();
  assert(Math.abs(before.y+before.height/2-single.y-single.height/2)<=1);
  await page.locator('#promptInput').fill('first\nsecond\nthird\nfourth');
  await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
  const after=await page.locator('#sendButton').boundingBox();
  assert.equal(after.x,before.x);assert.equal(after.y+after.height,before.y+before.height,'grows upward without moving footer');
  // Clear button and /clear share the actual clear path. Fake only the network.
  await page.evaluate(()=>{
   window.clearCalls=[];
   api=async op=>{clearCalls.push(op);return op==='status'?{mode:'stdio'}:op==='clear'?{ok:true,gen:2}:{entries:[],leafId:null,watermark:'fixture:0',pendingDialogs:[]};};
   sendGetState=()=>{};sendCtxStats=()=>{};sendStatusQuery=()=>{};refreshLoadedCommands=()=>{};openStream=()=>{};
   setChatStatus('ready');
  });
  await page.locator('#clearButton').click();await page.waitForFunction(()=>!healBusy);
  assert.deepEqual(await page.evaluate(()=>clearCalls),['status','clear','attach']);
  assert.equal(await page.locator('#promptInput').inputValue(),'first\nsecond\nthird\nfourth');
  // Separate page: original api(), fetch SSE decoder and lifecycle/race protections.
  const live=await browser.newPage({viewport:{width:390,height:844}});
  live.on('pageerror',e=>errors.push(e.message));
  await live.setContent(html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,''));await live.addScriptTag({content:source});
  await live.evaluate(()=>{
   window.fixtureState={ok:true,state:'ready',busy:false,pendingDialogs:[]};window.seq=0;window.fixtureCalls=[];
   window.fetch=async(url,opts)=>{
    if(opts.method==='POST') {
     const b=new URLSearchParams(opts.body),op=b.get('op');fixtureCalls.push(op);
     if(op==='chat_state') {
      if(window.deferState)return new Promise(r=>window.resolveState=()=>r(new Response(JSON.stringify(fixtureState))));
      return new Response(JSON.stringify(fixtureState));
     }
     if(op==='cmd')return new Response('{"ok":true}');
     throw Error('Unexpected fixture API '+op);
    }
    return {ok:true,body:new ReadableStream({start(c){window.fixtureStream=c;}})};
   };
   window.emit=e=>fixtureStream.enqueue(new TextEncoder().encode('id: fixture:'+ ++seq+'\ndata: '+JSON.stringify(e)+'\n\n'));
   attached='/isolated.jsonl';openStream();
  });
  const emit=async e=>live.evaluate(e=>window.emit(e),e);
  async function current(label,active) {
   await live.waitForFunction(x=>document.getElementById('chatStatusText').textContent===x,label);
   assert.equal(await live.evaluate(()=>turnActive),active);assert(await live.locator('#chatFooter').isVisible());
   assert.equal(await live.locator('#sendButton').innerText(),'↑');
   const input=await live.locator('#promptInput').boundingBox(),footer=await live.locator('#chatFooter').boundingBox();
   assert(footer.y>=input.y+input.height-1,'status always below composer');
   assert.equal(await live.locator('#chatStatus').getAttribute('aria-label'),label);
  }
  await current('就绪',false);
  for(const type of ['sessiond.session_starting','before_agent_start','agent_start']){await emit({type});await current('启动中',true);}
  assert.equal(await live.locator('.process-group').count(),0);
  await emit({type:'message_start',message:{role:'user',content:'pure text'}});
  await emit({type:'message_start',message:{role:'assistant'}});await current('模型生成中',true);
  await live.waitForTimeout(2700);assert.equal(await live.locator('.process-group, #stream > .waiting').count(),0);
  await emit({type:'message_update',assistantMessageEvent:{type:'text_delta',delta:'Plain answer'}});
  await emit({type:'message_end',message:{role:'assistant',content:'Plain answer',stopReason:'stop'}});
  await emit({type:'turn_end',toolResults:[]});await current('收尾中',true);
  await emit({type:'agent_end'});await current('收尾中',true);
  await emit({type:'agent_settled'});await current('就绪',false);
  assert.equal(await live.locator('.mdtext').innerText(),'Plain answer');assert.equal(await live.locator('.process-group').count(),0);
  await emit({type:'message_start',message:{role:'user',content:'tools'}});
  await emit({type:'message_start',message:{role:'assistant'}});
  await emit({type:'message_update',assistantMessageEvent:{type:'thinking_delta',delta:'Thought'}});
  await live.locator('.process-group').waitFor();assert(await live.locator('.process-group').evaluate(e=>e.open));
  await live.locator('.process-group > summary').click();
  await emit({type:'tool_execution_start',toolCallId:'long',toolName:'bash',args:{command:'fixture'}});await current('工具执行中',true);
  assert.equal(await live.locator('.process-group').evaluate(e=>e.open),false);assert.equal(await live.locator('.pg-label').innerText(),'思考 · 1次工具');
  await emit({type:'extension_ui_request',id:'question',method:'input',title:'Fixture question'});await current('等待用户回答',true);
  await live.locator('#dialogCollapse').click();await emit({type:'sessiond.dialog_resolved',id:'question'});await current('处理中',true);
  await emit({type:'tool_execution_end',toolCallId:'long',result:{content:[]}});await current('处理中',true);
  await emit({type:'message_end',message:{role:'assistant',content:[]}});
  await emit({type:'turn_end',toolResults:[{}]});await emit({type:'message_start',message:{role:'assistant'}});await current('模型生成中',true);
  assert.equal(await live.locator('.process-group').count(),1,'tool continuation shares one group');
  await emit({type:'tool_execution_start',toolCallId:'ask',toolName:'ask_dispatcher'});
  await emit({type:'tool_execution_update',toolCallId:'ask',toolName:'ask_dispatcher',partialResult:{details:{agentdWait:{askId:'fixture',question:'Which?'}}}});await current('等待协调者答复',true);
  await emit({type:'tool_execution_end',toolCallId:'ask',result:{}});await current('处理中',true);
  await emit({type:'message_end',message:{role:'assistant',stopReason:'error',errorMessage:'fixture error'}});
  await emit({type:'agent_end'});await current('收尾中',true);
  await emit({type:'auto_retry_start',attempt:1,maxAttempts:2,errorMessage:'fixture error'});await current('自动重试中',true);
  await emit({type:'auto_retry_end',success:true});await current('处理中',true);
  await emit({type:'auto_compaction_start'});await current('上下文压缩中',true);
  await live.evaluate(()=>fixtureState={ok:true,state:'busy',busy:true,pendingDialogs:[]});
  await emit({type:'auto_compaction_end'});await current('处理中',true);
  await emit({type:'agent_end'});await current('收尾中',true);
  await emit({type:'queue_update',steering:[],followUp:['queued next']});assert.equal(await live.locator('.process-group').count(),1);
  await emit({type:'message_start',message:{role:'user',content:'queued next'}});await current('启动中',true);
  assert.equal(await live.locator('.process-group').count(),1,'no empty group on follow-up admission');
  await live.evaluate(()=>{window.deferState=true;window.pendingRefresh=refreshChatStatus(streamCtrl);});
  await emit({type:'tool_execution_start',toolCallId:'new',toolName:'bash'});await current('工具执行中',true);
  await live.evaluate(async()=>{fixtureState={ok:true,state:'success',busy:false,pendingDialogs:[]};resolveState();await pendingRefresh;window.deferState=false;});
  await current('工具执行中',true);
  await live.evaluate(()=>fixtureStream.error(Error('fixture disconnect')));await current('连接状态未知',true);
  await live.evaluate(()=>{fixtureState={ok:true,state:'busy',busy:true,pendingDialogs:[]};openStream();});await current('处理中',true);
  await live.evaluate(()=>{fixtureState={ok:true,state:'busy',busy:true,pendingDialogs:[{id:'restored',method:'confirm'}]};openStream();});await current('等待用户回答',true);
  await live.evaluate(()=>{fixtureState={ok:true,state:'failed',busy:false,pendingDialogs:[]};openStream();});await current('最终失败',false);
  await emit({type:'agent_start'});await emit({type:'message_end',message:{role:'assistant',stopReason:'aborted'}});
  await emit({type:'agent_settled'});await current('已停止',false);
  if(process.env.CHAT_SCREENSHOT)await live.screenshot({path:process.env.CHAT_SCREENSHOT});
  await require('./chat_process_browser.cjs')({browser,html,source,assert,errors});
  assert.deepEqual(errors,[]);console.log('ALL PASS: Chromium footer, send/queue/images/clear, history editing, real SSE lifecycle and reconnect races');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
