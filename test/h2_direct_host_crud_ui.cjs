// Actual UI lifecycle functions, actual independent web API child, no HTTP/browser.
'use strict';
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const {spawn}=require('node:child_process'),readline=require('node:readline'),assert=require('node:assert/strict');
const [python,options,wire]=process.argv.slice(2);
const p=spawn(python,['-B',path.join(__dirname,'h2_direct_host_crud_client.py'),options],{stdio:['pipe','pipe','inherit'],env:process.env});
const queue=[];readline.createInterface({input:p.stdout}).on('line',line=>queue.shift()?.(JSON.parse(line)));
function call(req){return new Promise((resolve,reject)=>{queue.push(obj=>{fs.appendFileSync(wire,JSON.stringify({request:req,response:obj})+'\n');obj.ok?resolve(obj.result):reject(new Error(obj.error));});p.stdin.write(JSON.stringify(req)+'\n');});}
(async()=>{
 const doc=await call({special:'create',profile:'good',names:['ui01']});
 const a=(await call({op:'attach',session:doc.agent_site})).body;assert(a.ok);
 let badFence=true;const notifications=[],errors=[];
 const html=fs.readFileSync(path.join(__dirname,'../ext/sessiond/view/index.html'),'utf8');
 const begin=html.indexOf('function notifyTabsChanged()'),end=html.indexOf('// ---------------- 斜杠命令拦截',begin);
 assert(begin>0&&end>begin);
 const ctx=vm.createContext({IS_CC:true,attached:doc.agent_site,AGENT_NAME:doc.name,sessionGone:false,
  curCtx:null,ctxStatsInFlight:false,ctxStatsRequestId:null,
  document:{title:''},location:{origin:'http://fixture.invalid'},
  window:{parent:{postMessage:(o,origin)=>notifications.push({o,origin})}},
  flash:(s)=>errors.push(s),showNotice:()=>{},stopStream:()=>{},paintContextUsage:()=>{},clearPendingDialogs:()=>{},$:()=>({}),
  api:async(op,args)=>(await call({op,...args,expected_registration:doc.registration,expected_instance:badFence?'stale':a.gen})).body});
 vm.runInContext(html.slice(begin,end),ctx);
 await vm.runInContext('doRename("bad\\ntitle")',ctx);assert.equal(notifications.length,0);
 await vm.runInContext('doRename("Real API title")',ctx);assert.equal(notifications.length,1);
 let tabs=(await call({op:'session_tabs'})).body;assert.equal(tabs.rows[0].title,'Real API title');
 await vm.runInContext('doDelete()',ctx);assert.equal(notifications.length,1);assert.equal(ctx.sessionGone,false);
 assert((await call({op:'list_sessions'})).body.sessions.length===1);
 badFence=false;await vm.runInContext('doDelete()',ctx);assert.equal(notifications.length,2);assert.equal(ctx.sessionGone,true);assert.equal(ctx.attached,null);
 assert.equal((await call({op:'session_tabs'})).body.rows.length,0);
 await vm.runInContext('doDelete()',ctx);assert.equal(notifications.length,2);
 assert(notifications.every(x=>x.o.type==='frame:tabs-changed'&&x.origin==='http://fixture.invalid'));
 fs.appendFileSync(wire,JSON.stringify({notifications,errors,documentTitle:ctx.document.title,web:p.pid})+'\n');
 p.stdin.end();console.log('UI actual API CRUD PASS');
})().catch(e=>{console.error(e);p.stdin.end();process.exitCode=1;});
