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
  if(process.env.CHAT_SCREENSHOT) await page.screenshot({path:process.env.CHAT_SCREENSHOT});
  assert.deepEqual(errors,[]);
  console.log('ALL PASS: Chromium mobile layout, icon-free bubble edit/cancel/busy and image links, batch recall, terminal icon and click-to-send prompt/queue/images/empty');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
