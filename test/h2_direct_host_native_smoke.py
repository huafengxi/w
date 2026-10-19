#!/usr/bin/env python3
"""Own ordinary P + real pi OS smoke; two read-only RPCs, no model request.

PI_TEST_BIN must explicitly select an isolated existing pi installation.
No browser, receiver, dispatch, duplicate-host or production activation test.
"""
import json
import os
from pathlib import Path
import shlex
import signal
import socket
import sys
import tempfile
import time

ROOT = Path(tempfile.mkdtemp(prefix='h2native-')).resolve()
for name in ('home', 'tmp', 'cwd', 'runtime', 'config'):
    (ROOT / name).mkdir(mode=0o700)
os.environ['W_WORKSPACE'] = str(ROOT)
os.environ['W_AGENTD_DIR'] = os.environ['H2_A_SOURCE']
sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parent)]
import h2_direct_host as H

pi = Path(os.environ['PI_TEST_BIN']).resolve()
if not pi.is_file():
    raise RuntimeError('explicit PI_TEST_BIN missing')
# An unused localhost-only provider prevents accidental use of global settings.
(ROOT / 'config/models.json').write_text(json.dumps({'providers': {'fixture': {
    'baseUrl': 'http://127.0.0.1:1/v1', 'apiKey': 'fixture-not-a-secret',
    'api': 'openai-completions', 'models': [{'id': 'fixture', 'name': 'fixture',
    'reasoning': False, 'input': ['text'], 'contextWindow': 32000, 'maxTokens': 32,
    'cost': {'input': 0, 'output': 0, 'cacheRead': 0, 'cacheWrite': 0}}]}}}))
(ROOT / 'config/settings.json').write_text(json.dumps({'retry': {'enabled': False},
                                                    'compaction': {'enabled': False}}))
wrapper = ROOT / 'native-pi'
wrapper.write_text('#!/bin/sh\nexec ' + shlex.quote(str(pi)) +
                   ' --provider fixture --model fixture --no-skills --no-prompt-templates --no-extensions "$@"\n')
wrapper.chmod(0o700)
env = dict(HOME=str(ROOT / 'home'), TMPDIR=str(ROOT / 'tmp'),
           PATH=os.environ['PATH'], PI_CODING_AGENT_DIR=str(ROOT / 'config'),
           PI_OFFLINE='1', NO_PROXY='127.0.0.1,localhost', W_WORKSPACE=str(ROOT))
life = H.Lifecycle(str(ROOT / 'history.jsonl'), 'native-smoke', str(ROOT / 'cwd'),
                   str(ROOT / 'runtime'), str(Path(os.environ['H2_P_SOURCE']) / 'pi_host.py'),
                   os.environ['H2_A_SOURCE'], env, str(wrapper), claim=lambda path: None)
known = []
result = dict(root=str(ROOT), pi=str(pi), submittedPrompt=False,
              scope='post-launch get_state acceptance, two read-only RPCs and stop')
print('NATIVE_SMOKE_ROOT=' + str(ROOT), flush=True)
try:
    d = life.ensure()
    known.extend(d['known'])
    facts, _ = life.facts(d)
    proof = d['startupAcceptance']
    assert proof['peerPid'] == d['host']['pid'] and proof['success'] is True
    assert list((ROOT / 'runtime').rglob('*.lock')) == []
    result.update(instance=d['instance'], host=d['host'], child=d['child'], facts=facts,
                  startupAcceptance=proof, fileLocks=0)
    assert str(facts['hostProcStart']) == d['host']['start']
    assert str(facts['childProcStart']) == d['child']['start']
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(25)
        sock.connect(life.sock)
        peer = H._PROCESS_TOOLS.peer_credentials(sock)
        assert peer[:2] == (d['host']['pid'], os.getuid()), peer
        result['peer'] = peer
        with sock.makefile('rb') as reader:
            for command in ('get_state', 'get_commands'):
                rid = 'os-smoke-' + command
                sock.sendall(json.dumps(dict(type=command, id=rid)).encode() + b'\n')
                deadline = time.monotonic() + 25
                while time.monotonic() < deadline:
                    raw = reader.readline()
                    if not raw:
                        raise RuntimeError('native channel closed before response')
                    response = json.loads(raw)
                    if response.get('type') == 'response' and response.get('id') == rid:
                        assert response.get('success'), response
                        result[command] = dict(success=True, command=response.get('command'))
                        break
                else:
                    raise TimeoutError(command)
    stopped = life.operate('stop', d['instance'])
    obs = life.status()
    assert obs['state'] == 'stopped' and obs['settled'], obs
    result.update(stopped=obs, startupAndStopPassed=True)
finally:
    # Only this invocation's exact recorded identities; retain all file evidence.
    current = life.load()
    if current:
        known.extend(i for i in current.get('known', []) if i)
        known.extend(current[k] for k in ('host', 'child') if current.get(k))
        try:
            start, _ = life.facts(current)
            child = H.identity(start['childPid'])
            if child and child['start'] == str(start['childProcStart']):
                known.append(child)
        except (OSError, ValueError, KeyError, RuntimeError):
            pass
    for item in known:
        actual = H.identity(item['pid'])
        if H.same(actual, item) and H.live(actual):
            H.signal_identity(item, signal.SIGKILL)
    for handle in life.handles.values():
        handle.wait(timeout=10)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        remaining = [i for i in known if H.same(H.identity(i['pid']), i) and H.live(H.identity(i['pid']))]
        if not remaining:
            break
        time.sleep(.02)
    result['remainingLive'] = remaining
    result['ok'] = result.get('startupAndStopPassed') is True and not remaining
    (ROOT / 'result.json').write_text(json.dumps(result, indent=2))
    assert not remaining, remaining
print('PASS native P/pi start facts, socket peer, RPC and checked stop; no model request', flush=True)
