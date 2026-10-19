#!/usr/bin/env python3
"""Deterministic RPC load, never a real model. Durable effects identify replay."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from local_process import starttime

session = Path(sys.argv[sys.argv.index('--session') + 1])
name = sys.argv[sys.argv.index('-n') + 1]
root = session.parent

def ident():
    return dict(pid=os.getpid(), start=str(starttime(os.getpid())), name=name,
                stdio=[os.fstat(i).st_ino for i in (0, 1, 2)])


def emit(o):
    print(json.dumps(o), flush=True)


(root / 'ready.json').write_text(json.dumps(ident()))
if not session.exists() or not session.stat().st_size:
    session.write_text(json.dumps(dict(type='session', cwd=str(root), name=name)) + '\n')

hold_eof = False
for line in sys.stdin:
    cmd = json.loads(line)
    t = cmd['type']
    if t == 'get_state':
        with (root / 'startup-requests').open('a') as log:
            log.write(json.dumps(dict(pid=os.getpid(), id=cmd.get('id'))) + '\n')
        mode_path = root / 'startup-mode'
        mode = mode_path.read_text().strip() if mode_path.exists() else 'ok'
        if mode == 'eof':
            sys.exit(0)
        if mode == 'silent':
            continue
        if mode == 'wrong-id':
            emit(dict(type='response', id='not-' + str(cmd.get('id')), command=t, success=True))
            continue
        if mode == 'rejected':
            emit(dict(type='response', id=cmd.get('id'), command=t, success=False, error='fixture rejection'))
            continue
        if mode == 'wrong-command':
            emit(dict(type='response', id=cmd.get('id'), command='get_entries', success=True))
            continue
    if t == 'effect':
        with session.open('a') as f:
            entry = dict(type='message', id=cmd['value'], value=cmd['value'])
            if 'text' in cmd:
                entry['message'] = dict(role='user', content=cmd['text'])
            f.write(json.dumps(entry) + '\n')
        with (root / 'effects').open('a') as f:
            f.write(cmd['value'] + '\n')
    elif t == 'flood':
        if cmd.get('waitForGate'):
            (root / 'flood.armed').write_text('command received')
            while not (root / 'flood.go').exists():
                time.sleep(.01)
        for n in range(7000):
            emit(dict(type='load', n=n, payload='x' * 4096))
        (root / 'flood.done').write_text('done')
    elif t == 'exit':
        (root / 'exit.requested').write_text(str(cmd.get('code', 0)))
        sys.exit(cmd.get('code', 0))
    elif t == 'hold_eof':
        hold_eof = True
    elif t == 'ignore_term':
        hold_eof = True
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    elif t == 'descendant':
        p = subprocess.Popen([sys.executable, '-c',
                              'import time; time.sleep(120)'], stdin=subprocess.DEVNULL)
        (root / 'descendant.json').write_text(json.dumps(dict(pid=p.pid, start=str(starttime(p.pid)))))
    elif t == 'get_entries':
        data = dict(entries=[json.loads(x) for x in session.read_text().splitlines()], leafId='leaf')
        emit(dict(type='response', id=cmd.get('id'), command=t, success=True, data=data))
        continue
    elif t == 'get_commands':
        emit(dict(type='response', id=cmd.get('id'), command=t, success=True,
                  data=dict(commands=[])))
        continue
    emit(dict(type='response', id=cmd.get('id'), command=t, success=True, data=ident()))

if hold_eof:
    time.sleep(120)
