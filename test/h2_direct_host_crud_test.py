#!/usr/bin/env python3
"""Independent web OS processes, real original CRUD paths, synthetic load only."""
import concurrent.futures
import ctypes
import json
import os
from pathlib import Path
import selectors
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest

W = Path(__file__).resolve().parents[1]
BASE = Path(tempfile.mkdtemp(prefix='hc-')).resolve()
for n in ('home', 'tmp', 'cwd', 'rt'):
    (BASE / n).mkdir(mode=0o700)
os.environ.update(HOME=str(BASE/'home'), TMPDIR=str(BASE/'tmp'), W_WORKSPACE=str(BASE),
                  W_AGENTD_DIR=os.environ['H2_A_SOURCE'])
os.chdir(BASE/'cwd')
sys.path[:0] = [str(W), str(W/'test')]
import h2_direct_host as H
H.configure_process_tools(Path(os.environ['H2_P_SOURCE']) / 'pi_host.py')
if sys.platform.startswith('linux'):
    assert ctypes.CDLL(None).prctl(36, 1, 0, 0, 0) == 0
print('CRUD_ROOT='+str(BASE), flush=True)


def record(**obj):
    with (BASE/'wire.jsonl').open('a') as f:
        f.write(json.dumps(dict(time=time.time(), **obj))+'\n')


def wait(fn, seconds=8):
    end = time.monotonic()+seconds
    while time.monotonic()<end:
        x=fn()
        if x:return x
        time.sleep(.02)
    raise TimeoutError('condition not reached')


class Client:
    def __init__(self, fixture):
        self.f=fixture
        self.err=(fixture.root/('web%d.stderr'%len(fixture.clients))).open('wb')
        self.p=subprocess.Popen([sys.executable,'-B',str(W/'test/h2_direct_host_crud_client.py'),str(fixture.config)],
              env=fixture.env(),cwd=BASE/'cwd',stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.err)
        self.err.close();fixture.clients.append(self)
        record(webStart=self.p.pid, env=fixture.env(), cwd=str(BASE/'cwd'))

    def call(self, **req):
        self.p.stdin.write(json.dumps(req).encode()+b'\n');self.p.stdin.flush()
        with selectors.DefaultSelector() as sel:
            sel.register(self.p.stdout,selectors.EVENT_READ)
            if not sel.select(25):raise TimeoutError('web response timeout')
        raw=self.p.stdout.readline()
        if not raw:raise RuntimeError('web died '+str(self.f.root))
        obj=json.loads(raw);record(web=self.p.pid, request=req,response=obj)
        return obj

    def request(self, **req):
        obj=self.call(**req)
        assert obj['ok'],obj
        return obj['result']

    def close(self, kill=False):
        if self.p.poll() is None:
            if kill:self.p.kill()
            else:self.p.stdin.close()
            self.p.wait(10)
        for s in (self.p.stdin,self.p.stdout):
            if not s.closed:s.close()
        record(webEnd=self.p.pid, rc=self.p.returncode)


