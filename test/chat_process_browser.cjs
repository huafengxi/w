// Sliced baseline, process ownership and responsive footer/accessory fixtures.
module.exports=async({browser,html,source,assert,errors})=>{
 const page=await browser.newPage({viewport:{width:320,height:740}});
 page.on('pageerror',e=>errors.push(e.message));
 await page.setContent(html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,''));await page.addScriptTag({content:source});
 await page.evaluate(()=>{
  window.seq=0;window.fixtureCalls=[];window.fixtureState={ok:true,state:'busy',busy:true,pendingDialogs:[]};
  const entries=[],add=message=>{const id='entry-'+entries.length;entries.push({id,parentId:entries.at(-1)?.id,type:'message',message});};
  for(let i=0;i<35;i++)entries.push({id:'meta-'+i,parentId:entries.at(-1)?.id,type:'session_info'});
  add({role:'user',content:'Historical question'});
  add({role:'assistant',content:[{type:'thinking',thinking:'historical thought'},{type:'toolCall',id:'old-tool',name:'read',arguments:{path:'fixture'}}]});
  add({role:'toolResult',toolCallId:'old-tool',content:'historical result'});
  add({role:'assistant',content:[{type:'text',text:'Historical answer'}]});
  add({role:'user',content:'Last text-only question'});add({role:'assistant',content:[{type:'text',text:'Last text-only answer'}]});
  window.fixtureDoc={entries,leafId:entries.at(-1).id,watermark:'fixture:0',pendingDialogs:[],queue:{followUp:[],steering:[]}};
  window.emit=e=>fixtureStream.enqueue(new TextEncoder().encode('id: fixture:'+ ++seq+'\ndata: '+JSON.stringify(e)+'\n\n'));
  window.fetch=async(url,opts)=>{
   if(opts.method==='POST') {
    const b=new URLSearchParams(opts.body),op=b.get('op');fixtureCalls.push(op);
    if(op==='attach')return new Response(JSON.stringify(fixtureDoc));
    if(op==='chat_state') {
     if(window.deferState)return new Promise(r=>window.resolveState=()=>r(new Response(JSON.stringify(fixtureState))));
     return new Response(JSON.stringify(fixtureState));
    }
    if(op==='cmd') {
     const c=JSON.parse(b.get('cmd'));
     if(c.type==='get_session_stats')queueMicrotask(()=>emit({type:'response',id:c.id,command:c.type,success:true,data:{contextUsage:{tokens:42000,contextWindow:200000,percent:21}}}));
     return new Response('{"ok":true}');
    }
    if(op==='commands')return new Response('{"ok":true,"commands":[]}');
    if(op==='status')return new Response('{"ok":true}');
    throw Error('Unexpected fixture API '+op);
   }
   return {ok:true,body:new ReadableStream({start(c){window.fixtureStream=c;}})};
  };
  window.originalNow=Date.now;let tick=0;Date.now=()=>tick+=6;attach('/fixture.jsonl');
 });
 await page.waitForFunction(()=>batchDepth>0);assert.equal(await page.evaluate(()=>currentProcessGroup),null);
 await page.waitForFunction(()=>batchDepth===0 && chatState==='busy' && currentProcessGroup);
 await page.evaluate(()=>Date.now=originalNow);
 const labels=()=>page.locator('.pg-label').allTextContents();
 assert.deepEqual(await labels(),['思考 · 1次工具']);
 assert.equal(await page.locator('.process-group').evaluate(e=>e.open),false,'replay stays folded');
 assert.equal(await page.evaluate(()=>currentProcessGroup.det.isConnected),false,'busy text-only baseline reserves no visible header');
 const emit=async e=>page.evaluate(e=>window.emit(e),e);
 await emit({type:'message_start',message:{role:'assistant'}});
 await emit({type:'message_update',assistantMessageEvent:{type:'thinking_delta',delta:'Live continuation'}});
 await page.waitForFunction(()=>currentProcessGroup.n===1);
 assert.deepEqual(await labels(),['思考 · 1次工具','思考']);
 assert.equal(await page.locator('.process-group').last().evaluate(e=>e.nextElementSibling.textContent),'Last text-only answer','late first block uses the saved turn anchor');
 await page.locator('.process-group > summary').last().click();
 await emit({type:'tool_execution_start',toolCallId:'live-tool',toolName:'read'});
 await page.waitForFunction(()=>currentProcessGroup.nTools===1);
 assert.equal(await page.locator('.process-group').last().evaluate(e=>e.open),false);
 await page.evaluate(async()=>{fixtureState={ok:true,state:'success',busy:false,pendingDialogs:[]};await heal('fixture idle');});
 await page.waitForFunction(()=>batchDepth===0 && chatState==='success');
 assert.deepEqual(await labels(),['思考 · 1次工具']);assert.equal(await page.locator('#chatStatusText').innerText(),'就绪');
 // Optimistic/admitted pure text plus follow-up never mounts a new empty process.
 await page.locator('#promptInput').fill('Local text');await page.locator('#sendButton').click();
 await page.evaluate(()=>window.optimistic=currentProcessGroup);
 assert.equal(await page.locator('.process-group').count(),1);
 await emit({type:'message_start',message:{role:'user',content:'Local text'}});
 await page.waitForFunction(()=>processAwaitingUser===false);assert(await page.evaluate(()=>optimistic===currentProcessGroup));
 await page.locator('#promptInput').fill('Queued text');await page.locator('#sendButton').click();
 await emit({type:'message_start',message:{role:'assistant'}});
 await emit({type:'message_update',assistantMessageEvent:{type:'text_delta',delta:'Answer unchanged.'}});
 await emit({type:'message_end',message:{role:'assistant',content:'Answer unchanged.',stopReason:'stop'}});
 await emit({type:'agent_end'});await emit({type:'agent_settled'});
 await page.waitForFunction(()=>chatState==='success');
 await emit({type:'message_start',message:{role:'user',content:'Queued text'}});
 await page.waitForFunction(()=>chatPhase==='starting');assert.equal(await page.locator('.process-group').count(),1);
 await page.evaluate(()=>{window.deferState=true;window.pendingRefresh=refreshChatStatus(streamCtrl);});
 await emit({type:'message_start',message:{role:'assistant'}});
 await emit({type:'message_update',assistantMessageEvent:{type:'text_delta',delta:'Second answer.'}});
 await page.waitForFunction(()=>chatPhase==='generating');
 await page.evaluate(async()=>{fixtureState={ok:true,state:'success',busy:false,pendingDialogs:[]};resolveState();await pendingRefresh;window.deferState=false;});
 assert.equal(await page.locator('#chatStatusText').innerText(),'模型生成中');assert.deepEqual(await labels(),['思考 · 1次工具']);
 await emit({type:'message_end',message:{role:'assistant',content:'Second answer.',stopReason:'stop'}});await emit({type:'agent_settled'});
 await page.waitForFunction(()=>chatState==='success');
 assert.equal(await page.locator('.mdtext').nth(2).innerText(),'Answer unchanged.');
 // Long current status plus all input accessories simultaneously, including a
 // keyboard-sized viewport. Accessory scrolling must not evict input/footer.
 await emit({type:'message_start',message:{role:'user',content:'Layout wait'}});
 await emit({type:'tool_execution_start',toolCallId:'ask',toolName:'ask_dispatcher'});
 await emit({type:'tool_execution_update',toolCallId:'ask',toolName:'ask_dispatcher',partialResult:{details:{agentdWait:{askId:'layout',question:'Which?'}}}});
 await page.waitForFunction(()=>statusCoordinatorWaits.size===1);
 await page.evaluate(()=>{
  queueState={steering:['steer'],followUp:Array.from({length:20},(_,i)=>'follow-up '+i)};renderQueuedPanel();
  $('promptInput').value='/';showSlashNotice();paintSendButton();
  pendingImages=Array.from({length:8},()=>({mime:'image/png',b64:'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jWZkAAAAASUVORK5CYII=',dataUrl:'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jWZkAAAAASUVORK5CYII='}));renderAttachBar();
 });
 await page.waitForFunction(()=>$('contextUsage').textContent==='ctx≈42k / 200k');
 const viewports=[{width:320,height:740},{width:390,height:844},{width:1440,height:1000},{width:320,height:360}];
 for(const viewport of viewports) {
  await page.setViewportSize(viewport);await page.evaluate(()=>scrollStream(true));
  const layout=await page.evaluate(()=>{
   const rect=e=>{const r=e.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height};};
   return {docWidth:document.documentElement.scrollWidth,docHeight:document.documentElement.scrollHeight,
    stream:rect($('stream')),input:rect($('promptInput')),footer:rect($('chatFooter')),status:rect($('chatStatus')),context:rect($('contextUsage')),
    statusText:rect($('chatStatusText')),statusWidth:$('chatStatusText').scrollWidth,
    button:rect($('sendButton')),extras:rect($('composerExtras')),clear:rect($('clearButton')),
    groups:[...document.querySelectorAll('.process-group')].map(rect)};
  });
  assert(layout.docWidth<=viewport.width,'no horizontal overflow');assert(layout.docHeight<=viewport.height,'no page-level overflow');
  assert(layout.footer.top>=layout.input.bottom-1);assert(layout.footer.bottom<=viewport.height,'footer remains above keyboard-sized viewport bottom');
  assert(layout.extras.bottom<=layout.input.top+1);assert(layout.stream.height>0,'messages retain visible space');
  assert(layout.button.left>=layout.input.left && layout.button.right<=layout.input.right);
  assert(layout.status.right<=layout.context.left,'status and context never overlap');assert(layout.context.right<=layout.clear.left);
  assert(layout.statusWidth<=layout.statusText.width+1,'long current label fits even at 320px');
  assert.equal(await page.locator('#chatStatusText').innerText(),'等待协调者答复');
  assert.equal(await page.locator('#queuedPanel').isVisible(),true);assert.equal(await page.locator('#slashNotice').isVisible(),true);
  assert.equal(await page.locator('#sendButton').innerText(),'↑');
  if(process.env.CHAT_SCREENSHOT){
   const base=process.env.CHAT_SCREENSHOT.replace(/\.png$/,`-footer-${viewport.width}x${viewport.height}`);
   require('node:fs').writeFileSync(base+'.json',JSON.stringify(layout,null,2));await page.screenshot({path:base+'.png'});
  }
 }
 // Closing slash suggestions does not remove status or the waiting queue.
 await page.locator('#promptInput').fill('normal draft');assert.equal(await page.locator('#slashNotice').isVisible(),false);
 assert.equal(await page.locator('#chatFooter').isVisible(),true);assert.equal(await page.locator('#queuedPanel').isVisible(),true);
 console.log('PASS Chromium: sliced replay/late process anchors, text-only/follow-up no empty headers, 320/390/1440px and keyboard-sized footer with queue/slash/attachments');
 await page.close();
};
