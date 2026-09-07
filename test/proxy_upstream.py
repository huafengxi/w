#!/usr/bin/env python3
# 测试用临时上游（0831-0937-sh7l）：echo 请求方法/路径/头/体为 JSON；
# /custom 路径返回 201 + 自定义头，用于验证上游响应透传。
# 用法: python3 proxy_upstream.py [port|/abs/unix.sock]（缺省 18099；
#       以 / 开头的参数 = 监听 unix socket，任务 lzful1）
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
    arg = sys.argv[1] if len(sys.argv) > 1 else '18099'
    if arg.startswith('/'):
        # unix socket 上游（任务 lzful1）：proxy 的 unix:// upstream 回归用
        import os
        import socketserver

        class ThreadingUnixHTTPServer(socketserver.ThreadingMixIn,
                                      socketserver.UnixStreamServer):
            daemon_threads = True

        try:
            os.unlink(arg)
        except FileNotFoundError:
            pass
        ThreadingUnixHTTPServer(arg, H).serve_forever()
    else:
        ThreadingHTTPServer(('127.0.0.1', int(arg)), H).serve_forever()
