#!/usr/bin/env python3
"""Offline runner: existing tools, new outer HOME/TMPDIR/cwd, retained logs.

python -B test/h2_direct_host_run.py --p-source <fixed P export> --a-source <fixed A export>
The input sources are read-only; no production HTTP/socket/model/browser calls.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ap = argparse.ArgumentParser()
ap.add_argument('--p-source', required=True)
ap.add_argument('--a-source', required=True)
ap.add_argument('--cli-source', required=True, help='read-only original caller session-ctl.py')
a = ap.parse_args()
W = Path(__file__).resolve().parents[1]
P, A = Path(a.p_source).resolve(), Path(a.a_source).resolve()
ROOT = Path(tempfile.mkdtemp(prefix='h2run-'))
for n in ('home', 'tmp', 'cwd', 'workspace'):
    (ROOT / n).mkdir(mode=0o700)
node = shutil.which('node')
cli = ROOT / 'caller-session-ctl.py'
cli.write_bytes(Path(a.cli_source).resolve().read_bytes())
cli.chmod(0o444)
env = dict(HOME=str(ROOT / 'home'), TMPDIR=str(ROOT / 'tmp'),
           PATH=os.pathsep.join([str(Path(sys.executable).parent), str(Path(node).parent) if node else '/usr/bin', '/usr/bin', '/bin']),
           H2_P_SOURCE=str(P), H2_A_SOURCE=str(A), W_AGENTD_DIR=str(A),
           H2_CLI_SOURCE=str(cli), H2_NODE=node or '',
           W_WORKSPACE=str(ROOT / 'workspace'), AGENTD_DIR=str(A),
           PYTHONDONTWRITEBYTECODE='1', LANG='C.UTF-8')
results = []
manifest = json.loads((W / 'test/h2_direct_host_manifest.json').read_text())
assert hashlib.sha256(cli.read_bytes()).hexdigest() == manifest['crud']['caller_cli_sha256']
for project, base in (('P', P), ('A', A)):
    for name, expected in manifest[project]['files'].items():
        actual = hashlib.sha256((base / name).read_bytes()).hexdigest()
        assert actual == expected, (project, name, actual)
print('H2_RUN_ROOT=' + str(ROOT), flush=True)


def run(label, cmd):
    log = ROOT / (label + '.log')
    row = dict(label=label, command=list(map(str, cmd)), env=env,
               cwd=str(ROOT / 'cwd'), started=time.time(), log=str(log))
    with log.open('w') as f:
        f.write(json.dumps(row) + '\n'); f.flush()
        try:
            row['rc'] = subprocess.run(cmd, env=env, cwd=ROOT / 'cwd', stdout=f, stderr=subprocess.STDOUT,
                                       timeout=180).returncode
        except subprocess.TimeoutExpired:
            row['rc'] = 124
    text = log.read_text(errors='replace')
    row.update(ended=time.time(), warnings=[x for x in text.splitlines() if 'Warning' in x],
               skip=[x for x in text.splitlines() if 'skip' in x.lower()])
    results.append(row)
    (ROOT / 'results.json').write_text(json.dumps(dict(manifest=manifest, sourceW=str(W), results=results), indent=2))
    print(label, 'rc=' + str(row['rc']), flush=True)


# Guard verification only against this runner's newly created harmless bait.
bait = ROOT / 'bait'; bait.mkdir()
allowed = bait / 'authorized'; allowed.mkdir()
outside = bait / 'outside'; outside.mkdir(); (outside / 'keep').write_text('harmless')
run('root-guard', [sys.executable, '-B', '-c',
    "import sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);from safe_delete import safe_rmtree;"
    "a,b=map(Path,sys.argv[2:]);assert not safe_rmtree(a,authorized_root=a)[0];"
    "assert not safe_rmtree(b,authorized_root=a)[0];assert (b/'keep').read_text()=='harmless'", str(A), str(allowed), str(outside)])
run('h2-acceptance', [sys.executable, '-B', str(W / 'test/h2_direct_host_test.py')])
run('h2-crud', [sys.executable, '-B', str(W / 'test/h2_direct_host_crud_test.py')])
for name in ('resolve_agent_edge', 'session_lifecycle', 'dormant_send_revive', 'session_ops_edge', 'chat_backend'):
    run('W-' + name, [sys.executable, '-B', str(W / ('test/' + name + '.py'))])
if node:
    run('W-chat_frontend', [node, str(W / 'test/chat_frontend.cjs')])
else:
    results.append(dict(label='W-chat_frontend', rc=125, error='existing node missing, not skipped as PASS'))
# Shared socket/generic core is unchanged but exercised in this fresh environment.
run('P-generic', [sys.executable, '-B', str(P / 'test_generic.py')])
run('P-dependency', [sys.executable, '-B', str(P / 'test_dependency.py')])
sys.exit(0 if all(r['rc'] == 0 for r in results) else 1)
