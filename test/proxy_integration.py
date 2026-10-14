#!/usr/bin/env python3
"""Authenticated proxy integration, private upstream and configuration."""
import base64
import io
import json
import os
from pathlib import Path
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import sandbox
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.wsgi import make_wsgi_app, read_credential
from wsgi_basic_auth import BasicAuth
from ext.proxy import proxy

class Echo(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
        payload = json.dumps({'path': self.path, 'body': body.decode(),
                              'xff': self.headers.get('X-Forwarded-For')}).encode()
        self.send_response(201)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('X-Fixture', 'yes')
        self.end_headers()
        self.wfile.write(payload)
    def log_message(self, *args): pass

server = ThreadingHTTPServer(('127.0.0.1', 0), Echo)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
routes = Path(sandbox.WS, 'routes.json')
routes.write_text(json.dumps([{'prefix': '/fixture/', 'strip_prefix': True,
                              'upstream': 'http://127.0.0.1:%d' % server.server_port}]))
proxy._routes_file = str(routes)
credential = Path(sandbox.WS, 'auth')
credential.write_text('fixture:password')
os.environ['W_AUTH_FILE'] = str(credential)
os.environ['WSGI_AUTH_CREDENTIALS'] = read_credential()
app = BasicAuth(make_wsgi_app([proxy.proxy_handler]))

def request(auth):
    env = {'REQUEST_METHOD': 'POST', 'PATH_INFO': '/fixture/echo',
           'QUERY_STRING': 'x=1', 'CONTENT_LENGTH': '7',
           'CONTENT_TYPE': 'text/plain', 'wsgi.input': io.BytesIO(b'payload'),
           'wsgi.url_scheme': 'http', 'REMOTE_ADDR': '192.0.2.1',
           'SERVER_NAME': 'fixture', 'SERVER_PORT': '80', 'wsgi.errors': sys.stderr,
           'SERVER_PROTOCOL': 'HTTP/1.1'}
    if auth: env['HTTP_AUTHORIZATION'] = 'Basic ' + base64.b64encode(auth.encode()).decode()
    result = []
    body = b''.join(app(env, lambda s, h, *a: result.extend([s, h])))
    return result, body

try:
    result, body = request(None)
    assert result[0].startswith('401'), result
    result, body = request('fixture:wrong')
    assert result[0].startswith('401'), result
    result, body = request('fixture:password')
    assert result[0].startswith('201'), result
    assert ('X-Fixture', 'yes') in result[1], result
    doc = json.loads(body)
    assert doc == {'path': '/echo?x=1', 'body': 'payload', 'xff': '192.0.2.1'}, doc
    credential.write_text('')
    try:
        read_credential()
        raise AssertionError('empty credentials accepted')
    except ValueError: pass
    print('ALL PASS: BasicAuth, strip prefix, method/body/headers, invalid credentials')
finally:
    server.shutdown()
    server.server_close()
