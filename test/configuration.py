#!/usr/bin/env python3
"""Configuration failures are explicit and transport is caller-controlled."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import sandbox
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import config, wsgi
from ext.proxy import proxy

class ConfigurationTests(unittest.TestCase):
    def test_missing_workspace(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, 'W_WORKSPACE'):
                config.workspace()
    def test_relative_path(self):
        with patch.dict(os.environ, {'W_FSTAB': 'relative'}):
            with self.assertRaises(ValueError): config.path('W_FSTAB', True)
    def test_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError): wsgi.read_credential()
        p = Path(sandbox.WS, 'credentials')
        p.write_text('user:password')
        with patch.dict(os.environ, {'W_AUTH_FILE': str(p)}):
            self.assertEqual(wsgi.read_credential(), 'user:password')
    def test_routes(self):
        with patch.object(proxy, '_routes_file', None):
            self.assertEqual(proxy.load_routes(), [])
        with patch.object(proxy, '_routes_file', str(Path(sandbox.WS, 'missing'))):
            with self.assertRaises(FileNotFoundError): proxy.load_routes()
    def test_token_requires_directory(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                proxy._parse_rules([{'prefix': '/x/', 'upstream': 'unix:///${RSH_FWD_DIR}/x.sock'}])
    def test_transport(self):
        import wsgiserver
        cert, key = Path(sandbox.WS, 'cert'), Path(sandbox.WS, 'key')
        cert.write_text('fixture'); key.write_text('fixture')
        with patch.object(wsgi, 'patch_wsgiserver_nonascii_uri'), patch.object(wsgiserver, 'WSGIServer') as server:
            with patch.dict(os.environ, {}, clear=True):
                wsgi.run_use_wsgiserver(None, '127.0.0.1', 0, False)
                self.assertIsNone(server.call_args.kwargs['certfile'])
            with patch.dict(os.environ, {'W_TLS_CERT': str(cert), 'W_TLS_KEY': str(key)}, clear=True):
                wsgi.run_use_wsgiserver(None, '127.0.0.1', 0, False)
                self.assertEqual(server.call_args.kwargs['certfile'], str(cert))
            with patch.dict(os.environ, {'W_TLS_CERT': str(cert)}, clear=True):
                with self.assertRaises(ValueError): wsgi.run_use_wsgiserver(None, '', 0, False)

if __name__ == '__main__': unittest.main()
