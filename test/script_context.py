#!/usr/bin/env python3
"""Script source identity survives virtual mounts and nested execution."""
from pathlib import Path
import sys
import tempfile
import traceback
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.handler import run_script
from stores.dir_store import DirStore
from stores.store import RootStore


class ScriptContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='w-script-context-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.store = RootStore([('/virtual', DirStore(str(self.root)))])

    def script(self, name, body):
        path = self.root / name
        path.write_text(body)
        return path

    def test_mounted_script_reads_sibling_without_cwd_or_injected_imports(self):
        (self.root / 'sibling.txt').write_text('relocated content')
        self.script('entry.py', 'from pathlib import Path\n'
            'def interp(store, **kw):\n'
            ' return Path(__file__).with_name("sibling.txt").read_text()\n')
        meta, body = run_script(self.store, '/virtual/entry.py', {'__file__': '/spoofed'})
        self.assertEqual(body, 'relocated content')
        self.assertEqual(meta['type'], 'text/plain')

    def test_nested_scripts_have_independent_source_identity(self):
        inner = self.script('inner.py', 'def interp(store, **kw): return __file__\n')
        outer = self.script('outer.py', 'def interp(store, **kw):\n'
            ' return __file__, run_script(store, "/virtual/inner.py", {})[1]\n')
        self.assertEqual(run_script(self.store, '/virtual/outer.py', {}),
                         (str(outer), str(inner)))

    def test_traceback_names_physical_script(self):
        script = self.script('failure.py', 'def interp(store, **kw): raise ValueError("fixture")\n')
        try:
            run_script(self.store, '/virtual/failure.py', {})
        except ValueError as error:
            frames = traceback.extract_tb(error.__traceback__)
        self.assertEqual(frames[-1].filename, str(script))

    def test_virtual_store_does_not_claim_a_local_source_file(self):
        class VirtualStore:
            def head(self, path): return {'type': 'script'}
            def read(self, path):
                return 'def interp(store, **kw): return str("__file__" in globals())'
        self.assertEqual(run_script(VirtualStore(), '/remote/script.py', {})[1], 'False')


if __name__ == '__main__':
    unittest.main()
