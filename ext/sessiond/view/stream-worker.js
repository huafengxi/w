// One SharedWorker and one SSE per host prefix, shared by its chat tabs/iframes.
// Ports retain independent cursors so a new tab never replays events into an old tab.
'use strict';
const clients = new Map();
let controller = null, timer = null;
const prefix = new URL(self.location.href).pathname.split('/w/ext/sessiond/view/')[0];
const endpoint = prefix + '/sessiond/rpc/multiplex.py';
function cursor(id) {
  const m = /^([^:]+):(\d+)$/.exec(id || '');
  return m ? [m[1], Number(m[2])] : ['', 0];
}
function send(client, type, payload = {}) {
  client.port.postMessage({type, token: client.token, ...payload});
}
function schedule(delay = 50) {
  if (timer !== null) clearTimeout(timer);
  if (controller) { controller.abort(); controller = null; }
  timer = setTimeout(() => { timer = null; connect(); }, delay);
}
function deliver(packet) {
  for (const client of clients.values()) {
    if (client.session !== packet.session) continue;
    const event = packet.event || {};
    if (event.type === 'sessiond.stream_closed') {
      send(client, 'error', {error: '会话事件流已结束'}); continue;
    }
    const [ns, n] = cursor(packet.id), [oldNs, oldN] = cursor(client.since);
    if (packet.id && ns === oldNs && n <= oldN) continue;
    if (event.type === 'webgw.gap' && event.oldest) {
      const [firstNs, firstN] = cursor(event.oldest);
      if (oldNs === firstNs && oldN >= firstN - 1) continue;
    }
    if (packet.id) client.since = packet.id;
    send(client, 'frame', {frame: (packet.id ? 'id: ' + packet.id + '\n' : '')
      + 'data: ' + JSON.stringify(event) + '\n\n'});
  }
}
async function connect() {
  if (!clients.size) return;
  const subscriptions = new Map();
  for (const c of clients.values()) {
    const old = subscriptions.get(c.session);
    if (!old) subscriptions.set(c.session, {session: c.session, since: c.since});
    else {
      const [ns, n] = cursor(c.since), [oldNs, oldN] = cursor(old.since);
      old.since = ns === oldNs ? (n < oldN ? c.since : old.since) : '';
    }
  }
  const my = new AbortController(); controller = my;
  try {
    const response = await fetch(endpoint + '?subscriptions=' + encodeURIComponent(JSON.stringify([...subscriptions.values()])),
      {credentials: 'same-origin', signal: my.signal});
    if (my.signal.aborted) return;
    if (!response.ok) throw Error('event stream HTTP ' + response.status);
    for (const c of clients.values()) send(c, 'open');
    const reader = response.body.getReader(), decoder = new TextDecoder();
    let buffer = '';
    while (!my.signal.aborted) {
      const {done, value} = await reader.read();
      if (my.signal.aborted || controller !== my) return;
      if (done) throw Error('event stream ended');
      buffer += decoder.decode(value, {stream: true});
      let end;
      while ((end = buffer.indexOf('\n\n')) >= 0) {
        const frame = buffer.slice(0, end); buffer = buffer.slice(end + 2);
        if (frame.startsWith('data: ')) deliver(JSON.parse(frame.slice(6)));
        else for (const c of clients.values()) send(c, 'ping');
      }
    }
  } catch (e) {
    if (my.signal.aborted || controller !== my) return;
    for (const c of clients.values()) send(c, 'error', {error: e.message});
    schedule(1500);
  }
}
self.onconnect = event => {
  const port = event.ports[0];
  port.onmessage = ({data}) => {
    if (data.type === 'unsubscribe') { clients.delete(port); schedule(); }
    if (data.type === 'subscribe' && typeof data.session === 'string' && typeof data.since === 'string') {
      clients.set(port, {port, session: data.session, since: data.since, token: data.token}); schedule();
    }
  };
  port.onmessageerror = () => { clients.delete(port); schedule(); };
  port.start();
};
