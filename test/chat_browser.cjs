// Actual chat HTML/CSS/script in Chromium, deterministic API fixture (no live sessions).
// PLAYWRIGHT_MODULE and CHROMIUM_BIN are caller-supplied read-only dependencies.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const html = fs.readFileSync(path.join(__dirname, '../ext/sessiond/view/index.html'), 'utf8');
const source = html.slice(html.indexOf('"use strict";'), html.indexOf('// ---------------- 启动：'));
(async()=>{
 const browser = await chromium.launch({headless:true, executablePath:process.env.CHROMIUM_BIN});
 try {
  const page = await browser.newPage({viewport:{width:390,height:844}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.setContent(html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,''));
  await page.addScriptTag({content:source});
  await page.evaluate(()=>{
   attached='/fixture.agent';setChatStatus('ready');
   $('promptInput').value='draft';queueState={steering:['one'],followUp:['two']};renderQueuedPanel();
   api=async(op,x)=>op==='queue_rpc'?{ok:true,data:{steering:['returned one'],followUp:['returned two']}}:{ok:true};
   renderUserMessage({content:'historical user text'},'stable-id');
  });
  await page.getByRole('button',{name:'全部撤回',exact:true}).click();
  assert.equal(await page.locator('#promptInput').inputValue(),'draft\n\nreturned one\n\nreturned two');
  const draft=await page.locator('#promptInput').inputValue();
  const bubble=page.locator('.role-user');
  assert.equal(await bubble.locator('button, svg').count(),0,'no edit icon or reserved control');
  assert.deepEqual(await bubble.evaluate(e=>[getComputedStyle(e).paddingLeft,getComputedStyle(e).paddingRight]),
   ['10px','10px'],'no extra left gutter');
  await bubble.click({position:{x:20,y:14}});
  await page.locator('#editBanner').waitFor({state:'visible'});
  assert.equal(await page.locator('#promptInput').evaluate(e=>document.activeElement===e),true);
  assert.equal(await page.locator('#promptInput').inputValue(),'historical user text');
  await page.evaluate(()=>{handleEvent({type:'agent_start'});handleEvent({type:'agent_end'});});
  assert.equal(await page.locator('#chatStatus').getAttribute('data-state'),'busy');
  assert.equal(await page.locator('#editSubmit').isDisabled(),true);
  assert.equal(await page.locator('#promptInput').isDisabled(),false);
  await page.locator('#editCancel').click();
  assert.equal(await page.locator('#promptInput').inputValue(),draft);
  await page.locator('.role-user').click({position:{x:5,y:5}});
  await page.locator('#editBanner').waitFor({state:'visible'});
  assert.equal(await page.locator('#promptInput').inputValue(),'historical user text');
  await page.locator('#editCancel').click();
  assert.equal(await page.locator('#promptInput').inputValue(),draft);
  await page.evaluate(()=>{
   window.openedImages=[];window.open=url=>window.openedImages.push(url);
   renderUserMessage({content:[{type:'image',data:'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jWZkAAAAASUVORK5CYII=',mimeType:'image/png'}]},'image-id');
  });
  await page.locator('.role-user .msg-img').click();
  assert.equal(await page.evaluate(()=>window.openedImages.length),1,'image still opens');
  assert.equal(await page.evaluate(()=>historyEdit),null,'image click does not edit the message');
  await page.evaluate(()=>handleEvent({type:'agent_settled'}));
  assert.equal(await page.locator('#chatStatus').getAttribute('aria-label'),'已成功完成');
  await page.evaluate(()=>{
   window.sentPrompts=[];
   api=async(op,x)=>{if(op==='cmd')window.sentPrompts.push(JSON.parse(x.cmd));return {ok:true};};
  });
  await page.locator('#promptInput').fill('click to send');
  await page.locator('#chatStatus').click();
  assert.equal(await page.locator('#promptInput').inputValue(),'');
  assert.deepEqual(await page.evaluate(()=>window.sentPrompts.map(c=>[c.type,c.message])),[['prompt','click to send']]);
  assert.equal(await page.locator('#chatStatus').getAttribute('data-state'),'busy');
  await page.locator('#promptInput').fill('click while busy');
  await page.locator('#chatStatus').click();
  assert.deepEqual(await page.evaluate(()=>window.sentPrompts.map(c=>[c.type,c.message])),
   [['prompt','click to send'],['follow_up','click while busy']]);
  await page.locator('#chatStatus').click();
  assert.equal(await page.evaluate(()=>window.sentPrompts.length),2,'empty click does not send');
  await page.evaluate(()=>{pendingImages=[{b64:'aW1hZ2U=',mime:'image/png'}];renderAttachBar();});
  await page.locator('#chatStatus').click();
  assert.deepEqual(await page.evaluate(()=>window.sentPrompts[2].images),
   [{type:'image',data:'aW1hZ2U=',mimeType:'image/png'}]);
  assert.equal(await page.evaluate(()=>pendingImages.length),0);
  await page.locator('#promptInput').fill('single line');
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  const before=await page.locator('#chatStatus').boundingBox();
  const singleInput=await page.locator('#promptInput').boundingBox();
  assert(Math.abs((before.y+before.height/2)-(singleInput.y+singleInput.height/2))<=1,
    'indicator vertically centered in single-line input');
  await page.locator('#promptInput').fill('first\nsecond\nthird\nfourth');
  const after=await page.locator('#chatStatus').boundingBox();
  const input=await page.locator('#promptInput').boundingBox();
  assert.equal(after.x,before.x);assert.equal(after.y+after.height,before.y+before.height);
  assert(after.x>=input.x && after.x+after.width<=input.x+input.width);
  assert.equal(await page.locator('#promptInput').evaluate(e=>parseFloat(getComputedStyle(e).paddingRight)),44);
  // Second real page: exercise the original api(), SSE fetch/decoder and rendering.
  // All fetches terminate in this in-memory deterministic fixture, never production.
  const statusPage=await browser.newPage({viewport:{width:390,height:844}});
  statusPage.on('pageerror',e=>errors.push(e.message));
  await statusPage.setContent(html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,''));
  await statusPage.addScriptTag({content:source});
  await statusPage.evaluate(()=>{
   window.fixtureState={ok:true,state:'ready',busy:false,pendingDialogs:[]};
   window.fixtureSeq=0;window.fixtureCalls=[];
   window.fetch=async(url,opts)=>{
    if(opts.method==='POST'){
     const body=new URLSearchParams(opts.body),op=body.get('op');window.fixtureCalls.push(op);
     if(op!=='chat_state') throw Error('Unexpected fixture API '+op);
     if(window.deferState) return new Promise(resolve=>window.resolveState=()=>resolve(new Response(JSON.stringify(window.fixtureState))));
     return new Response(JSON.stringify(window.fixtureState));
    }
    return {ok:true,body:new ReadableStream({start(controller){window.fixtureStream=controller;}})};
   };
   window.emit=event=>window.fixtureStream.enqueue(new TextEncoder().encode(
    'id: fixture:'+ ++window.fixtureSeq+'\ndata: '+JSON.stringify(event)+'\n\n'));
   attached='/isolated.jsonl';openStream();
  });
  async function current(label,active){
   await statusPage.waitForFunction(expected=>document.getElementById('chatStatus').getAttribute('aria-label')===expected,label);
   assert.equal(await statusPage.locator('#chatStatusText').count(),0,'no separate status text row');
   assert.equal(await statusPage.locator('#chatStatus').isVisible(),true);
   assert.equal(await statusPage.locator('#chatStatus').getAttribute('title'),label);
   assert.equal(await statusPage.evaluate(()=>turnActive),active);
   const header=await statusPage.evaluate(()=>currentProcessGroup && currentProcessGroup.label.textContent);
   if(header) assert.equal(header,await statusPage.evaluate(expected=>'过程 · '+expected+(currentProcessGroup.nTools?' · '+currentProcessGroup.nTools+'次工具':''),label),
    'folded process header and indicator use identical lifecycle status');
   const iconBox=await statusPage.locator('#chatStatus').boundingBox();
   const promptBox=await statusPage.locator('#promptInput').boundingBox();
   assert(iconBox.y>=promptBox.y && iconBox.y+iconBox.height<=promptBox.y+promptBox.height+1,
    'indicator remains within composer without an extra status row');
  }
  async function emit(event){await statusPage.evaluate(e=>window.emit(e),event);}
  await current('就绪',false);
  await emit({type:'sessiond.session_starting'});await current('启动中',true);
  await emit({type:'before_agent_start'});await current('启动中',true);
  await emit({type:'agent_start'});await current('启动中',true);
  assert.equal(await statusPage.locator('.process-group').count(),1,'header before first token');
  await emit({type:'message_start',message:{role:'user',content:'pure text fixture'}});
  await current('启动中',true);
  assert.equal(await statusPage.locator('.process-group').count(),1,'real user admission reuses provisional header');
  assert.equal(await statusPage.locator('.process-group .pg-body').evaluate(e=>e.getBoundingClientRect().height),0,'empty header has no shell');
  await statusPage.locator('.process-group > summary').click();
  assert.equal(await statusPage.locator('.process-group').evaluate(e=>e.open),false,'no meaningless empty expansion');
  await emit({type:'message_start',message:{role:'assistant'}});await current('模型生成中',true);
  await statusPage.waitForTimeout(2700); // Longer than the removed 2.5s waiting-line timer.
  await current('模型生成中',true);
  assert.equal(await statusPage.locator('#stream > .waiting').count(),0,'silent generation has no standalone waiting line');
  assert.equal(await statusPage.locator('#stream').innerText().then(t=>t.includes('Waiting for model')),false);
  await emit({type:'message_update',assistantMessageEvent:{type:'text_delta',delta:'Plain answer'}});
  await emit({type:'message_update',assistantMessageEvent:{type:'text_delta',delta:' more'}});
  await current('模型生成中',true);
  await emit({type:'message_end',message:{role:'assistant',content:[{type:'text',text:'Plain answer'}],stopReason:'stop'}});
  await emit({type:'turn_end',toolResults:[]});await current('收尾中',true);
  assert.equal(await statusPage.locator('#stream > .waiting').count(),0);
  await emit({type:'agent_end'});await current('收尾中',true);
  await emit({type:'agent_settled'});await current('已成功完成',false);
  assert.equal(await statusPage.locator('.mdtext').innerText(),'Plain answer more','answer text is unchanged');
  await emit({type:'agent_start'});
  await emit({type:'message_start',message:{role:'user',content:'multi-tool fixture'}});
  await current('启动中',true);
  assert.equal(await statusPage.locator('.process-group').count(),2);
  await emit({type:'message_start',message:{role:'assistant'}});
  await emit({type:'message_update',assistantMessageEvent:{type:'thinking_delta',delta:'fixture thought'}});
  await current('模型生成中',true);
  assert.equal(await statusPage.locator('.process-group').last().evaluate(e=>e.open),true,'first live block expands once');
  await statusPage.locator('.process-group > summary').last().click();
  assert.equal(await statusPage.locator('.process-group').last().evaluate(e=>e.open),false);
  await emit({type:'message_update',assistantMessageEvent:{type:'thinking_delta',delta:' continued'}});
  await emit({type:'tool_execution_start',toolCallId:'long',toolName:'bash',args:{command:'fixture only'}});
  await current('工具执行中',true);
  assert.equal(await statusPage.locator('.process-group').last().evaluate(e=>e.open),false,'tool does not override manual collapse');
  assert.equal(await statusPage.locator('.pg-label').first().innerText(),'过程 · 已成功完成','previous terminal freezes');
  await emit({type:'extension_ui_request',id:'question',method:'input',title:'Fixture question'});
  await current('等待用户回答',true);
  await statusPage.locator('#dialogCollapse').click();await current('等待用户回答',true);
  await emit({type:'sessiond.dialog_resolved',id:'question'});await current('处理中（阶段未知）',true);
  assert.equal(await statusPage.locator('#dialogPill').isVisible(),false);
  await emit({type:'tool_execution_end',toolCallId:'long',toolName:'bash',result:{content:[]}});
  await current('处理中（阶段未知）',true);
  await emit({type:'message_end',message:{role:'assistant',content:[]}});
  await emit({type:'turn_end',toolResults:[{}]});
  await emit({type:'message_start',message:{role:'assistant'}});
  await current('模型生成中',true);
  assert.equal(await statusPage.locator('.process-group').count(),2,'tool continuation assistant subturn shares group');
  await emit({type:'tool_execution_start',toolCallId:'coord-ask',toolName:'ask_dispatcher'});
  await emit({type:'tool_execution_update',toolCallId:'coord-ask',toolName:'ask_dispatcher',partialResult:{
   content:[{type:'text',text:'fixture wait'}],details:{agentdWait:{askId:'fixture-ask',question:'Which option?',recipient:'queue/dispatcher',since:1,deadline:null}}}});
  await current('等待协调者答复',true);
  await emit({type:'tool_execution_end',toolCallId:'coord-ask',toolName:'ask_dispatcher',result:{content:[]}});
  await current('处理中（阶段未知）',true);
  await emit({type:'message_end',message:{role:'assistant',stopReason:'error',errorMessage:'fixture error'}});
  await emit({type:'agent_end'});await current('收尾中',true);
  await emit({type:'auto_retry_start',attempt:1,maxAttempts:2,errorMessage:'fixture error'});
  await current('自动重试中',true);
  await emit({type:'auto_retry_end',success:true});await current('处理中（阶段未知）',true);
  await emit({type:'auto_compaction_start'});await current('上下文压缩中',true);
  await statusPage.evaluate(()=>window.fixtureState={ok:true,state:'busy',busy:true,pendingDialogs:[]});
  await emit({type:'auto_compaction_end'});await current('处理中（阶段未知）',true);
  await emit({type:'agent_start'});await current('启动中',true);
  await emit({type:'agent_end'});await current('收尾中',true);
  await emit({type:'queue_update',steering:[],followUp:['queued next']});await current('收尾中',true);
  assert.equal(await statusPage.locator('.process-group').count(),2,'queued follow-up has no group yet');
  await emit({type:'message_start',message:{role:'user',content:'queued next'}});await current('启动中',true);
  assert.equal(await statusPage.locator('.process-group').count(),3,'admitted follow-up starts a new group');
  assert.equal(await statusPage.locator('.pg-label').nth(1).innerText(),'过程 · 2次工具','unfinished previous turn becomes neutral, not a fabricated terminal');
  // A late idle HTTP result must not erase a newer streaming frame.
  await statusPage.evaluate(()=>{window.deferState=true;window.pendingRefresh=refreshChatStatus(streamCtrl);});
  await emit({type:'tool_execution_start',toolCallId:'new-tool',toolName:'bash'});await current('工具执行中',true);
  await statusPage.evaluate(async()=>{
   window.fixtureState={ok:true,state:'success',busy:false,pendingDialogs:[]};window.resolveState();
   await window.pendingRefresh;window.deferState=false;
  });
  await current('工具执行中',true);
  await statusPage.evaluate(()=>window.fixtureStream.error(Error('fixture disconnect')));
  await current('连接状态未知',true);
  await statusPage.evaluate(()=>{window.fixtureState={ok:true,state:'busy',busy:true,pendingDialogs:[]};openStream();});
  await current('处理中（阶段未知）',true);
  await statusPage.evaluate(()=>{window.fixtureState={ok:true,state:'busy',busy:true,pendingDialogs:[{id:'restored',method:'confirm'}]};openStream();});
  await current('等待用户回答',true);
  await statusPage.evaluate(()=>{window.fixtureState={ok:true,state:'failed',busy:false,pendingDialogs:[]};openStream();});
  await current('最终失败',false);
  await emit({type:'agent_start'});await current('启动中',true);
  await emit({type:'message_end',message:{role:'assistant',stopReason:'aborted'}});
  await emit({type:'agent_settled'});await current('已停止',false);
  if(process.env.CHAT_SCREENSHOT) await statusPage.screenshot({path:process.env.CHAT_SCREENSHOT});
  await require('./chat_process_browser.cjs')({browser,html,source,assert,errors});
  assert.deepEqual(errors,[]);
  console.log('ALL PASS: Chromium mobile layout, icon-free bubble edit/cancel/busy and image links, batch recall, click-to-send prompt/queue/images/empty; compact process/indicator lifecycle status without bottom text, SSE finalization/tools/ask/retry/compaction/queue/terminal and reconnect/HTTP race fixtures');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
