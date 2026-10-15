// node test/markdown_widget.cjs — run the real widget with a minimal DOM.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, '../ext/markdown/view/markdown.html'), 'utf8');
const script = html.match(/<script type="application\/javascript">([\s\S]*?)<\/script>/)[1];
function node() {
  return {style: {}, classList: {add() {}}, appendChild() {},
    parentNode: {replaceChild() {}}};
}
const src = '/agent-platform/agentd/tasks.md';
for (const prefix of ['', '/dev', '/arbitrary-worker']) {
  const calls = [];
  const context = {
    window: {location: {pathname: prefix + src}},
    // getUrl deliberately remains the browser URL, as in my.js.
    getUrl: () => prefix + src,
    $: () => node(), $n: node, hide() {}, show() {},
    rpc_stream: (url, args, chunk, done) => { calls.push({url, args}); done(); },
  };
  vm.createContext(context);
  vm.runInContext(script.replace('$ARGS_JSON', JSON.stringify({src})), context);
  const cmd = 'python3 "$(dirname "$src")/report.py" -s abnormal';
  const widget = context.buildCmdWidget({id: 'cmd1', cmd});
  let completed = false;
  widget.run(() => { completed = true; });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, prefix + '/w/ext/shell/rpc/sh.py');
  assert.equal(calls[0].args.src, src);
  assert.equal(calls[0].args.cmd, cmd);
  assert.equal(calls[0].args.stream, 2);
  assert(completed);
  console.log('PASS widget source and RPC route:', prefix || '(direct)');
}
// A static/unsubstituted direct view still works without server arguments.
const fallback = {window: {location: {pathname: src}}};
vm.createContext(fallback);
vm.runInContext(script, fallback);
assert.equal(fallback.cmdSource, src);
assert.equal(fallback.cmdPrefix, '');
console.log('PASS missing injection direct-view fallback');
