"""Minimal ordinary runtime through default HTTP/CLI assembly, no test adapter.

Fake pi is deterministic. Every writable path/process belongs to a fresh root;
startup audits observe REAL Popen boundaries, including a pre-spawn failure.
"""
import ctypes
import importlib.util
import json
import os
import signal
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest

W = Path(__file__).resolve().parents[1]
CHILD = r'''
import importlib.util,json,os,pathlib,sys
sys.path.insert(0,sys.argv[1])
from ext.sessiond.bootstrap import configure
from ext.sessiond import sessions as P,bridge as B
from ext.sessiond.rpc import api
from ext.sessiond import lifecycle as H
from stores.store import RootStore
from stores.dir_store import DirStore
assert not pathlib.Path(os.environ['W_SESSION_RUNTIME']).exists()
original=(P.create_files,P.list_files,B.Bridge,B.get_bridge)
registry=configure(required=False)
assert original==(P.create_files,P.list_files,B.Bridge,B.get_bridge)
assert registry.rows()==[]
assert not pathlib.Path(os.environ['W_SESSION_RUNTIME']).exists(),'list must not create runtime state'
assert configure() is registry
assert not any(n in sys.modules for n in ('h2_direct_host','h2_direct_host_crud','unittest.mock'))
store=RootStore([('/',DirStore(P.WS))])
def call(**args):
 meta,body=api.interp(store,**args);return json.loads(body)
spec=importlib.util.spec_from_file_location('deployment_cli',sys.argv[2]);cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
assert cli._sessions() is P and cli._registry() is registry
spawn_audits=[]
fail_next=[False]
def audit(event,args):
 if event!='subprocess.Popen' or os.environ['W_SESSION_HOST_ENTRY'] not in args[1]:return
 assert args[1][-1]=='-','configuration must be one-shot stdin, not a file'
 for path in pathlib.Path(P.sessions_dir()).glob('*/*.agent'):
  doc=json.loads(path.read_text());control=doc.get('runtime',{})
  assert control.get('phase') not in ('starting','launching','verifying','unverified'),doc
  assert 'instance' not in control and 'launchAttempt' not in doc
  assert not any(p.is_dir() for p in path.parent.iterdir()),'no per-instance directory'
 spawn_audits.append(list(args[1]))
 if fail_next[0]:
  raise OSError('fixture failure at real pre-spawn boundary')
sys.addaudithook(audit)
created=registry.create('good');name=created['name'];site=created['agent_site']
a_path=pathlib.Path(created['agent_path']);history=pathlib.Path(created['session_path'])
assert a_path.parent==pathlib.Path(P.sessions_dir())/name
assert set(p.name for p in a_path.parent.iterdir())=={name+'.agent'}
assert 'runtime' not in json.loads(a_path.read_text())
a=call(op='attach',session=site);assert a['ok'],a
first=a['gen'];b=B.get_bridge(site,store=store);life=b.sup.life
assert life.local==a_path.parent
raw_host=life.current.read_bytes();actual=json.loads(raw_host)
assert first==H.Lifecycle.process_key(actual['host'])
assert 'instance' not in actual and 'startupAcceptance' not in actual
assert life.load()['startupAcceptance']['success'] is True
assert not list(pathlib.Path(os.environ['W_SESSION_RUNTIME']).rglob('*'))
for forbidden in ('binding.json','current.json','declaration.json','record.json','s.pid','s.stderr.log','facts.jsonl'):
 assert not (life.local/forbidden).exists(),forbidden
assert not any(p.is_dir() for p in life.local.iterdir())
assert {name+'.agent',name+'.jsonl','host.json','host.log','s'}<=set(p.name for p in life.local.iterdir())
actual_env=b.request({'type':'identity'})['data']['env']
assert actual_env['DISPATCH_PROFILE']=='good' and 'AGENTD_DIR' not in actual_env
assert actual_env['SESSIOND_SESSION_FILE']==str(history)
# Web updates are not writes to P's state file.
d=life.load();life.save(d);assert life.current.read_bytes()==raw_host
old_handle=life.handles[first];old_host=d['host']
bad=call(op='reload',session=site,expected_instance=first,expected_registration='0'*32)
assert not bad['ok'] and 'registration fence' in bad['error'],bad
r=call(op='reload',session=site,expected_instance=first);assert r['ok'],r
assert r['gen']!=first
before_callback=a_path.read_bytes()
life._reap(old_handle,old_host)
assert a_path.read_bytes()==before_callback,'late old wait must not overwrite current control'
assert not b.stream_closed(),'reload is not a registration deletion'
stale=call(op='clear',session=site,expected_instance=first);assert not stale['ok'],stale
r=call(op='clear',session=site,expected_instance=r['gen']);assert r['ok'],r
# Losing actual P state is unknown, not an invitation to start over.
parked=life.local/'fixture-parked-host'
life.current.rename(parked)
try:
 launches=len(spawn_audits)
 assert not call(op='attach',session=site)['ok']
 assert len(spawn_audits)==launches
finally:parked.rename(life.current)
# Native pi can have a live RPC process before any JSONL has been persisted.
history.unlink()
r=call(op='delete_session',session=site,confirm='1',expected_instance=r['gen'])
assert r['ok'] and r['process']['settled'],r
assert not a_path.exists() and not life.current.exists()
assert (pathlib.Path(P.WS)/'agents/bot'/name/'inbox').is_dir()
assert not b.sup.send({'type':'effect','value':'forbidden'}),'cached deleted client cannot revive'
assert b.stream_closed()
unstarted=registry.create('good')
assert call(op='delete_session',session=unstarted['agent_site'],confirm='1')['ok']
# No synthetic state is left by Popen failing before it creates a process.
failed=registry.create('good');failed_path=pathlib.Path(failed['agent_path']);before=failed_path.read_bytes()
fail_next[0]=True
assert not call(op='attach',session=failed['agent_site'])['ok']
fail_next[0]=False
assert failed_path.read_bytes()==before
assert not (failed_path.parent/'host.json').exists()
assert registry.rows()[0]['state']=='dormant','diagnostic log is not a launch marker'
retried=call(op='attach',session=failed['agent_site']);assert retried['ok'],retried
assert call(op='delete_session',session=failed['agent_site'],confirm='1',expected_instance=retried['gen'])['ok']
# Existing history with no host facts needs explicit OFFLINE operator adoption;
# no imported-history file/permission is written or reused by normal requests.
old=registry.create('good');doc,path,history=registry.declaration(old['name'])
raw=(json.dumps(dict(type='session',cwd=P.WS))+'\n'+json.dumps(dict(type='message',id='preserved'))+'\n').encode();history.write_bytes(raw)
assert not call(op='attach',session=old['agent_site'])['ok']
life=registry.life(old['name'],doc,history)
with registry.lock(),life.lock():life._start()  # harness owns this stopped history
opened=call(op='attach',session=old['agent_site']);assert opened['ok'],opened
assert history.read_bytes()==raw
assert call(op='delete_session',session=old['agent_site'],confirm='1',expected_instance=opened['gen'])['ok']
assert not list(pathlib.Path(P.sessions_dir()).rglob('*.lock'))
assert len(spawn_audits)>=6
print(json.dumps(dict(ok=True,minimalLayout=True,noPreSpawnMarker=True,nativeIdentity=True,
                     singleWriters=True,lateWaitRejected=True,historyPreserved=True,
                     missingFactsUnknown=True,spawnAudits=len(spawn_audits))))
'''


