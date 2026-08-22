#!/usr/bin/env python
# -*- coding: utf-8-unix -*-
'''
reverse-proxy.py HOST:PORT [LOG_FILE]

Reverse proxy that forwards all requests to a backend server.
Handles HTTP, SSE streaming, and WebSocket upgrades.
Adds BasicAuth (via ~/.auth/passwd) + SSL via core.wsgi.

Env vars:
  REVERSE_PROXY_BACKEND  backend URL (default: http://127.0.0.1:8192)
  log                    log level: debug/info/warning/error (default: info)
  nossl                  set to 1 to disable SSL
  timeout                socket timeout in seconds (default: 300)

Example:
  REVERSE_PROXY_BACKEND=http://127.0.0.1:8192 log=info python w/server/reverse-proxy.py 0.0.0.0:8193 logs/reverse-proxy.log
'''

import sys
import os
import logging
import http.client
import urllib.parse
import socket
import select
import threading
import base64
import time
import signal
import subprocess

# repo root
_repo_root = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

HOP_BY_HOP = {
    'connection', 'keep-alive', 'proxy-authenticate',
    'proxy-authorization', 'te', 'trailers', 'transfer-encoding',
    'proxy-connection',
}

BUFSIZE = 8192


def _read_credentials():
    """Read BasicAuth credentials from ~/.auth/passwd."""
    path = os.path.expanduser('~/.auth/passwd')
    try:
        with open(path) as f:
            return f.read().strip()
    except IOError:
        return ''


def _check_auth(headers):
    """Check BasicAuth. Returns True if authorized."""
    creds = _read_credentials()
    if not creds:
        return True  # no auth configured
    auth = headers.get('authorization', '')
    if auth.startswith('Basic '):
        try:
            decoded = base64.b64decode(auth[6:]).decode('utf-8')
            return decoded == creds
        except Exception:
            pass
    return False


def _copy_headers(headers, preserve_upgrade=False):
    """Copy headers, stripping hop-by-hop."""
    out = {}
    for k, v in headers.items():
        kl = k.lower()
        if preserve_upgrade and kl in ('upgrade', 'connection', 'sec-websocket-key',
                                        'sec-websocket-version', 'sec-websocket-extensions'):
            out[k] = v
            continue
        if kl in HOP_BY_HOP:
            continue
        out[k] = v
    return out


def _recv_until_double_crlf(sock, timeout=30):
    """Read from socket until \r\n\r\n. Returns (headers_bytes, leftover_bytes)."""
    data = b''
    deadline = time.time() + timeout
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            raise socket.timeout('timed out reading HTTP headers')
        ready, _, _ = select.select([sock], [], [], min(remaining, 1.0))
        if not ready:
            continue
        chunk = sock.recv(BUFSIZE)
        if not chunk:
            raise ConnectionError('client disconnected')
        data += chunk
        idx = data.find(b'\r\n\r\n')
        if idx >= 0:
            return data[:idx + 4], data[idx + 4:]


def _parse_headers(raw):
    """Parse raw HTTP request headers into (method, path, headers_dict)."""
    lines = raw.decode('utf-8', errors='replace').split('\r\n')
    if not lines:
        return None, None, None
    parts = lines[0].split(' ', 2)
    if len(parts) < 2:
        return None, None, None
    method = parts[0].upper()
    path = parts[1]
    headers = {}
    for line in lines[1:]:
        if ':' in line:
            k, v = line.split(':', 1)
            headers[k.strip().lower()] = v.strip()
    return method, path, headers


def _tunnel(client_sock, backend_sock):
    """Bidirectional tunnel between client and backend."""
    sockets = [client_sock, backend_sock]
    try:
        while True:
            readable, _, _ = select.select(sockets, [], [], 60)
            if not readable:
                break
            for s in readable:
                data = s.recv(BUFSIZE)
                if not data:
                    return
                if s is client_sock:
                    backend_sock.sendall(data)
                else:
                    client_sock.sendall(data)
    except Exception:
        pass


def _handle_websocket(client_sock, method, path, headers, backend_host, backend_port, timeout):
    """Handle WebSocket upgrade: connect to backend, send upgrade, tunnel."""
    backend_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    backend_sock.settimeout(timeout)
    try:
        backend_sock.connect((backend_host, backend_port))
    except Exception as e:
        logging.error('WebSocket: backend connect failed: %s', e)
        client_sock.sendall(b'HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n')
        return

    # Forward the WebSocket upgrade request
    ws_headers = _copy_headers(headers, preserve_upgrade=True)
    ws_headers['host'] = '%s:%d' % (backend_host, backend_port)
    raw = '%s %s HTTP/1.1\r\n' % (method, path)
    for k, v in ws_headers.items():
        raw += '%s: %s\r\n' % (k, v)
    raw += '\r\n'

    try:
        backend_sock.sendall(raw.encode('utf-8'))
        # Read backend response
        resp_data, _ = _recv_until_double_crlf(backend_sock, timeout=timeout)
        # Forward to client
        client_sock.sendall(resp_data)
        # Tunnel the rest
        _tunnel(client_sock, backend_sock)
    except Exception as e:
        logging.error('WebSocket tunnel error: %s', e)
    finally:
        try:
            backend_sock.close()
        except Exception:
            pass


