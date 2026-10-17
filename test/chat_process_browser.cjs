// Additional real Chromium/API/SSE fixtures, invoked by chat_browser.cjs.
// No production server, session, credentials or model request is used here.
module.exports = async ({browser, html, source, assert, errors}) => {
 const page = await browser.newPage({viewport:{width:320,height:740}});
 page.on('pageerror',e=>errors.push(e.message));
 await page.setContent(html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,''));
 await page.addScriptTag({content:source});
 await page.evaluate(()=>{
  window.fixtureSeq=0;window.fixtureCalls=[];window.sliceSeen=false;
  window.fixtureState={ok:true,state:'busy',busy:true,pendingDialogs:[]};
  const entries=[];
  const add=(message)=>{const id='entry-'+entries.length;entries.push({id,parentId:entries.at(-1)?.id,type:'message',message});};
  // Enough entries + a deterministic clock to exercise multiple baseline slices.
  for(let i=0;i<35;i++) entries.push({id:'meta-'+i,parentId:entries.at(-1)?.id,type:'session_info'});
  add({role:'user',content:'Historical question'});
  add({role:'assistant',content:[{type:'thinking',thinking:'historical thought'},
   {type:'toolCall',id:'old-tool',name:'read',arguments:{path:'fixture'}}]});
  add({role:'toolResult',toolCallId:'old-tool',content:'historical result'});
  add({role:'assistant',content:[{type:'text',text:'Historical answer'}]});
  add({role:'user',content:'Last text-only question'});
  add({role:'assistant',content:[{type:'text',text:'Last text-only answer'}]});
  window.fixtureDoc={entries,leafId:entries.at(-1).id,watermark:'fixture:0',pendingDialogs:[],queue:{followUp:[],steering:[]}};
  window.fetch=async(url,opts)=>{
   if(opts.method==='POST'){
    const body=new URLSearchParams(opts.body),op=body.get('op');fixtureCalls.push(op);
    if(op==='attach') return new Response(JSON.stringify(fixtureDoc));
    if(op==='chat_state') {
     if(window.deferState) return new Promise(resolve=>window.resolveState=()=>resolve(new Response(JSON.stringify(fixtureState))));
     return new Response(JSON.stringify(fixtureState));
    }
    if(['cmd','status'].includes(op))return new Response(JSON.stringify({ok:true}));
    throw Error('Unexpected fixture API '+op);
   }
   return {ok:true,body:new ReadableStream({start(controller){window.fixtureStream=controller;}})};
  };
  window.emit=event=>fixtureStream.enqueue(new TextEncoder().encode(
   'id: fixture:'+ ++fixtureSeq+'\ndata: '+JSON.stringify(event)+'\n\n'));
  window.originalNow=Date.now;let tick=0;Date.now=()=>tick+=6;
  attach('/fixture.jsonl');
 });
 await page.waitForFunction(()=>batchDepth>0);
 assert.equal(await page.evaluate(()=>currentProcessGroup),null,'no live binding during sliced replay');
 await page.waitForFunction(()=>batchDepth===0 && currentProcessGroup?.statusLabel==='处理中');
 await page.evaluate(()=>Date.now=originalNow);
 const labels=()=>page.locator('.pg-label').allTextContents();
 assert.deepEqual(await labels(),['过程 · 1次工具','过程 · 处理中']);
 assert.equal(await page.locator('.process-group').first().evaluate(e=>e.open),false,'historical replay stays folded');
 assert.equal(await page.locator('.process-group').last().evaluate(e=>e.nextElementSibling.textContent),'Last text-only answer',
  'busy text-only baseline header is before its answer');
 const emit=async event=>{await page.evaluate(e=>window.emit(e),event);};
 await emit({type:'message_start',message:{role:'assistant'}});
 await emit({type:'message_update',assistantMessageEvent:{type:'thinking_delta',delta:'Live continuation'}});
 await page.waitForFunction(()=>currentProcessGroup.n===1);
 assert.equal(await page.locator('.process-group').count(),2,'continuation reuses busy baseline group');
 assert.equal(await page.locator('.process-group').last().evaluate(e=>e.open),true);
 await page.locator('.process-group > summary').last().click();
 await emit({type:'tool_execution_start',toolCallId:'live-tool',toolName:'read',args:{path:'fixture'}});
 await page.waitForFunction(()=>currentProcessGroup.nTools===1);
 assert.equal(await page.locator('.process-group').last().evaluate(e=>e.open),false,'later block respects user collapse');
 assert.equal(await page.locator('.pg-label').first().innerText(),'过程 · 1次工具');
 // Heal/refresh: rebuild the same history under an idle authority. Never label it successful.
 await page.evaluate(async()=>{
  fixtureState={ok:true,state:'success',busy:false,pendingDialogs:[]};
  await heal('fixture idle refresh');
 });
 await page.waitForFunction(()=>batchDepth===0 && chatState==='success');
 assert.deepEqual(await labels(),['过程 · 1次工具']);
 assert.equal(await page.evaluate(()=>currentProcessGroup),null);
 // Optimistic local startup, its real SSE admission, and follow-up queuing.
 await page.locator('#promptInput').fill('Local text fixture');
 await page.locator('#chatStatus').click();
 await page.waitForFunction(()=>currentProcessGroup?.statusLabel==='启动中');
 assert.equal(await page.locator('.process-group').count(),2);
 await page.evaluate(()=>window.optimistic=currentProcessGroup);
 await emit({type:'message_start',message:{role:'user',content:'Local text fixture'}});
 await page.waitForFunction(()=>processAwaitingUser===false);
 assert.equal(await page.evaluate(()=>optimistic===currentProcessGroup),true);
 assert.equal(await page.locator('.process-group').count(),2);
 await page.locator('#promptInput').fill('Queued text fixture');
 await page.locator('#chatStatus').click();
 assert.equal(await page.locator('.process-group').count(),2,'sending follow_up does not grab a header');
 await emit({type:'message_start',message:{role:'assistant'}});
 await emit({type:'message_update',assistantMessageEvent:{type:'text_delta',delta:'Answer must remain unchanged.'}});
 await emit({type:'message_end',message:{role:'assistant',content:[{type:'text',text:'Answer must remain unchanged.'}],stopReason:'stop'}});
 await emit({type:'agent_end'});
 await page.waitForFunction(()=>currentProcessGroup.statusLabel==='收尾中');
 assert.equal(await page.locator('.process-group').count(),2);
 await emit({type:'agent_settled'});
 await page.waitForFunction(()=>currentProcessGroup.statusLabel==='已成功完成');
 await emit({type:'message_start',message:{role:'user',content:'Queued text fixture'}});
 await page.waitForFunction(()=>document.querySelectorAll('.process-group').length===3);
 assert.equal(await page.locator('.pg-label').nth(1).innerText(),'过程 · 已成功完成');
 // A stale authoritative response cannot alter the new current header or bind history.
 await page.evaluate(()=>{window.deferState=true;window.oldRefresh=refreshChatStatus(streamCtrl);});
 await emit({type:'message_start',message:{role:'assistant'}});
 await emit({type:'message_update',assistantMessageEvent:{type:'text_delta',delta:'Second answer.'}});
 await page.waitForFunction(()=>currentProcessGroup.statusLabel==='模型生成中');
 await page.evaluate(async()=>{fixtureState={ok:true,state:'success',busy:false,pendingDialogs:[]};resolveState();await oldRefresh;window.deferState=false;});
 assert.equal(await page.locator('.pg-label').last().innerText(),'过程 · 模型生成中');
 assert.equal(await page.locator('.pg-label').nth(1).innerText(),'过程 · 已成功完成');
 await emit({type:'message_end',message:{role:'assistant',content:[{type:'text',text:'Second answer.'}],stopReason:'stop'}});
 await emit({type:'agent_settled'});
 await page.waitForFunction(()=>currentProcessGroup.statusLabel==='已成功完成');
 assert.equal(await page.locator('.mdtext').nth(2).innerText(),'Answer must remain unchanged.');
 // A live, collapsed long wait label + three real tools exercises narrow headers.
 await emit({type:'message_start',message:{role:'user',content:'Layout wait fixture'}});
 await emit({type:'message_start',message:{role:'assistant'}});
 for(const id of ['layout-1','layout-2']) {
  await emit({type:'tool_execution_start',toolCallId:id,toolName:'read',args:{path:'fixture'}});
  await emit({type:'tool_execution_end',toolCallId:id,toolName:'read',result:{content:[]}});
 }
 await emit({type:'tool_execution_start',toolCallId:'layout-ask',toolName:'ask_dispatcher'});
 await emit({type:'tool_execution_update',toolCallId:'layout-ask',toolName:'ask_dispatcher',partialResult:{details:{agentdWait:{askId:'layout-question',question:'Layout question'}}}});
 await page.waitForFunction(()=>currentProcessGroup.statusLabel==='等待协调者答复' && currentProcessGroup.nTools===3);
 await page.locator('.process-group > summary').last().click();
 assert.equal(await page.locator('.process-group').last().evaluate(e=>e.open),false);
 // Narrow mobile and desktop geometry: compact header stays in the stream, never
 // overlays the answer or composer. No independent status row below the stream.
 for(const viewport of [{width:320,height:740},{width:390,height:844},{width:1440,height:1000}]) {
  await page.setViewportSize(viewport);
  await page.evaluate(()=>scrollStream(true));
  const layout=await page.evaluate(()=>{
   const rect=e=>{const r=e.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height};};
   const input=$('promptInput'),style=getComputedStyle(input),canvas=document.createElement('canvas');
   const ctx=canvas.getContext('2d');
   ctx.font=`${style.fontStyle} ${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
   const placeholder=input.getAttribute('placeholder');
   return {viewport:innerWidth,docWidth:document.documentElement.scrollWidth,
    placeholder,placeholderWidth:ctx.measureText(placeholder).width,
    placeholderAvailable:input.clientWidth-parseFloat(style.paddingLeft)-parseFloat(style.paddingRight),
    stream:rect($('stream')),input:rect($('promptInput')),icon:rect($('chatStatus')),
    groups:[...document.querySelectorAll('.process-group')].map(e=>({box:rect(e),summary:rect(e.querySelector('summary')),label:e.querySelector('.pg-label').textContent})),
    answers:[...document.querySelectorAll('.role-assistant')].map(rect)};
  });
  assert(layout.docWidth<=viewport.width,'no document horizontal overflow');
  assert.equal(layout.placeholder,'输入消息或 /命令');
  assert(layout.placeholderWidth<=layout.placeholderAvailable,'placeholder fits one line, including narrow Android-sized viewports');
  assert.equal(await page.locator('#stream > .waiting').count(),0,'process header is the only in-stream lifecycle hint');
  for(const g of layout.groups) {
   assert(g.box.left>=layout.stream.left && g.box.right<=layout.stream.right+1);
   assert(g.summary.height<=36,'short header is at most two small lines on 320px');
   assert(!/Process details|process blocks?/.test(g.label));
  }
  for(const a of layout.answers) for(const g of layout.groups)
   assert(g.box.bottom<=a.top+1 || a.bottom<=g.box.top+1,'header never overlaps answer');
  assert(layout.input.top>=layout.stream.bottom-1,'composer remains outside the scrollable history');
  assert(layout.icon.left>=layout.input.left && layout.icon.right<=layout.input.right+1);
  assert.equal(await page.locator('#chatStatusText').count(),0);
  if(process.env.CHAT_SCREENSHOT) {
   await page.locator('#flashNotice').waitFor({state:'hidden'});
   const base=process.env.CHAT_SCREENSHOT.replace(/\.png$/,`-process-${viewport.width}`);
   require('node:fs').writeFileSync(base+'.json',JSON.stringify(layout,null,2));
   await page.screenshot({path:base+'.png'});
  }
 }
 console.log('PASS Chromium: sliced history busy/idle/heal, text-only startup/admission/follow-up, frozen terminals/HTTP race, first-block expansion/manual collapse, 320/390/1440px layout');
 await page.close();
};
