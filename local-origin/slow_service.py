"""Slow service. Listens on :9000.

GET /slow[?delay=N]   -> sleeps N seconds (default 240), then returns 200.

Intentionally sends NOTHING to the client during the sleep, so the response
is completely silent until the very end. That's what exercises the proxy
read timeout: with the default 100s, the Cloudflare edge would 524 before
the origin responds.
"""
import json
import socketserver
import time
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

DEFAULT_DELAY = 240


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        q = parse_qs(urlparse(self.path).query)
        try:
            delay = int(q.get("delay", [DEFAULT_DELAY])[0])
        except ValueError:
            delay = DEFAULT_DELAY

        start = time.time()
        print(f"[slow] {self.path}  sleeping {delay}s", flush=True)
        time.sleep(delay)
        elapsed = time.time() - start

        body = json.dumps({
            "service": "slow-service",
            "requested_delay_s": delay,
            "actual_elapsed_s": round(elapsed, 2),
            "msg": f"alive after {round(elapsed)} seconds of silence",
        }).encode()

        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.send_header("x-served-by", "slow-service@ssh.demoflair.com")
        self.end_headers()
        self.wfile.write(body)
        print(f"[slow] returned after {round(elapsed,2)}s", flush=True)

    def log_message(self, *a, **kw):
        pass


class TS(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    print("[slow] listening on :9000", flush=True)
    TS(("0.0.0.0", 9000), H).serve_forever()