class Fixture:
    def __init__(self, key):
        self.root=BASE/key;self.root.mkdir();self.ws=self.root/'ws';self.ws.mkdir()
        p=self.ws/'bots/profiles';p.mkdir(parents=True)
        for n,doc in [('good',dict(form='interactive',cwd='.')),('other',dict(form='interactive',cwd='.')),
                      ('task',dict(form='task')),('escape',dict(form='interactive',cwd='../escape'))]:
            (p/(n+'.json')).write_text(json.dumps(doc))
        self.hostmap=self.root/'host-id';self.hostmap.write_text(socket.gethostname()+' test\n')
        self.fake=self.root/'fakepi';self.fake.write_text('#!'+sys.executable+'\nimport sys; sys.path.insert(0, '+repr(os.environ['H2_P_SOURCE'])+')\n'+(W/'test/h2_direct_host_fakepi.py').read_text());self.fake.chmod(0o700)
        self.runtime=BASE/'rt'/__import__('hashlib').sha256(key.encode()).hexdigest()[:4];self.runtime.mkdir(mode=0o700)
        self.options=dict(runtime=str(self.runtime),host_entry=str(Path(os.environ['H2_P_SOURCE'])/'pi_host.py'),
                          agentd=os.environ['H2_A_SOURCE'],stop_timeout=.3)
        self.config=self.root/'options.json';H.atomic(self.config,self.options)
        self.clients=[];self.known=[]
        self.cli=self.root/'session-ctl.py';shutil.copyfile(os.environ['H2_CLI_SOURCE'],self.cli)

    def env(self):
        return dict(HOME=str(BASE/'home'),TMPDIR=str(BASE/'tmp'),PATH='/usr/bin:/bin',
                    W_WORKSPACE=str(self.ws),W_AGENTD_DIR=os.environ['H2_A_SOURCE'],
                    W_HOST_ID_FILE=str(self.hostmap),W_SESSION_PI_BIN=str(self.fake),
                    AGENTD_ROOT=str(self.ws),H2_CLI_SOURCE=str(self.cli),PYTHONDONTWRITEBYTECODE='1')

    def remember(self):
        for p in self.runtime.glob('*/current.json'):
            d=json.loads(p.read_text())
            self.known.extend(i for i in d.get('known',[]) if i)
            self.known.extend(d[k] for k in ('host','child') if d.get(k))
        for p in (self.ws/'run/sessiond').glob('descendant.json'):
            i=H.identity(json.loads(p.read_text())['pid'])
            if i:self.known.append(i)

    def current(self, name):
        history=self.ws/'run/sessiond'/(name+'.jsonl')
        key=__import__('hashlib').sha256(os.fsencode(str(history))).hexdigest()[:16]
        return self.runtime/key/'current.json'

    def cleanup(self):
        self.remember()
        for c in self.clients:c.close()
        for i in self.known:
            try:
                if H.same(H.identity(i['pid']),i) and H.live(H.identity(i['pid'])):H.signal_identity(i,signal.SIGKILL)
            except ProcessLookupError:pass
        for i in self.known:
            try:os.waitpid(i['pid'],0)
            except ChildProcessError:pass
        record(cleanup='exact recorded identities only; no roots removed', known=self.known,
               live=[i for i in self.known if H.same(H.identity(i['pid']),i) and H.live(H.identity(i['pid']))])