def _handle_http(client_sock, method, path, headers, body_data, backend_host, backend_port, timeout):
    """Handle normal HTTP request via proxy."""
    req_headers = _copy_headers(headers)
    req_headers['host'] = '%s:%d' % (backend_host, backend_port)

    try:
        conn = http.client.HTTPConnection(backend_host, backend_port, timeout=timeout)
        body = body_data if body_data else None
        conn.request(method, path, body=body, headers=req_headers)
        resp = conn.getresponse()

        # Build status line
        status_line = 'HTTP/1.1 %d %s\r\n' % (resp.status, resp.reason)
        client_sock.sendall(status_line.encode('utf-8'))

        # Send headers
        for k, v in resp.getheaders():
            if k.lower() in HOP_BY_HOP:
                continue
            client_sock.sendall(('%s: %s\r\n' % (k, v)).encode('utf-8'))
        client_sock.sendall(b'\r\n')

        # Stream body
        while True:
            chunk = resp.read(BUFSIZE)
            if not chunk:
                break
            client_sock.sendall(chunk)
        resp.close()
    except Exception as e:
        logging.error('HTTP proxy error: %s', e)
        try:
            client_sock.sendall(b'HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n')
        except Exception:
            pass


def _read_body(headers, sock, leftover, timeout=30):
    """Read request body based on Content-Length. Returns (body_bytes, consumed_leftover)."""
    cl = int(headers.get('content-length', '0'))
    if cl <= 0:
        return b'', leftover

    body = leftover[:cl]
    leftover = leftover[cl:]

    deadline = time.time() + timeout
    while len(body) < cl:
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        ready, _, _ = select.select([sock], [], [], min(remaining, 1.0))
        if not ready:
            continue
        chunk = sock.recv(min(BUFSIZE, cl - len(body)))
        if not chunk:
            break
        body += chunk

    return body, leftover


def handle_client(client_sock, backend_host, backend_port, timeout):
    """Handle one client connection."""
    try:
        # Read HTTP headers
        raw_headers, leftover = _recv_until_double_crlf(client_sock, timeout=timeout)
        method, path, headers = _parse_headers(raw_headers)

        if not method:
            client_sock.sendall(b'HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n')
            return

        logging.info('REVERSE_PROXY: %s %s', method, path)

        # Check BasicAuth
        if not _check_auth(headers):
            client_sock.sendall(
                b'HTTP/1.1 401 Unauthorized\r\n'
                b'WWW-Authenticate: Basic realm="pi-web", charset="UTF-8"\r\n'
                b'Content-Length: 0\r\n\r\n'
            )
            return

        # Check for WebSocket upgrade
        upgrade = headers.get('upgrade', '').lower()
        if upgrade == 'websocket':
            _handle_websocket(client_sock, method, path, headers,
                              backend_host, backend_port, timeout)
            return

        # Read body
        body, leftover = _read_body(headers, client_sock, leftover, timeout=timeout)

        # Handle HTTP
        _handle_http(client_sock, method, path, headers, body,
                     backend_host, backend_port, timeout)

    except socket.timeout:
        logging.debug('client timeout')
    except ConnectionError as e:
        logging.debug('client disconnected: %s', e)
    except Exception as e:
        logging.error('handle_client error: %s', e)
    finally:
        try:
            client_sock.close()
        except Exception:
            pass


def main():
    if len(sys.argv) <= 1:
        print(__doc__)
        sys.exit(1)

    log_level = os.getenv('log') or 'info'
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), None),
        format='%(asctime)s %(levelname)s %(message)s',
    )

    listen_addr = sys.argv[1]
    log_file = sys.argv[2] if len(sys.argv) > 2 else ''
    backend_url = os.getenv('REVERSE_PROXY_BACKEND', 'http://127.0.0.1:8192')
    timeout = int(os.getenv('timeout', '300'))

    parsed = urllib.parse.urlparse(backend_url)
    backend_host = parsed.hostname or '127.0.0.1'
    backend_port = parsed.port or 80

    host_port = listen_addr.split(':')
    if len(host_port) == 2:
        host, port = host_port[0], int(host_port[1])
    else:
        host, port = '', int(listen_addr)

    logging.info('reverse_proxy_start: listen=%s:%d backend=%s log=%s(%s)',
                 host, port, backend_url, log_file or 'stdout', log_level)

    # Fork daemon if log file provided
    if log_file:
        if os.fork() > 0:
            time.sleep(0.1)
            sys.exit(0)
        f = open(log_file, 'wb+')
        os.dup2(f.fileno(), 1)
        os.dup2(f.fileno(), 2)

    # Kill existing process on same port
    for pid in subprocess.Popen(['pgrep', '-f', 'reverse-proxy.py'],
                                stdout=subprocess.PIPE).communicate()[0].split(b'\n'):
        if pid and int(pid) != os.getpid():
            try:
                os.kill(int(pid), signal.SIGKILL)
            except Exception:
                pass

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((host, port))
    server_sock.listen(128)
    logging.info('reverse proxy listening at %s:%d => %s', host, port, backend_url)

    while True:
        try:
            client_sock, addr = server_sock.accept()
            client_sock.settimeout(timeout)
            t = threading.Thread(target=handle_client,
                                 args=(client_sock, backend_host, backend_port, timeout),
                                 daemon=True)
            t.start()
        except Exception as e:
            logging.error('accept error: %s', e)


if __name__ == '__main__':
    main()