class DefaultFactory(unittest.TestCase):
    def test_default_minimal_runtime_and_no_launch_marker(self):
        root = Path(tempfile.mkdtemp(prefix='wp-default-', dir='/tmp')).resolve()
        print('DEFAULT_ROOT=' + str(root), flush=True)
        ws = root / 'ws'; (ws / 'bots/profiles').mkdir(parents=True)
        (ws / 'bots/profiles/good.json').write_text(json.dumps(dict(form='interactive', cwd='.')))
        hostmap = root / 'host-id'; hostmap.write_text(socket.gethostname() + ' test\n')
        fake = root / 'fakepi'
        fake.write_text('#!' + sys.executable + '\nimport sys;sys.path.insert(0,' + repr(os.environ['H2_P_SOURCE']) + ')\n' + (W / 'test/h2_direct_host_fakepi.py').read_text())
        fake.chmod(0o700)
        home = root / 'home'; home.mkdir()
        caller = root / 'caller'; caller.symlink_to(root, target_is_directory=True)
        env = dict(HOME=str(home), TMPDIR=str(root), PATH=os.defpath, PYTHONDONTWRITEBYTECODE='1',
                   W_WORKSPACE=str(caller/'ws'), W_AGENTD_DIR=os.environ['H2_A_SOURCE'], W_HOST_ID_FILE=str(hostmap),
                   W_SESSION_PI_BIN=str(fake), W_SESSION_HOST_ENTRY=str(Path(os.environ['H2_P_SOURCE'])/'pi_host.py'),
                   W_SESSION_RUNTIME=str(caller/'runtime'))
        if sys.platform.startswith('linux'):
            self.assertEqual(ctypes.CDLL(None).prctl(36, 1, 0, 0, 0), 0)
        try:
            result = subprocess.run([sys.executable, '-B', '-c', CHILD, str(W), os.environ['H2_CLI_SOURCE']],
                                    cwd=root, env=env, text=True, capture_output=True, timeout=30)
            (root/'stdout').write_text(result.stdout); (root/'stderr').write_text(result.stderr)
        finally:
            spec = importlib.util.spec_from_file_location('fixture_process', Path(os.environ['H2_P_SOURCE'])/'local_process.py')
            tools = importlib.util.module_from_spec(spec); spec.loader.exec_module(tools)
            known = []
            for record in (ws/'run/sessiond').glob('*/host.json'):
                d = json.loads(record.read_text())
                known.extend(d[k] for k in ('host', 'child') if d.get(k))
            for i in known:
                now = tools.identity(i['pid'])
                if now and all(now[k] == i[k] for k in ('pid', 'start', 'boot', 'uid', 'pgid', 'sid')) and now['state'] not in ('Z', 'X'):
                    sys.path.insert(0, str(W))
                    from ext.sessiond.lifecycle import configure_process_tools, signal_identity
                    configure_process_tools(Path(os.environ['H2_P_SOURCE'])/'pi_host.py')
                    signal_identity(i, signal.SIGKILL)
            for pid in {i['pid'] for i in known}:
                try: os.waitpid(pid, 0)
                except ChildProcessError: pass
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(json.loads(result.stdout.strip().splitlines()[-1])['ok'])

    def test_server_configuration_missing_is_not_a_pipe_fallback(self):
        env = dict(HOME='/tmp', PATH=os.defpath, PYTHONDONTWRITEBYTECODE='1')
        result = subprocess.run([sys.executable, '-B', '-c',
            "import sys;sys.path.insert(0,sys.argv[1]);from ext.sessiond.bootstrap import configure;configure()", str(W)],
            env=env, text=True, capture_output=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('W_SESSION_RUNTIME', result.stderr)


if __name__ == '__main__':
    unittest.main(verbosity=2)
