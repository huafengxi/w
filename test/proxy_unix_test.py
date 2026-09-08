#!/usr/bin/env python3
"""Unit test for the w proxy `unix://` upstream support.

Usage:  python3 w/test/proxy_unix_test.py [--proxy PATH] [--keep]
Expect: RESULT: PASS (22/22 passed), exit code 0 (any FAIL -> exit code 1).

Imports `w/ext/proxy/proxy.py` directly and drives `forward()` against fake
upstreams — no web service, no routes rewrite, nothing production is touched
(sandbox files live in a fresh temp dir, TCP fake upstream on an ephemeral
127.0.0.1 port, all waits bounded).

Covers:
  1. _parse_rules: `unix:///abs/path` accepted; 6 malformed forms rejected
     (relative path, netloc filled, empty path x2, foreign scheme, http
     without netloc); `http://` parsing unchanged; `${RSH_FWD_DIR}` token
     expands to rshd's FWD_DIR (env override honoured) while the token in
     host position stays rejected

  2. plain GET over a unix upstream -> 200 + body passthrough + log label
     `unix:/path`
  3. unix upstream sees Host=localhost + X-Forwarded-For/Proto; POST body
     passthrough
  4. unreachable unix socket -> 502 whose text carries the `unix:` label, no
     exception escapes, PROXY FAIL log line carries the path
  5. SSE (text/event-stream) over unix -> streaming meta (no content_len) +
     all frames
  6. http:// TCP upstream unchanged (forward, log label `scheme://netloc`,
     Host=netloc, SSE)
  7. the shipped `routes.json` parses with the real proxy code and keeps the
     agreed shape (`/mac/` + `/nv2/` = unix socket under rshd's fwd dir,
     `/dev/` + `/nv1/` = http)
"""
import argparse
import importlib.util
import io
import json
import logging
import os
import shutil
import socketserver
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler

HERE = os.path.dirname(os.path.abspath(__file__))
PROXY_DEFAULT = os.path.join(os.path.dirname(HERE), "ext", "proxy",
                             "proxy.py")
BODY = b"plain-body-unix-test"
# routes.json 里走 rsh 反向转发的前缀 -> (socket 路径后缀, timeout, local_on)。
# 任务 4ob5de：/nv2/ 由 TCP 改 unix（dev->nv2:8080 被公司网络策略拦截），两条都用
# `${RSH_FWD_DIR}` token 写法（配置文件不再硬编码 dev 的绝对 home 路径）。
UNIX_ROUTES = {"/mac/": ("/run/rsh-fwd/mac.sock", 65.0, ["mac"]),
               "/nv2/": ("/run/rsh-fwd/nv2.sock", 10.0, ["nv2"])}
HTTP_ROUTES = {"/dev/": ["dev"], "/nv1/": ["nv1"]}
FWD_DIR_TOKEN = "${RSH_FWD_DIR}"

results = []          # (name, True/False)
skipped = []          # (name, reason)


def check(name, ok, detail=""):
    results.append((name, ok))
    print("%s: %s%s" % ("PASS" if ok else "FAIL", name,
                        ("  | " + str(detail)) if detail else ""))


def skip(name, reason):
    skipped.append((name, reason))
    print("SKIP: %s  | %s" % (name, reason))


def report():
    failed = [n for n, ok in results if not ok]
    total = len(results)
    extra = (", %d skipped" % len(skipped)) if skipped else ""
    print("\nRESULT: %s (%d/%d passed%s)"
          % ("PASS" if not failed else "FAIL", total - len(failed), total,
             extra))
    if failed:
        print("failed: %s" % ", ".join(failed))
    return 1 if failed else 0


