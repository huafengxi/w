#!/usr/bin/env python3
# 测试用临时上游（0831-0937-sh7l）：echo 请求方法/路径/头/体为 JSON；
# /custom 路径返回 201 + 自定义头，用于验证上游响应透传。
# 用法: python3 proxy_upstream.py [port]（缺省 18099）
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class H(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def _handle(self):
        length = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(length) if length else b''
        if self.path.split('?')[0].rstrip('/').endswith('/custom'):
            payload = b'{"custom": true}'
            self.send_response(201)
            self.send_header('Content-Type', 'application/json')
            self.send_header('X-Upstream-Test', 'proxy-ok')
        else:
            echo = {
                'method': self.command,
                'path': self.path,
                'headers': {k.lower(): v for k, v in self.headers.items()},
                'body': body.decode('utf-8', 'replace'),
            }
            payload = json.dumps(echo, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = _handle

    def log_message(self, *a):
        pass


if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18099
    ThreadingHTTPServer(('127.0.0.1', port), H).serve_forever()
