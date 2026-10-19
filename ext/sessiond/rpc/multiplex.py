# -*- type=script -*-
"""One authenticated SSE connection for a browser's sessions on this host.

Each session retains its own cursor, gap detection and subscription budget.
Bridge notifications wake the generator; no polling or extra reader threads.
"""
import json
import threading

from core.stream import ClosingIterator
from ext.sessiond import bridge

MAX_SESSIONS = 32


def frame(session, event, eid=None):
    return ('data: ' + json.dumps(dict(session=session, event=event, id=eid),
                                  ensure_ascii=False) + '\n\n').encode()


def interp(store, subscriptions='', **kw):
    def error(message, status='400 Bad Request'):
        return dict(type='application/json', http_status=status), json.dumps(dict(ok=False, error=message))
    try:
        requests = json.loads(subscriptions)
        if not isinstance(requests, list) or not 1 <= len(requests) <= MAX_SESSIONS:
            raise ValueError('expected 1-%d session subscriptions' % MAX_SESSIONS)
        names = set()
        for item in requests:
            if (not isinstance(item, dict) or set(item) != {'session', 'since'}
                    or not isinstance(item['session'], str) or not item['session']
                    or not isinstance(item['since'], str) or item['session'] in names):
                raise ValueError('invalid or duplicate session subscription')
            names.add(item['session'])
        channels = [(item, bridge.get_bridge(item['session'], store=store)) for item in requests]
    except (ValueError, TypeError) as e:
        return error(str(e))

    wake = threading.Event()
    reserved = []

    def release():
        for b in reserved:
            with b.cond:
                b.stream_wakes.discard(wake)
                b.subscribers -= 1
        reserved.clear()

    states = []
    for item, b in channels:
        with b.cond:
            if b.subscribers >= bridge.MAX_STREAMS_PER_BRIDGE:
                limited = True
            else:
                limited = False
                b.subscribers += 1
                b.stream_wakes.add(wake)
                reserved.append(b)
        if limited:
            release()
            return error('session stream limit reached', '429 Too Many Requests')
        sent, gap = b.resolve_since(item['since'])
        states.append(dict(name=item['session'], bridge=b, sent=sent, gap=gap))

    def generate():
        yield b': stream open\n\n'
        while states:
            # Clear BEFORE sampling to avoid losing a notification between read and wait.
            wake.clear()
            for state in list(states):
                b, sent, name = state['bridge'], state['sent'], state['name']
                with b.cond:
                    batch = [e for e in b.ring if e[0] > sent]
                    oldest = b.ring[0][1] if b.ring else None
                if state.pop('gap', False) or (batch and batch[0][0] > sent + 1):
                    yield frame(name, dict(type='webgw.gap', gap=True, oldest=oldest))
                for seq, eid, event in batch:
                    state['sent'] = seq
                    yield frame(name, event, eid)
                if b.stream_closed():
                    yield frame(name, dict(type='sessiond.stream_closed'))
                    states.remove(state)
            # Subscription changes replace this connection. A short, tiny ping
            # discovers closed browser sockets promptly and releases reservations.
            if states and not wake.wait(1):
                yield b': ping\n\n'

    return dict(type='text/event-stream', extra_headers=[('Cache-Control', 'no-cache')]), \
        ClosingIterator(generate(), release)