class CRUD(unittest.TestCase):
    def setUp(self):self.f=Fixture(self.id().split('.')[-1])
    def tearDown(self):self.f.cleanup()
    def create(self,c,name='abcd'):
        return c.request(special='create',profile='good',names=[name])
    def attach(self,c,doc):
        a=c.request(op='attach',session=doc['agent_site'])['body'];self.assertTrue(a['ok'],a);self.f.remember();return a
    def delete(self,c,doc,token,**kw):
        return c.request(op='delete_session',session=doc['agent_site'],confirm=1,
                         expected_instance=token,expected_registration=doc['registration'],**kw)['body']
    def rpc(self,c,doc,t,**kw):return c.request(special='rpc',session=doc['agent_site'],command=dict(type=t,**kw))

    def test_A_cli_create_web_death_same_instance_and_delete(self):
        c1=Client(self.f)
        cli=c1.request(special='cli',argv=['new','--profile','good'])
        self.assertEqual(cli['rc'],0,cli)
        rows=c1.request(op='list_sessions')['body']['sessions'];self.assertEqual(len(rows),1)
        row=rows[0];self.assertEqual(row['state'],'dormant')
        name=row['name'];decl=self.f.ws/'run/sessiond'/(name+'.agent')
        doc=dict(agent_site=row['agent'],name=name,registration=json.loads(decl.read_text())['h2Registration'])
        inbox=self.f.ws/'agents/bot'/name/'inbox';(inbox/'keep').write_text('preexisting inbox payload')
        a=self.attach(c1,doc)
        self.rpc(c1,doc,'effect',value='once-only',text='  Auto first title\nsecond line')
        before=self.rpc(c1,doc,'identity')['data']
        ident=c1.request(special='identity',session=doc['agent_site']);d=json.loads(self.f.current(name).read_text())
        cfg=json.loads((self.f.current(name).parent/d['instance']/'declaration.json').read_text())
        for k,v in dict(SESSIOND_SESSION_FILE=str(decl.with_suffix('.jsonl')),W_WORKSPACE=str(self.f.ws),
                        AGENTD_ROOT=str(self.f.ws),AGENTD_DIR=self.f.options['agentd'],DISPATCH_PROFILE='good').items():
            self.assertEqual(cfg['env'][k],v)
        self.assertEqual(cfg['cwd'],str(self.f.ws));self.assertEqual(cfg['sessionName'],name)
        renamed=c1.request(op='rename_session',session=doc['agent_site'],title='Visible title')['body'];self.assertTrue(renamed['ok'])
        self.assertEqual(json.loads(decl.read_text())['name'],name)
        self.assertEqual(c1.request(op='session_tabs')['body']['rows'][0]['title'],'Visible title')
        self.assertTrue(c1.request(op='rename_session',session=doc['agent_site'],title='')['body']['ok'])
        auto=c1.request(op='list_sessions')['body']['sessions'][0]
        self.assertEqual((auto['title'],auto['titleSource']),('Auto first title','auto'))
        c1.close(kill=True)
        self.assertTrue(H.live(H.identity(d['host']['pid'])))
        c2=Client(self.f);a2=self.attach(c2,doc);self.assertEqual(a2['gen'],a['gen'])
        self.assertEqual(c2.request(special='identity',session=doc['agent_site'])['handles'],[])
        self.assertEqual(self.rpc(c2,doc,'identity')['data'],before)
        self.assertEqual(c2.request(op='list_sessions')['body']['sessions'][0]['state'],'running')
        history=decl.with_suffix('.jsonl');self.assertIn('once-only',history.read_text())
        self.assertEqual((history.parent/'effects').read_text(),'once-only\n')
        self.rpc(c2,doc,'descendant');self.f.remember()
        deleted=self.delete(c2,doc,a['gen']);self.assertTrue(deleted['ok'],deleted)
        stopped=json.loads(self.f.current(name).read_text())
        self.assertEqual(len(stopped['known']),3)
        self.assertFalse(any(H.live(H.identity(i['pid'])) for i in stopped['known']))
        self.assertFalse(decl.exists());self.assertFalse(history.exists())
        self.assertEqual((inbox/'keep').read_text(),'preexisting inbox payload')
        self.assertEqual(c2.request(op='session_tabs')['body']['rows'],[])
        self.assertFalse(self.delete(c2,doc,a['gen'])['ok'])
        record(crossWeb=dict(first=ident,second=a2,config=cfg,identity=before,delete=deleted))

    def test_B_delete_race_attach_cached_web_and_late_old_instance(self):
        c1,c2=Client(self.f),Client(self.f);doc=self.create(c1);a=self.attach(c1,doc)
        self.attach(c2,doc)
        c2.request(special='stream_open',session=doc['agent_site'])
        reload=c1.request(op='reload',session=doc['agent_site'],expected_instance=a['gen'])['body']
        self.assertTrue(reload['ok'],reload);self.f.remember()
        old=self.delete(c2,doc,a['gen']);self.assertFalse(old['ok'])
        self.assertEqual(json.loads(self.f.current(doc['name']).read_text())['instance'],reload['gen'])
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            f1=pool.submit(self.delete,c1,doc,reload['gen'])
            f2=pool.submit(c2.request,op='attach',session=doc['agent_site'])
            deleted,attached=f1.result(),f2.result()['body']
        self.assertTrue(deleted['ok'],deleted)
        cached=c2.request(special='cached',session=doc['agent_site']);self.assertFalse(cached['sent'])
        current=json.loads(self.f.current(doc['name']).read_text());self.assertEqual(current['phase'],'deleted')
        self.assertEqual(current['instance'],reload['gen'])
        self.assertFalse(c2.request(op='attach',session=doc['agent_site'])['body']['ok'])
        stream=c2.request(special='stream_finish',session=doc['agent_site'])
        self.assertTrue(stream['terminated']);self.assertEqual(stream['subscribers'],0)
        # Original stream iterator sees the durable tombstone, not an invented
        # natural exit or a socket disconnect being treated as stop success.
        record(race=dict(delete=deleted,attach=attached,cached=cached,current=current))

    def test_C_stop_timeout_files_unchanged_and_no_retry(self):
        c=Client(self.f);doc=self.create(c);a=self.attach(c,doc)
        self.rpc(c,doc,'ignore_term')
        agent=self.f.ws/doc['agent_site'].lstrip('/');history=agent.with_suffix('.jsonl')
        before=(agent.read_bytes(),history.read_bytes())
        result=self.delete(c,doc,a['gen']);self.assertFalse(result['ok']);self.assertIn('stop not confirmed',result['error'])
        self.assertEqual((agent.read_bytes(),history.read_bytes()),before)
        self.assertFalse(self.delete(c,doc,a['gen'])['ok'])
        self.assertEqual(c.request(op='list_sessions')['body']['sessions'][0]['state'],'failed')
        self.assertFalse(c.request(op='attach',session=doc['agent_site'])['body']['ok'])

    def test_D_unknown_identity_stale_fence_path_and_incomplete(self):
        c=Client(self.f);doc=self.create(c);a=self.attach(c,doc)
        agent=self.f.ws/doc['agent_site'].lstrip('/');history=agent.with_suffix('.jsonl');before=(agent.read_bytes(),history.read_bytes())
        denial=c.request(special='inject_identity_denial',op='delete_session',session=doc['agent_site'],confirm=1,
                         expected_instance=a['gen'],expected_registration=doc['registration'])['body']
        self.assertFalse(denial['ok']);self.assertIn('injected identity denial',denial['error'])
        path=self.f.current(doc['name']);d=json.loads(path.read_text());bad=json.loads(json.dumps(d));bad['host']['start']='0';H.atomic(path,bad)
        stale=self.delete(c,doc,a['gen']);self.assertFalse(stale['ok'])
        self.assertEqual(c.request(op='list_sessions')['body']['sessions'][0]['state'],'unknown')
        self.assertEqual((agent.read_bytes(),history.read_bytes()),before)
        H.atomic(path,['malformed runtime record'])  # explicit metadata injection
        self.assertEqual(c.request(op='list_sessions')['body']['sessions'][0]['state'],'unknown')
        self.assertFalse(self.delete(c,doc,a['gen'])['ok'])
        self.assertEqual((agent.read_bytes(),history.read_bytes()),before);H.atomic(path,d)
        wrongReg=c.request(op='delete_session',session=doc['agent_site'],confirm=1,
                           expected_instance=a['gen'],expected_registration='0'*32)['body']
        self.assertFalse(wrongReg['ok']);self.assertIn('registration fence mismatch',wrongReg['error'])
        self.assertFalse(c.request(op='delete_session',session='/escape.agent',confirm=1)['body']['ok'])
        self.assertFalse(c.request(op='delete_session',session=doc['agent_site'],expected_instance=a['gen'],expected_registration=doc['registration'])['body']['ok'])
        outside=self.f.root/'keep';outside.write_text('outside harmless bait')
        (history.parent/'alias.agent').symlink_to(outside)
        self.assertFalse(c.request(op='delete_session',session='/run/sessiond/alias.agent',confirm=1)['body']['ok'])
        self.assertEqual(outside.read_text(),'outside harmless bait')
        (history.parent/'incomplete.agent').write_text('{"host":"test"}')
        rows=c.request(op='list_sessions')['body']['sessions'];self.assertEqual(next(x for x in rows if x['name']=='incomplete')['state'],'unknown')
        self.assertFalse(c.request(op='delete_session',session='/run/sessiond/incomplete.agent',confirm=1)['body']['ok'])
        self.assertTrue(self.delete(c,doc,a['gen'])['ok'])

    def test_E_collisions_whitelist_title_and_registry_create_race(self):
        c1,c2=Client(self.f),Client(self.f)
        doc=self.create(c1)
        agent=self.f.ws/doc['agent_site'].lstrip('/');before=agent.read_bytes()
        other=c2.request(special='create',profile='other',names=['abcd','efgh'])
        self.assertEqual(other['name'],'efgh');self.assertEqual(agent.read_bytes(),before)
        history=agent.parent/'old1.jsonl';history.write_text('orphan history')
        new=c1.request(special='create',profile='good',names=['old1','new1']);self.assertEqual(new['name'],'new1');self.assertEqual(history.read_text(),'orphan history')
        exhausted=c1.call(special='create',profile='good',names=['abcd']*32);self.assertFalse(exhausted['ok'])
        for bad in ['task','missing','../good','escape']:
            self.assertFalse(c1.call(special='create',profile=bad,names=['free'])['ok'])
        self.assertFalse(c1.call(special='create',profile='good',names=['../bad'])['ok'])
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            rows=list(pool.map(lambda c:c.request(special='create',profile='good',names=['race','last']),[c1,c2]))
        self.assertEqual({r['name'] for r in rows},{'race','last'})
        for title in ['','Override']:
            self.assertTrue(c1.request(op='rename_session',session=doc['agent_site'],title=title)['body']['ok'])
        self.assertFalse(c1.request(op='rename_session',session=doc['agent_site'],title='x\ny')['body']['ok'])
        self.assertFalse(c1.request(op='rename_session',session=doc['agent_site'],title='x',profile='other')['body']['ok'])
        self.assertFalse((self.f.ws/'agents/bot/abcd/spec.json').exists())
        self.assertFalse((self.f.ws/'agents/bot/abcd/pid.json').exists())

    def test_F_host_crash_residual_refuses_delete(self):
        c=Client(self.f);doc=self.create(c);a=self.attach(c,doc)
        self.rpc(c,doc,'hold_eof');self.rpc(c,doc,'descendant');self.f.remember()
        path=self.f.current(doc['name']);d=json.loads(path.read_text())
        H.signal_identity(d['host'],signal.SIGKILL)
        wait(lambda:not H.live(H.identity(d['host']['pid'])))
        agent=self.f.ws/doc['agent_site'].lstrip('/');before=(agent.read_bytes(),agent.with_suffix('.jsonl').read_bytes())
        result=self.delete(c,doc,a['gen']);self.assertFalse(result['ok']);self.assertIn('leader lost',result['error'])
        self.assertEqual((agent.read_bytes(),agent.with_suffix('.jsonl').read_bytes()),before)
        self.assertTrue(H.live(H.identity(d['child']['pid'])))

    def test_G_unstarted_delete_and_natural_list_facts(self):
        c=Client(self.f);doc=self.create(c)
        deleted=self.delete(c,doc,'unstarted');self.assertTrue(deleted['ok'],deleted)
        self.assertTrue(deleted['process']['neverStarted'])
        self.assertFalse(self.f.current(doc['name']).exists())
        for name,code,state in [('zero',0,'dormant'),('fail',7,'failed')]:
            doc=self.create(c,name);a=self.attach(c,doc)
            # A real native wait produces the state, not an injected status field.
            self.rpc(c,doc,'exit',code=code)
            def settled():
                d=json.loads(self.f.current(name).read_text())
                return d if d.get('hostWait') else None
            d=wait(settled)
            row=next(x for x in c.request(op='list_sessions')['body']['sessions'] if x['name']==name)
            self.assertEqual(row['state'],state)
            self.assertTrue(self.delete(c,doc,a['gen'])['ok'])
            record(naturalWait=d,listed=row)

    def test_H_actual_ui_notifies_only_real_api_success(self):
        node=os.environ['H2_NODE']
        cmd=[node,str(W/'test/h2_direct_host_crud_ui.cjs'),sys.executable,str(self.f.config),str(self.f.root/'ui-wire.jsonl')]
        with (self.f.root/'ui.log').open('w') as out:
            rc=subprocess.run(cmd,env=self.f.env(),cwd=BASE/'cwd',stdout=out,stderr=subprocess.STDOUT,timeout=30).returncode
        self.f.remember()
        self.assertEqual(rc,0,(self.f.root/'ui.log').read_text())
        record(uiCommand=cmd,rc=rc,env=self.f.env(),cwd=str(BASE/'cwd'),wire=str(self.f.root/'ui-wire.jsonl'))


if __name__=='__main__':unittest.main(verbosity=2)
