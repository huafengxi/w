// Loaded by chat_frontend.cjs; exercises real lifecycle/render functions.
module.exports = ({test, harness, assert, emptyBaseline}) => {
const labels = h => Array.from(h.run(`$('stream').children.filter(d => d.className==='process-group').map(d=>d.children[0].children[1].textContent)`));
const status = (h, text) => assert.equal(h.run("$('chatStatusText').textContent"), text);
const processBaseline = `{entries:[
 {id:'u1',type:'message',message:{role:'user',content:'old question'}},
 {id:'a1',parentId:'u1',type:'message',message:{role:'assistant',content:[{type:'thinking',thinking:'old thought'},{type:'toolCall',id:'old-tool',name:'read',arguments:{path:'fixture'}}]}},
 {id:'r1',parentId:'a1',type:'message',message:{role:'toolResult',toolCallId:'old-tool',content:'old result'}},
 {id:'a2',parentId:'r1',type:'message',message:{role:'assistant',content:[{type:'text',text:'old answer'}]}},
 {id:'u2',parentId:'a2',type:'message',message:{role:'user',content:'last question'}},
 {id:'a3',parentId:'u2',type:'message',message:{role:'assistant',content:[{type:'text',text:'last text'}]}}
 ],leafId:'a3',watermark:'fixture:1',pendingDialogs:[]}`;

test('text-only startup, admission, generation and completion never mount an empty process header', async () => {
 const h=harness();
 await h.run(`attached='/test.jsonl';api=async()=>({ok:true});setChatStatus('ready');$('promptInput').value='first';sendPrompt();`);
 status(h,'启动中'); assert.deepEqual(labels(h),[]);
 h.run(`globalThis.first=currentProcessGroup;handleEvent({type:'agent_start'});
  handleEvent({type:'message_start',message:{role:'user',content:'first'}});
  handleEvent({type:'message_start',message:{role:'assistant'}});
  handleEvent({type:'message_update',assistantMessageEvent:{type:'text_delta',delta:'unchanged answer'}});
  handleEvent({type:'message_end',message:{role:'assistant',content:[{type:'text',text:'unchanged answer'}]}});`);
 assert(h.run('first===currentProcessGroup')); status(h,'模型生成中');
 assert.equal(h.run(`$('stream').children.find(d=>d.className==='ev role-assistant md')._md.textContent`),'unchanged answer');
 h.run(`handleEvent({type:'turn_end',toolResults:[]});handleEvent({type:'agent_end'});`);
 status(h,'收尾中'); assert.deepEqual(labels(h),[]);
 h.run(`handleEvent({type:'agent_settled'});`); status(h,'就绪');
 h.run(`stopStream();handleEvent({type:'message_start',message:{role:'user',content:'second'}});`);
 assert.deepEqual(labels(h),[]); status(h,'启动中');
});

test('actual thinking/tools share one content-only header and respect manual collapse across phases', () => {
 const h=harness();
 h.run(`handleEvent({type:'message_start',message:{role:'user',content:'fixture'}});
  handleEvent({type:'message_start',message:{role:'assistant'}});
  handleEvent({type:'message_update',assistantMessageEvent:{type:'thinking_delta',delta:'think'}});`);
 assert.deepEqual(labels(h),['思考']); assert(h.run('currentProcessGroup.det.open'));
 h.run(`currentProcessGroup.det.open=false;
  handleEvent({type:'message_update',assistantMessageEvent:{type:'thinking_delta',delta:' more'}});`);
 for(let i=0;i<3;i++) {
  h.run(`handleEvent({type:'tool_execution_start',toolCallId:'t${i}',toolName:'read'});`);
  status(h,'工具执行中'); assert.deepEqual(labels(h),['思考 · '+(i+1)+'次工具']);
  assert.equal(h.run('currentProcessGroup.det.open'),false);
  h.run(`handleEvent({type:'tool_execution_end',toolCallId:'t${i}',result:{content:[]}});
   handleEvent({type:'turn_end',toolResults:[{}]});handleEvent({type:'message_start',message:{role:'assistant'}});`);
 }
 assert.equal(h.run('currentProcessGroup.n'),4);
 h.run(`handleEvent({type:'extension_ui_request',id:'q',method:'input'});`); status(h,'等待用户回答');
 h.run(`collapseDialog();handleEvent({type:'sessiond.dialog_resolved',id:'q'});
  handleEvent({type:'tool_execution_start',toolCallId:'ask',toolName:'ask_dispatcher'});
  handleEvent({type:'tool_execution_update',toolCallId:'ask',toolName:'ask_dispatcher',partialResult:{details:{agentdWait:{askId:'id',question:'fixture'}}}});`);
 status(h,'等待协调者答复'); assert.deepEqual(labels(h),['思考 · 4次工具']);
 h.run(`handleEvent({type:'tool_execution_end',toolCallId:'ask',result:{}});
  handleEvent({type:'auto_retry_start',attempt:1,maxAttempts:2});`); status(h,'自动重试中');
 h.run(`handleEvent({type:'auto_retry_end',success:true});handleEvent({type:'auto_compaction_start'});`); status(h,'上下文压缩中');
 h.run(`handleEvent({type:'agent_end',errorMessage:'fixture failure'});handleEvent({type:'agent_settled'});`);
 status(h,'最终失败'); assert.deepEqual(labels(h),['思考 · 4次工具']);
 assert.equal(h.run('currentProcessGroup.det.open'),false);
});

test('follow-up queuing creates no header and admission gives actual tools their own group', async () => {
 const h=harness();
 await h.run(`attached='/test.jsonl';api=async()=>({ok:true});
  handleEvent({type:'message_start',message:{role:'user',content:'first'}});
  handleEvent({type:'tool_execution_start',toolCallId:'first-tool',toolName:'read'});
  globalThis.old=currentProcessGroup;$('promptInput').value='queued';sendPrompt();
  handleEvent({type:'queue_update',steering:[],followUp:['queued']});`);
 assert.deepEqual(labels(h),['1次工具']);
 h.run(`handleEvent({type:'agent_end'});handleEvent({type:'message_start',message:{role:'user',content:'queued'}});`);
 assert.deepEqual(labels(h),['1次工具']); assert(h.run('old!==currentProcessGroup'));
 h.run(`handleEvent({type:'tool_execution_start',toolCallId:'second-tool',toolName:'bash'});
  handleEvent({type:'message_end',message:{role:'assistant',stopReason:'aborted'}});handleEvent({type:'agent_settled'});`);
 assert.deepEqual(labels(h),['1次工具','1次工具']); status(h,'已停止');
 assert.equal(h.run(`$('stream').children.filter(d=>d.className==='ev sys' && d.children[0]?.textContent==='已停止').length`),1);
});

test('historical process summaries never change with live status or terminal/ownership transfer', () => {
 for(const state of ['busy','success','failed','stopped','unknown']) {
  const h=harness();
  h.run(`handleEvent({type:'message_start',message:{role:'user',content:'old'}});
   handleEvent({type:'tool_execution_start',toolCallId:'t',toolName:'read'});
   globalThis.old=currentProcessGroup;setChatStatus('${state}','retrying');
   handleEvent({type:'message_start',message:{role:'user',content:'new'}});`);
  assert.deepEqual(labels(h),['1次工具']); status(h,'启动中');
  assert.equal(h.run('old.label.textContent'),'1次工具');
 }
});

test('sliced baseline and busy text-only binding stay invisible until a real continuation block', async () => {
 for(const busy of [false,true]) {
  const h=harness();h.context.Date={now:(()=>{let n=0;return()=>n+=6;})()};
  h.run(`attached='/test.jsonl';applyBaseline(${processBaseline},false);`);
  assert.equal(h.run('batchDepth'),1); assert.equal(h.run('currentProcessGroup'),null);
  h.drainTimers(); assert.deepEqual(labels(h),['思考 · 1次工具']);
  await h.run(`api=async()=>({ok:true,state:'${busy?'busy':'success'}',busy:${busy},pendingDialogs:[]});refreshChatStatus();`);
  assert.deepEqual(labels(h),['思考 · 1次工具']); status(h,busy?'处理中':'就绪');
  if(busy) {
   h.run(`handleEvent({type:'message_start',message:{role:'assistant'}});
    handleEvent({type:'message_update',assistantMessageEvent:{type:'thinking_delta',delta:'live thought'}});`);
   assert.deepEqual(labels(h),['思考 · 1次工具','思考']);
   assert(h.run(`$('stream').children.indexOf(currentProcessGroup.det) < $('stream').children.findIndex(d=>d._md?.textContent==='last text')`));
  }
 }
});

test('busy replay binds only last real group; empty baseline admission retains its detached reservation', async () => {
 const h=harness();
 h.run(`attached='/test.jsonl';applyBaseline(${processBaseline.replace("{type:'text',text:'last text'}","{type:'thinking',thinking:'last thought'}")},false);`);
 await h.run(`api=async()=>({ok:true,state:'busy',busy:true,pendingDialogs:[]});refreshChatStatus();`);
 assert.deepEqual(labels(h),['思考 · 1次工具','思考']);
 h.run(`applyBaseline(${emptyBaseline},false);`); await h.run('refreshChatStatus();');
 assert.deepEqual(labels(h),[]);
 h.run(`globalThis.placeholder=currentProcessGroup;handleEvent({type:'message_start',message:{role:'user',content:'admitted'}});
  handleEvent({type:'tool_execution_start',toolCallId:'new',toolName:'read'});`);
 assert(h.run('currentProcessGroup===placeholder')); assert.deepEqual(labels(h),['1次工具']);
 assert(h.run(`$('stream').children.indexOf(turnAnchor)<$('stream').children.indexOf(currentProcessGroup.det)`));
});

test('reconnect/late authority cannot relabel history or terminate a live tool, heal invalidates ownership', async () => {
 const h=harness();
 await h.run(`(async()=>{attached='/test.jsonl';handleEvent({type:'message_start',message:{role:'user',content:'one'}});
  globalThis.grp=currentProcessGroup;let resolve;api=()=>new Promise(r=>resolve=r);
  const old=refreshChatStatus();handleEvent({type:'tool_execution_start',toolCallId:'t',toolName:'read'});
  resolve({ok:true,state:'success',busy:false,pendingDialogs:[]});await old;})()`);
 status(h,'工具执行中');h.run('stopStream();');status(h,'连接状态未知');assert.deepEqual(labels(h),['1次工具']);
 await h.run(`api=async()=>({ok:true,state:'busy',busy:true,pendingDialogs:[]});refreshChatStatus();`);
 assert(h.run('currentProcessGroup===grp'));status(h,'处理中');
 await h.run(`(async()=>{let resolve;api=()=>new Promise(r=>resolve=r);const old=refreshChatStatus();
  applyBaseline(${processBaseline},false);resolve({ok:true,state:'busy',busy:true,pendingDialogs:[]});await old;})()`);
 assert.deepEqual(labels(h),['思考 · 1次工具']);assert.equal(h.run('currentProcessGroup'),null);
});
};