class Upstream(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/echo"):
            body = json.dumps({"path": self.path,
                               "host": self.headers.get("Host"),
                               "xff": self.headers.get("X-Forwarded-For"),
                               "xfp": self.headers.get("X-Forwarded-Proto"),
                               "method": self.command}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/sse"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for i in range(3):
                self.wfile.write(b"data: frame-%d\n\n" % i)
                self.wfile.flush()
                time.sleep(0.2)
        elif self.path.startswith("/plain"):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(BODY)))
            self.end_headers()
            self.wfile.write(BODY)
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n)
        out = json.dumps({"method": "POST", "body": body.decode()}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


class ThreadingUnixServer(socketserver.ThreadingMixIn,
                          socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = False


class ThreadingTcpServer(socketserver.ThreadingMixIn,
                         socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True


def env_for(path, method="GET", body=b"", scheme="http"):
    return {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": "",
        "wsgi.url_scheme": scheme,
        "wsgi.input": io.BytesIO(body),
        "CONTENT_LENGTH": str(len(body)),
        "REMOTE_ADDR": "10.0.0.9",
        "HTTP_ACCEPT": "*/*",
    }


class LogCapture(logging.Handler):
    def __init__(self):
        logging.Handler.__init__(self)
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def parse_checks(proxy, base):
    """1. rule parsing (pure, no sockets involved)."""
    def rules_from(entries):
        return proxy._parse_rules({"routes": entries})

    ok_unix = rules_from([{"prefix": "/mac/", "strip_prefix": True,
                           "timeout": 65, "local_on": ["mac"],
                           "upstream": "unix://%s/up.sock" % base}])
    check("1a. unix:// upstream parses (absolute path)",
          len(ok_unix) == 1 and ok_unix[0]["upstream"].scheme == "unix"
          and ok_unix[0]["upstream"].path == "%s/up.sock" % base
          and ok_unix[0]["local_on"] == ["mac"]
          and ok_unix[0]["timeout"] == 65.0,
          str(ok_unix[0]["upstream"]) if ok_unix else "rejected")
    for bad, why in [("unix://relative/path.sock", "netloc must be empty"),
                     ("unix://localhost/abs/path.sock",
                      "netloc must be empty"),
                     ("unix://", "no path at all"),
                     ("unix:", "no path at all"),
                     ("ftp://host/x", "unsupported scheme"),
                     ("http://", "http without netloc")]:
        try:
            rules_from([{"prefix": "/x/", "upstream": bad}])
            check("1b. %r rejected (%s)" % (bad, why), False, "accepted!")
        except ValueError:
            check("1b. %r rejected (%s)" % (bad, why), True)
    ok_http = rules_from([{"prefix": "/dev/", "strip_prefix": True,
                           "timeout": 10, "local_on": ["dev"],
                           "upstream": "http://192.0.2.7:8080"}])
    check("1c. http:// upstream still parses identically",
          len(ok_http) == 1 and ok_http[0]["upstream"].netloc
          == "192.0.2.7:8080" and ok_http[0]["timeout"] == 10.0)


def fwd_dir_token_checks(proxy, base):
    """1d/1e. `${RSH_FWD_DIR}` token expansion (rshd FWD_DIR convention)."""
    saved = os.environ.get("RSH_FWD_DIR")
    os.environ["RSH_FWD_DIR"] = base
    try:
        r = proxy._parse_rules({"routes": [
            {"prefix": "/mac/",
             "upstream": "unix:///%s/mac.sock" % FWD_DIR_TOKEN}]})[0]
        check("1d. ${RSH_FWD_DIR} token expands to rshd's fwd dir",
              r["upstream"].scheme == "unix"
              and r["upstream"].path == os.path.join(base, "mac.sock"),
              r["upstream"].path)
        try:
            proxy._parse_rules({"routes": [
                {"prefix": "/x/",
                 "upstream": "unix://%s/x.sock" % FWD_DIR_TOKEN}]})
            check("1e. token in host position (unix://<token>/x) rejected",
                  False, "accepted!")
        except ValueError:
            check("1e. token in host position (unix://<token>/x) rejected",
                  True)
    finally:
        if saved is None:
            os.environ.pop("RSH_FWD_DIR", None)
        else:
            os.environ["RSH_FWD_DIR"] = saved


def shipped_routes_check(proxy):
    """7. the repo's routes.json still parses into the agreed shape."""
    name = ("7. shipped routes.json: /mac/ + /nv2/ = unix (rsh fwd dir), "
            "/dev/ + /nv1/ = http")
    path = getattr(proxy, "_routes_file", None)
    if not path or not os.path.exists(path):
        skip(name, "no routes.json at %s (proxy disabled there)" % path)
        return
    real = proxy.load_routes()
    by_prefix = dict((r["prefix"], r) for r in real)
    ok = set(by_prefix) == set(UNIX_ROUTES) | set(HTTP_ROUTES)
    for prefix, (suffix, timeout, local_on) in UNIX_ROUTES.items():
        r = by_prefix.get(prefix)
        ok = ok and bool(r) and r["upstream"].scheme == "unix" \
            and r["upstream"].path.endswith(suffix) \
            and r["timeout"] == timeout and r["local_on"] == local_on \
            and FWD_DIR_TOKEN not in r["upstream"].path
    for prefix, local_on in HTTP_ROUTES.items():
        r = by_prefix.get(prefix)
        ok = ok and bool(r) and r["upstream"].scheme == "http" \
            and r["upstream"].netloc and r["local_on"] == local_on
    check(name, ok,
          str([(r["prefix"], proxy.upstream_label(r["upstream"]))
               for r in real]))


def main():
    ap = argparse.ArgumentParser(
        description="w proxy unix:// upstream unit test")
    ap.add_argument("--proxy",
                    default=os.environ.get("W_PROXY_PY", PROXY_DEFAULT),
                    help="proxy.py under test (default: ../ext/proxy/"
                         "proxy.py next to this test; $W_PROXY_PY overrides)")
    ap.add_argument("--keep", action="store_true",
                    help="keep the sandbox temp dir for inspection")
    args = ap.parse_args()

    if not os.path.exists(args.proxy):
        print("FAIL: proxy.py not found at %s" % args.proxy)
        return 2

    spec = importlib.util.spec_from_file_location("proxy_under_test",
                                                 args.proxy)
    proxy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(proxy)

    base = tempfile.mkdtemp(prefix="proxy-unix-sb-")
    cap = LogCapture()
    logging.getLogger().addHandler(cap)
    logging.getLogger().setLevel(logging.INFO)

    parse_checks(proxy, base)
    fwd_dir_token_checks(proxy, base)

    upath = os.path.join(base, "up.sock")
    usrv = ThreadingUnixServer(upath, Upstream)
    threading.Thread(target=usrv.serve_forever, daemon=True).start()
    tsrv = ThreadingTcpServer(("127.0.0.1", 0), Upstream)
    tport = tsrv.server_address[1]
    threading.Thread(target=tsrv.serve_forever, daemon=True).start()
    time.sleep(0.3)

    def rules_from(entries):
        return proxy._parse_rules({"routes": entries})

    try:
        rule = rules_from([{"prefix": "/mac/", "strip_prefix": True,
                            "timeout": 10,
                            "upstream": "unix://%s" % upath}])[0]

        # ---------- 2. plain GET over unix
        cap.lines[:] = []
        meta, data = proxy.forward(env_for("/mac/plain"), "/mac/plain", rule)
        check("2. GET over unix upstream -> 200 + body passthrough",
              meta["http_status"].startswith("200") and data == BODY
              and meta["content_len"] == len(BODY),
              "%r %r" % (meta["http_status"], data[:40]))
        logline = [l for l in cap.lines if l.startswith("PROXY:")]
        check("2b. PROXY log line uses unix:/path label",
              bool(logline) and ("unix:%s" % upath) in logline[0],
              logline[0] if logline else "no PROXY line")

        # ---------- 3. headers on the unix path
        meta, data = proxy.forward(env_for("/mac/echo?a=1"), "/mac/echo",
                                   rule)
        got = json.loads(data.decode())
        check("3. unix upstream sees Host=localhost + X-Forwarded-*",
              got["host"] == "localhost" and got["xff"] == "10.0.0.9"
              and got["xfp"] == "http" and got["path"] == "/echo", str(got))
        meta, data = proxy.forward(env_for("/mac/echo", "POST", b"ping=1"),
                                  "/mac/echo", rule)
        check("3b. POST body passthrough over unix",
              json.loads(data.decode())["body"] == "ping=1")

        # ---------- 4. missing socket -> 502, no crash
        dead = rules_from([{"prefix": "/dead/", "timeout": 3,
                            "upstream": "unix://%s/nope.sock" % base}])[0]
        cap.lines[:] = []
        try:
            meta, data = proxy.forward(env_for("/dead/x"), "/dead/x", dead)
            crashed = False
        except Exception as e:            # must not happen
            crashed = True
            meta, data = {}, str(e)
        fail = [l for l in cap.lines if l.startswith("PROXY FAIL")]
        check("4. unreachable unix socket -> 502 (no exception escapes)",
              not crashed and meta.get("http_status") == "502 Bad Gateway"
              and data.startswith("502 Bad Gateway: upstream unix:"),
              "%r %r" % (meta.get("http_status"), data[:90]))
        check("4b. PROXY FAIL log line carries the unix path",
              bool(fail) and "/nope.sock" in fail[0],
              fail[0] if fail else "no FAIL line")

        # ---------- 5. SSE streaming over unix
        meta, gen = proxy.forward(env_for("/mac/sse"), "/mac/sse", rule)
        is_stream = "content_len" not in meta
        frames = b""
        t0 = time.time()
        for chunk in gen:                 # generator ends by itself (3 frames)
            frames += chunk
            if time.time() - t0 > 15:
                break
        check("5. SSE over unix -> streaming meta + all frames",
              is_stream and meta["type"] == "text/event-stream"
              and frames.count(b"data: frame-") == 3,
              "meta=%r frames=%d bytes" % (meta["type"], len(frames)))

        # ---------- 6. http:// TCP path unchanged
        trule = rules_from([{"prefix": "/t/", "strip_prefix": True,
                             "timeout": 10,
                             "upstream": "http://127.0.0.1:%d" % tport}])[0]
        cap.lines[:] = []
        meta, data = proxy.forward(env_for("/t/plain"), "/t/plain", trule)
        logline = [l for l in cap.lines if l.startswith("PROXY:")]
        check("6. http:// TCP upstream still forwards (200 + body)",
              meta["http_status"].startswith("200") and data == BODY)
        check("6b. http log label format unchanged (scheme://netloc)",
              bool(logline) and ("http://127.0.0.1:%d" % tport) in logline[0],
              logline[0] if logline else "no PROXY line")
        meta, data = proxy.forward(env_for("/t/echo"), "/t/echo", trule)
        check("6c. http upstream still sees Host=netloc",
              json.loads(data.decode())["host"] == "127.0.0.1:%d" % tport,
              str(json.loads(data.decode())["host"]))
        meta, gen = proxy.forward(env_for("/t/sse"), "/t/sse", trule)
        frames = b"".join(list(gen))
        check("6d. http SSE streaming unchanged",
              "content_len" not in meta
              and frames.count(b"data: frame-") == 3)

        # ---------- 7. shipped routes.json
        shipped_routes_check(proxy)
    finally:
        usrv.shutdown()
        usrv.server_close()
        tsrv.shutdown()
        tsrv.server_close()
        logging.getLogger().removeHandler(cap)
        if args.keep:
            print("sandbox kept at %s" % base)
        else:
            shutil.rmtree(base, ignore_errors=True)
    return report()


if __name__ == "__main__":
    sys.exit(main())
