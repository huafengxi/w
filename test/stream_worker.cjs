// Run the actual SharedWorker with fake ports and a deterministic streamed response.
'use strict';
const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../ext/sessiond/view/stream-worker.js'), 'utf8');
const timers = new Map(), requests = [];
let seq = 0;
const self = {location: {href: 'http://fixture/dev/w/ext/sessiond/view/stream-worker.js'}};
const context = vm.createContext({self, URL, Map, AbortController, TextDecoder,
  setTimeout(fn) {const id = ++seq; timers.set(id, fn); return id;},
  clearTimeout(id) {timers.delete(id);},
  fetch(url, options) {requests.push({url, options}); return new Promise(()=>{});}, console});
vm.runInContext(source, context);
function port(session, since, token) {
  const p = {messages: [], start() {}, postMessage(m) {this.messages.push(m);}};
  self.onconnect({ports: [p]});
  p.onmessage({data: {type:'subscribe', session, since, token}});
  return p;
}
function flush() {for (const [id, fn] of [...timers]) {timers.delete(id); fn();}}
const ports = Array.from({length:8}, (_,i)=>port('/one.agent', i===0?'one:2':'one:4', String(i)));
const second = port('/two.agent', 'two:9', 'second');
flush();
assert.equal(requests.length, 1, 'nine pages must share one HTTP stream');
const subs = JSON.parse(new URL(requests[0].url, 'http://fixture').searchParams.get('subscriptions'));
assert.deepEqual(subs, [{session:'/one.agent',since:'one:2'},{session:'/two.agent',since:'two:9'}]);
vm.runInContext(`deliver({session:'/one.agent',id:'one:3',event:{type:'agent_start'}})`, context);
assert.equal(ports[0].messages.length, 1);
assert(ports.slice(1).every(p=>p.messages.length===0), 'old replay must not duplicate current clients');
assert.equal(second.messages.length, 0, 'never route across sessions');
vm.runInContext(`deliver({session:'/one.agent',id:'one:5',event:{type:'queue_update'}})`, context);
assert(ports.every(p=>p.messages.at(-1).frame.includes('one:5')));
for (const p of ports) p.onmessage({data:{type:'unsubscribe'}});
second.onmessage({data:{type:'unsubscribe'}});
assert(requests[0].options.signal.aborted, 'last unsubscribe aborts the network stream');
flush(); assert.equal(requests.length,1, 'no orphan stream after all pages leave');
console.log('PASS shared worker: one connection, independent replay, session isolation, cleanup');
