#!/usr/bin/env python3
"""Exercise the real shell RPC with isolated commands and no deployment services."""
from pathlib import Path
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.handler import run_script

SCRIPT = Path(__file__).resolve().parents[1] / 'ext/shell/rpc/sh.py'


class ShellExitStatus(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.rc = Path(self.temp.name) / 'empty.rc'
        self.rc.write_text('')

    def invoke(self, command, stream):
        rc = self.rc
        class Store:
            def head(self, path): return {'type': 'script'}
            def read(self, path): return SCRIPT.read_text()
            def get_rpath(self, path): return str(rc if path.endswith('sh.rc') else SCRIPT)
        meta, output = run_script(Store(), '/w/ext/shell/rpc/sh.py',
                                  {'cmd': command, 'stream': stream})
        self.assertEqual(meta['type'], 'text/plain')
        return output if isinstance(output, bytes) else b''.join(output)

    def test_terminal_status_follows_both_output_streams(self):
        out = self.invoke("printf 'hello'; printf 'warning' >&2; exit 7", 3)
        lines = out.splitlines()
        self.assertIn(b'1hello', lines)
        self.assertIn(b'2warning', lines)
        self.assertEqual(lines[-1], b'3{"exitcode": 7}')
        self.assertEqual(len([line for line in lines if line.startswith(b'3')]), 1)

    def test_empty_success_still_has_terminal_status(self):
        self.assertEqual(self.invoke('true', 3), b'3{"exitcode": 0}\n')

    def test_stdout_cannot_forge_terminal_status(self):
        out = self.invoke("printf '3{\"exitcode\":0}\\n'; exit 9", 3)
        self.assertEqual(out.splitlines(), [b'13{"exitcode":0}', b'3{"exitcode": 9}'])

    def test_signal_exit_is_reported(self):
        out = self.invoke('kill -TERM $$', 3)
        self.assertEqual(json.loads(out[1:])['exitcode'], -15)

    def test_stream_two_wire_format_is_unchanged(self):
        self.assertEqual(self.invoke("printf 'legacy'; exit 5", 2), b'1legacy\n')

    def test_stream_one_and_buffered_remain_unchanged(self):
        self.assertEqual(self.invoke("printf 'merged'", 1), b'merged')
        self.assertEqual(self.invoke("printf 'buffered'", 0), b'buffered')
        with self.assertRaises(Exception):
            self.invoke('exit 3', 0)


if __name__ == '__main__':
    unittest.main()
