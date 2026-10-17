// Loaded by chat_frontend.cjs; tests execute the actual HTML script in its VM harness.
module.exports = ({test, harness, assert, html, emptyBaseline}) => {
// Process headers use real lifecycle/render functions, independently of history cursors.
function processLabels(h) {
  return Array.from(h.run(`$('stream').children.filter(d => (d.className || '').split(' ').includes('process-group')).map(d => d.children[0].children[1].textContent)`));
}
function processStatus(h, text, count = 0) {
  assert.equal(h.run('currentProcessGroup.label.textContent'), '过程 · ' + text + (count ? ' · ' + count + '次工具' : ''));
  assert.equal(h.elements.get('chatStatus').title, text, 'title and indicator share the status source');
}
const processBaseline = `{entries:[
 {id:'u1',type:'message',message:{role:'user',content:'old question'}},
 {id:'a1',parentId:'u1',type:'message',message:{role:'assistant',content:[{type:'thinking',thinking:'old thought'},{type:'toolCall',id:'old-tool',name:'read',arguments:{path:'fixture'}}]}},
 {id:'r1',parentId:'a1',type:'message',message:{role:'toolResult',toolCallId:'old-tool',content:'old result'}},
 {id:'a2',parentId:'r1',type:'message',message:{role:'assistant',content:[{type:'text',text:'old answer'}]}},
 {id:'u2',parentId:'a2',type:'message',message:{role:'user',content:'last question'}},
 {id:'a3',parentId:'u2',type:'message',message:{role:'assistant',content:[{type:'text',text:'last text'}]}}
 ],leafId:'a3',watermark:'fixture:1',pendingDialogs:[]}`;

test('process header starts before first token, admits once, text-only terminal freezes', async () => {
  const h = harness();
  await h.run(`attached='/test.jsonl';api=async()=>({ok:true});setChatStatus('ready');$('promptInput').value='first';sendPrompt();`);
  processStatus(h, '启动中');
  assert.equal(processLabels(h).length, 1);
  h.run(`globalThis.firstGroup=currentProcessGroup;
    handleEvent({type:'agent_start'});
    handleEvent({type:'message_start',message:{role:'user',content:'first'}});`);
  assert(h.run('currentProcessGroup===firstGroup'));
  assert.equal(processLabels(h).length, 1);
  assert(h.run(`$('stream').children.indexOf(turnAnchor) < $('stream').children.indexOf(firstGroup.det)`));
  h.run(`handleEvent({type:'message_start',message:{role:'assistant'}});
    handleEvent({type:'message_update',assistantMessageEvent:{type:'text_delta',delta:'unchanged answer'}});
    handleEvent({type:'message_end',message:{role:'assistant',content:[{type:'text',text:'unchanged answer'}]}});`);
  processStatus(h, '模型生成中');
  assert.equal(h.run('firstGroup.n'), 0);
  assert.equal(h.run(`$('stream').children.find(d=>d.className==='ev role-assistant md')._md.textContent`), 'unchanged answer');
  h.run(`handleEvent({type:'turn_end',toolResults:[]});handleEvent({type:'agent_end'});`);
  processStatus(h, '收尾中');
  h.run(`handleEvent({type:'agent_settled'});`);
  processStatus(h, '已成功完成');
  h.run(`stopStream();handleEvent({type:'message_start',message:{role:'user',content:'second'}});`);
  processStatus(h, '启动中');
  assert.deepEqual(processLabels(h), ['过程 · 已成功完成', '过程 · 启动中']);
  assert.equal(h.run('firstGroup.statusLabel'), '已成功完成');
  assert(!html.includes('id="chatStatusText"'), 'no independent bottom status text');
});

test('thinking and multiple tools/assistant subturns reuse one collapsible live header', () => {
  const h = harness();
  h.run(`handleEvent({type:'message_start',message:{role:'user',content:'fixture'}});
    handleEvent({type:'message_start',message:{role:'assistant'}});
    handleEvent({type:'message_update',assistantMessageEvent:{type:'thinking_delta',delta:'think'}});`);
  assert.equal(h.run('currentProcessGroup.det.open'), true, 'first real live block expands once');
  h.run(`currentProcessGroup.det.open=false;
    handleEvent({type:'message_update',assistantMessageEvent:{type:'thinking_delta',delta:' more'}});`);
  assert.equal(h.run('currentProcessGroup.det.open'), false, 'token does not reopen');
  for (let i=0;i<3;i++) {
    h.run(`handleEvent({type:'tool_execution_start',toolCallId:'t${i}',toolName:'read'});`);
    processStatus(h, '工具执行中', i+1);
    assert.equal(h.run('currentProcessGroup.det.open'), false);
    h.run(`handleEvent({type:'tool_execution_end',toolCallId:'t${i}',toolName:'read',result:{content:[]}});
      handleEvent({type:'turn_end',toolResults:[{}]});
      handleEvent({type:'message_start',message:{role:'assistant'}});`);
    processStatus(h, '模型生成中', i+1);
  }
  assert.equal(processLabels(h).length, 1);
  assert.equal(h.run('currentProcessGroup.n'), 4);
  assert(!processLabels(h)[0].includes('process block'));
  h.run(`handleEvent({type:'extension_ui_request',id:'q',method:'input',title:'fixture'});`);
  processStatus(h, '等待用户回答', 3);
  h.run(`collapseDialog();handleEvent({type:'sessiond.dialog_resolved',id:'q'});
    handleEvent({type:'tool_execution_start',toolCallId:'ask',toolName:'ask_dispatcher'});
    handleEvent({type:'tool_execution_update',toolCallId:'ask',toolName:'ask_dispatcher',partialResult:{details:{agentdWait:{askId:'id',question:'fixture'}}}});`);
  processStatus(h, '等待协调者答复', 4);
  h.run(`handleEvent({type:'tool_execution_end',toolCallId:'ask',toolName:'ask_dispatcher',result:{content:[]}});`);
  processStatus(h, '处理中', 4);
  h.run(`handleEvent({type:'auto_retry_start',attempt:1,maxAttempts:2});`);
  processStatus(h, '自动重试中', 4);
  h.run(`handleEvent({type:'auto_retry_end',success:true});handleEvent({type:'auto_compaction_start'});`);
  processStatus(h, '上下文压缩中', 4);
  h.run(`handleEvent({type:'agent_end',errorMessage:'fixture failure'});`);
  processStatus(h, '收尾中', 4);
  h.run(`handleEvent({type:'agent_settled'});`);
  processStatus(h, '最终失败', 4);
  assert.equal(h.run('currentProcessGroup.det.open'), false);
});

test('follow_up queue admission cannot steal or create a header; previous final is immutable', async () => {
  const h = harness();
  await h.run(`attached='/test.jsonl';api=async()=>({ok:true});
    handleEvent({type:'message_start',message:{role:'user',content:'first'}});
    globalThis.oldGroup=currentProcessGroup;$('promptInput').value='queued';sendPrompt();
    handleEvent({type:'queue_update',steering:[],followUp:['queued']});`);
  assert.equal(processLabels(h).length, 1);
  h.run(`handleEvent({type:'agent_end'});`);
  processStatus(h, '收尾中');
  h.run(`handleEvent({type:'message_start',message:{role:'user',content:'queued'}});`);
  assert.equal(processLabels(h).length, 2);
  assert.equal(h.run('oldGroup.label.textContent'), '过程', 'unfinished old turn becomes neutral, not a fabricated terminal');
  h.run(`handleEvent({type:'message_end',message:{role:'assistant',stopReason:'aborted'}});handleEvent({type:'agent_settled'});`);
  processStatus(h, '已停止');
  assert.equal(h.run('oldGroup.label.textContent'), '过程');
});

test('ownership transfer clears any nonterminal old label but preserves each known terminal', () => {
  for (const phase of ['starting','generating','tools','retrying','compacting','settling','unknown']) {
    const h=harness();
    h.run(`handleEvent({type:'message_start',message:{role:'user',content:'old'}});
      setChatStatus('busy','${phase}');globalThis.old=currentProcessGroup;
      handleEvent({type:'message_start',message:{role:'user',content:'new'}});`);
    assert.equal(h.run('old.label.textContent'),'过程');
    processStatus(h,'启动中');
    assert.equal(processLabels(h).length,2);
  }
  for (const state of ['success','failed','stopped']) {
    const h=harness();
    h.run(`handleEvent({type:'message_start',message:{role:'user',content:'old'}});
      turnActive=false;setChatStatus('${state}');globalThis.old=currentProcessGroup;globalThis.oldLabel=old.label.textContent;
      stopStream();handleEvent({type:'message_start',message:{role:'user',content:'new'}});`);
    assert.equal(h.run('old.label.textContent'),h.run('oldLabel'));
    assert.equal(h.run('old.frozen'),true);
    processStatus(h,'启动中');
  }
});

test('sliced baseline stays statusless; busy binds last text-only turn, idle never paints historical success', async () => {
  for (const busy of [false,true]) {
    const h=harness();
    h.context.Date={now:(()=>{let n=0;return()=>n+=6;})()};
    h.run(`attached='/test.jsonl';applyBaseline(${processBaseline},false);`);
    assert.equal(h.run('batchDepth'),1);
    assert.equal(h.run('currentProcessGroup'),null);
    h.drainTimers();
    assert.deepEqual(processLabels(h),['过程 · 1次工具']);
    await h.run(`api=async()=>({ok:true,state:'${busy?'busy':'success'}',busy:${busy},pendingDialogs:[]});refreshChatStatus();`);
    assert.deepEqual(processLabels(h),busy?['过程 · 1次工具','过程 · 处理中']:['过程 · 1次工具']);
    if(busy) {
      assert.equal(h.run(`$('stream').children.indexOf(currentProcessGroup.det) < $('stream').children.findIndex(d=>d._md?.textContent==='last text')`),true);
      h.run(`handleEvent({type:'message_start',message:{role:'assistant'}});
        handleEvent({type:'message_update',assistantMessageEvent:{type:'thinking_delta',delta:'live thought'}});`);
      assert.equal(processLabels(h).length,2);
      processStatus(h,'模型生成中');
    } else {
      h.run(`handleEvent({type:'message_start',message:{role:'user',content:'new'}});`);
      assert.deepEqual(processLabels(h),['过程 · 1次工具','过程 · 启动中']);
    }
  }
});

test('busy baseline with existing blocks binds only last group; empty baseline admission reuses placeholder', async () => {
  const h=harness();
  h.run(`attached='/test.jsonl';applyBaseline(${processBaseline.replace("{type:'text',text:'last text'}", "{type:'thinking',thinking:'last thought'}")},false);`);
  await h.run(`api=async()=>({ok:true,state:'busy',busy:true,pendingDialogs:[]});refreshChatStatus();`);
  assert.deepEqual(processLabels(h),['过程 · 1次工具','过程 · 处理中']);
  h.run(`applyBaseline(${emptyBaseline},false);`);
  await h.run('refreshChatStatus();');
  assert.deepEqual(processLabels(h),['过程 · 处理中']);
  h.run(`globalThis.placeholder=currentProcessGroup;handleEvent({type:'message_start',message:{role:'user',content:'admitted'}});`);
  assert(h.run('currentProcessGroup===placeholder'));
  assert.equal(processLabels(h).length,1);
});

test('process binding survives reconnect races, and heal invalidates pending authority before history', async () => {
  const h=harness();
  await h.run(`(async()=>{attached='/test.jsonl';handleEvent({type:'message_start',message:{role:'user',content:'one'}});
    globalThis.grp=currentProcessGroup;let resolve;api=()=>new Promise(r=>resolve=r);
    const old=refreshChatStatus();handleEvent({type:'tool_execution_start',toolCallId:'t',toolName:'read'});
    resolve({ok:true,state:'success',busy:false,pendingDialogs:[]});await old;})()`);
  processStatus(h,'工具执行中',1);
  h.run('stopStream();');processStatus(h,'连接状态未知',1);
  await h.run(`api=async()=>({ok:true,state:'busy',busy:true,pendingDialogs:[]});refreshChatStatus();`);
  processStatus(h,'处理中',1);
  assert(h.run('currentProcessGroup===grp'));
  await h.run(`(async()=>{let resolve;api=()=>new Promise(r=>resolve=r);const old=refreshChatStatus();
    applyBaseline(${processBaseline},false);resolve({ok:true,state:'busy',busy:true,pendingDialogs:[]});await old;})()`);
  assert.deepEqual(processLabels(h),['过程 · 1次工具']);
  assert.equal(h.run('currentProcessGroup'),null);
  await h.run(`api=async()=>({ok:true,state:'ready',busy:false,pendingDialogs:[]});refreshChatStatus();`);
  assert.deepEqual(processLabels(h),['过程 · 1次工具']);
});


};
