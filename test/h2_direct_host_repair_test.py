#!/usr/bin/env python3
"""B1/B2 real filesystem countercontrols via original CLI/API and OS webs.

Uses only this invocation's new CRUD fixture roots and exact identity cleanup.
No mock mkdir/registration/host status; retained injections are moved back by
this harness, never by the product. P state loss is not safe-to-restart proof.
"""
import json
import unittest
import h2_direct_host_crud_test as T
from h2_direct_host_crud_test import Client, Fixture, H, record


class Repairs(unittest.TestCase):
    def setUp(self):
        self.f = Fixture(self.id().split('.')[-1])

    def tearDown(self):
        self.f.cleanup()

    create, attach, delete = T.CRUD.create, T.CRUD.attach, T.CRUD.delete

    def test_B1_real_late_create_failure_and_complete_creation(self):
        # A harmless file in THIS fixture makes real parents=True mkdir fail.
        agents = self.f.ws / 'agents'
        agents.mkdir()
        blocker = agents / 'bot'
        blocker.write_text('own regular-file mkdir blocker\n')
        c1, c2 = Client(self.f), Client(self.f)
        cli = c1.call(special='cli', argv=['new', '--profile', 'good'])
        declarations = list((self.f.ws / 'run/sessiond').glob('*/*.agent'))
        self.assertEqual(len(declarations), 1)
        agent = declarations[0]
        name = agent.stem
        site = '/run/sessiond/' + name + '/' + agent.name
        original = agent.read_bytes()
        doc = json.loads(original)
        row = next(x for x in c2.request(op='list_sessions')['body']['sessions'] if x['name'] == name)
        attached = c2.request(op='attach', session=site)['body']
        self.f.remember()  # baseline failure may actually have started host/pi
        currents = list((self.f.ws/'run/sessiond').glob('*/host.json'))
        audit = [c.request(special='spawn_audit') for c in (c1, c2)]
        record(B1=dict(cli=cli, declaration=doc, row=row, attach=attached, spawnAttempts=audit,
                       currents=[json.loads(p.read_text()) for p in currents],
                       inboxExists=(agents/'bot'/name/'inbox').is_dir()))
        # Evaluate all product results after collecting baseline startup evidence.
        self.assertFalse(cli['ok'], cli)
        self.assertIn('NotADirectoryError', cli['error'])
        self.assertEqual(row['state'], 'unknown')
        self.assertFalse(attached['ok'])
        self.assertEqual(currents, [], 'partial create must not launch host/pi')
        self.assertEqual(audit, [[], []], 'no subprocess launch attempted in either web')
        self.assertEqual(blocker.read_text(), 'own regular-file mkdir blocker\n')
        self.assertFalse((agents/'bot'/name/'inbox').exists())
        # Explicit harness removal of the injection (move, not auto recovery).
        blocker.rename(self.f.root/'parked-blocker')
        normal = c1.request(special='cli', argv=['new', '--profile', 'good'])
        self.assertEqual(normal['rc'], 0, normal)
        rows = c2.request(op='list_sessions')['body']['sessions']
        self.assertEqual(next(x for x in rows if x['name'] == name)['state'], 'unknown')
        row2 = next(x for x in rows if x['name'] != name)
        self.assertEqual(row2['state'], 'dormant')
        valid = json.loads((agent.parent.parent/row2['name']/(row2['name']+'.agent')).read_text())
        full = dict(name=row2['name'], agent_site=row2['agent'], registration=valid['h2Registration'])
        self.assertTrue((agents/'bot'/row2['name']/'inbox').is_dir())
        a = self.attach(c2, full)
        self.assertTrue(self.delete(c2, full, a['gen'])['ok'])
        self.assertEqual(agent.read_bytes(), original)

    def test_B1_cached_unstarted_supervisor_checks_completion(self):
        c1, c2 = Client(self.f), Client(self.f)
        doc = self.create(c1)
        cached = c1.request(special='cache_unstarted', session=doc['agent_site'])
        self.assertIsNone(cached['gen'])
        self.assertEqual(cached['handles'], [])
        inbox = self.f.ws/'agents/bot'/doc['name']/'inbox'
        parked = self.f.root/'parked-inbox'
        inbox.rename(parked)
        try:
            row = c2.request(op='list_sessions')['body']['sessions'][0]
            result = c1.request(special='cached', session=doc['agent_site'])
            changed = c1.request(special='cached_change', session=doc['agent_site'], operation='reload', expected_instance='old')
            attached = c2.request(op='attach', session=doc['agent_site'])['body']
            self.f.remember()
            currents = list((self.f.ws/'run/sessiond').glob('*/host.json'))
            audit = [c.request(special='spawn_audit') for c in (c1, c2)]
            record(B1cached=dict(row=row, cached=result, change=changed, attach=attached, spawnAttempts=audit,
                                 currents=[json.loads(p.read_text()) for p in currents]))
            self.assertEqual(row['state'], 'unknown')
            self.assertFalse(result['sent'])
            self.assertEqual(result['status']['state'], 'idle')  # no instance exists
            self.assertFalse(changed['ok'])
            self.assertFalse(attached['ok'])
            self.assertEqual(currents, [])
            self.assertEqual(audit, [[], []])
            self.assertFalse(inbox.exists(), 'no automatic inbox repair')
        finally:
            parked.rename(inbox)
        a = self.attach(c2, doc)
        self.assertTrue(self.delete(c2, doc, a['gen'])['ok'])

    def test_B2_missing_runtime_with_live_host_and_never_started(self):
        c1, c2 = Client(self.f), Client(self.f)
        doc = self.create(c1)
        row = c2.request(op='list_sessions')['body']['sessions'][0]
        self.assertEqual(row['state'], 'dormant')
        self.assertFalse(self.f.current(doc['name']).exists())
        a = self.attach(c1, doc)
        current = self.f.current(doc['name'])
        d = json.loads(current.read_text())
        history = self.f.ws/'run/sessiond'/doc['name']/(doc['name']+'.jsonl')
        self.assertTrue(history.exists())
        before = history.read_bytes()
        parked = self.f.root/'parked-host.json'
        current.rename(parked)
        try:
            row = c2.request(op='list_sessions')['body']['sessions'][0]
            now = H.identity(d['host']['pid'])
            host_alive = H.same(now, d['host']) and H.live(now)
            denied = self.delete(c2, doc, 'unstarted')
            record(B2=dict(row=row, identity=H.Lifecycle.process_key(d['host']), host=d['host'], hostActuallyLive=host_alive,
                           history=str(history), historyBytes=len(before), deleteUnstarted=denied,
                           injection='moved only own P state file; declaration/history/socket retained; restored in finally'))
            self.assertTrue(host_alive)
            self.assertEqual(row['state'], 'unknown')
            self.assertFalse(denied['ok'])
            self.assertEqual(history.read_bytes(), before)
        finally:
            self.assertFalse(current.exists(), 'no automatic P state repair')
            parked.rename(current)
        self.assertTrue(self.delete(c1, doc, a['gen'])['ok'])
        # Historical JSONL alone is also NOT sufficient never-started evidence.
        old = self.create(c1, 'hist')
        h = self.f.ws/'run/sessiond/hist/hist.jsonl'
        h.write_text('{"type":"session","id":"existing-history"}\n')
        row = c2.request(op='list_sessions')['body']['sessions'][0]
        self.assertEqual(row['state'], 'unknown')
        self.assertFalse(self.delete(c2, old, 'unstarted')['ok'])
        self.assertTrue(h.exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
