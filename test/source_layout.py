"""Source-boundary regressions, independent of deployed state and model access."""
import ast
from pathlib import Path
import unittest

SESSIOND = Path(__file__).resolve().parents[1] / 'ext/sessiond'


class SourceLayout(unittest.TestCase):
    def test_one_explicit_layout_without_legacy_modules(self):
        for name in ('sessions.py', 'registry.py', 'lifecycle.py', 'client.py',
                     'agent_client.py', 'bridge.py', 'bootstrap.py', 'pi-extension.ts'):
            self.assertTrue((SESSIOND / name).is_file(), name)
        for name in ('proc.py', 'ordinary_host.py', 'ordinary_registry.py', 'probe.ts'):
            self.assertFalse((SESSIOND / name).exists(), name)

    def test_client_does_not_inherit_or_spawn_pipe_host(self):
        tree = ast.parse((SESSIOND / 'client.py').read_text())
        client = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Client')
        self.assertEqual(client.bases, [])
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                self.assertNotIn(node.attr, ('Popen', 'PIPE', '_ORIGINAL_SUPERVISOR'))

    def test_only_lifecycle_launches_p_not_pi(self):
        for file in SESSIOND.rglob('*.py'):
            tree = ast.parse(file.read_text())
            spawns = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                      and isinstance(n.func, ast.Attribute) and n.func.attr == 'Popen']
            if file.name == 'lifecycle.py':
                self.assertEqual(len(spawns), 1)
                self.assertIn('self.host_entry', ast.unparse(spawns[0].args[0]))
            else:
                self.assertEqual(spawns, [], str(file))

    def test_production_never_replaces_imported_factories(self):
        for file in SESSIOND.rglob('*.py'):
            tree = ast.parse(file.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotEqual(node.func.id, 'setattr', str(file))
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or '']
                    for name in names:
                        self.assertFalse(name.startswith(('unittest.mock', 'h2_direct_host')), (file, name))

    def test_session_files_do_not_scan_or_signal_processes(self):
        tree = ast.parse((SESSIOND / 'sessions.py').read_text())
        forbidden = {'kill', 'killpg', 'Popen', 'host_map', 'find_hosts', 'kill_hosts'}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, 'id', None)
                self.assertNotIn(name, forbidden)
        self.assertNotIn('/proc/', (SESSIOND / 'sessions.py').read_text())


if __name__ == '__main__':
    unittest.main()
