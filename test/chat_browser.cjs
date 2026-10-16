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
  await page.getByRole('button',{name:'编辑并重新生成',exact:true}).click();
  await page.locator('#editBanner').waitFor({state:'visible'});
  assert.equal(await page.locator('#promptInput').inputValue(),'historical user text');
  await page.evaluate(()=>{handleEvent({type:'agent_start'});handleEvent({type:'agent_end'});});
  assert.equal(await page.locator('#chatStatus').getAttribute('data-state'),'busy');
  assert.equal(await page.locator('#editSubmit').isDisabled(),true);
  assert.equal(await page.locator('#promptInput').isDisabled(),false);
  await page.locator('#editCancel').click();
  assert.equal(await page.locator('#promptInput').inputValue(),draft);
  await page.evaluate(()=>handleEvent({type:'agent_settled'}));
  assert.equal(await page.locator('#chatStatus').getAttribute('aria-label'),'已成功完成');
  await page.locator('#chatStatus').tap({force:true}).catch(()=>page.locator('#chatStatus').click());
  assert.equal(await page.locator('#flashNotice').textContent(),'已成功完成');
  const before=await page.locator('#chatStatus').boundingBox();
  await page.locator('#promptInput').fill('first\nsecond\nthird\nfourth');
  const after=await page.locator('#chatStatus').boundingBox();
  const input=await page.locator('#promptInput').boundingBox();
  assert.equal(after.x,before.x);assert.equal(after.y+after.height,before.y+before.height);
  assert(after.x>=input.x && after.x+after.width<=input.x+input.width);
  assert.equal(await page.locator('#promptInput').evaluate(e=>parseFloat(getComputedStyle(e).paddingRight)),44);
  if(process.env.CHAT_SCREENSHOT) await page.screenshot({path:process.env.CHAT_SCREENSHOT});
  assert.deepEqual(errors,[]);
  console.log('ALL PASS: Chromium mobile layout, batch recall, edit/cancel/busy, terminal icon and tap explanation');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
