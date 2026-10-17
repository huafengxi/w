// node test/markdown_widget.cjs — view context and ordinary Markdown contract.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, '../ext/markdown/view/markdown.html'), 'utf8');
const script = html.match(/<script type="application\/javascript">([\s\S]*?)<\/script>/)[1];
const marked = require('../ext/markdown/deps/marked.min.js');
const src = '/example/dashboard.md';
for (const prefix of ['', '/dev', '/arbitrary-worker']) {
  let config;
  const context = {
    window: {marked, location: {pathname: prefix + src, search: '?_v=autorun'}},
    URLSearchParams, inIframe: () => false,
    AnchorWidgets: {configure(value) { config = value; }},
    makeEditor() {}, $() { return {}; }
  };
  vm.createContext(context);
  vm.runInContext(script.replace('$ARGS_JSON', JSON.stringify({src})), context);
  context.initApp();
  assert.equal(config.shellEndpoint, prefix + '/w/ext/shell/rpc/sh.py');
  assert.equal(config.source, src);
  assert.equal(config.autoRun, true);
  const rendered = context.renderMarkdown('[状态](<cmd:echo snake_case>)');
  assert(rendered.includes('href="cmd:echo%20snake_case"'));
  assert(context.renderMarkdown('${date}').includes('${date}'), 'old command is literal');
  assert(!context.renderMarkdown('`[时间](cmd:date)`').includes('<a'));
  assert(!context.renderMarkdown('```md\n[时间](cmd:date)\n```').includes('<a'));
  assert(context.renderMarkdown('_literal_').includes('_literal_'));
  assert(!context.renderMarkdown('_literal_').includes('<em>'));
  console.log('PASS Markdown adapter and RPC route:', prefix || '(direct)');
}
const fallback = {window: {location: {pathname: src}}};
vm.createContext(fallback); vm.runInContext(script, fallback);
assert.equal(fallback.cmdSource, src);
assert.equal(fallback.cmdPrefix, '');
console.log('PASS missing injection fallback');
const protocol = {window: {location: {pathname: '/', href: 'http://fixture.test/'},
  document: {readyState: 'loading', addEventListener() {}}}, URL};
vm.createContext(protocol);
vm.runInContext(fs.readFileSync(path.join(__dirname, '../view/anchor-widgets.js'), 'utf8'), protocol);
const parse = protocol.window.AnchorWidgets.parse;
for (const href of ['cmd: ! change', 'cmd:%20!%20change', 'cmd:%09%0A!%20change']) {
  const result = parse(href); assert(result.manual, href); assert.equal(result.body, 'change');
}
assert.equal(parse('cmd:%21%20true').manual, false);
assert.equal(parse('cmd:%21%20true').body, '! true');
assert.equal(parse('cmd:!%20'), null);
assert.equal(parse('cmd:%ZZ'), null);
assert.equal(parse('/details.md?_v=inline'), null);
console.log('PASS manual-marker whitespace, literal shell !, invalid inputs, no legacy protocol');
