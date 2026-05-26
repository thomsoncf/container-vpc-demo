"""
Demo container app.

Two upstream paths:

  /slow*    -> https://slow.demoflair.com$PATH  (direct HTTPS out of the
               container, hits the Cloudflare edge proxy for demoflair.com.
               This is what exercises the proxy_read_timeout cache rule.)

  anything  -> http://acme-products.internal$PATH  (a virtual hostname that
               is intercepted by the Worker's outboundByHost handler and
               routed through the Workers VPC binding -> tunnel -> origin)
"""

import json
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PRIVATE_API = "http://acme-products.internal"
SLOW_SERVICE = "https://slow.demoflair.com"
SLOW_VPC = "http://slow-vpc.internal"

# Generous timeout so we don't kill the request before the upstream responds.
UPSTREAM_TIMEOUT_S = 600


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # /vpc-slow*  -> exercise VPC binding chain (no edge proxy)
        # /slow*      -> exercise direct HTTPS through the edge proxy
        # else        -> existing products-api demo through VPC binding
        if self.path.startswith("/vpc-slow"):
            # Strip the /vpc-slow prefix and forward to /slow on the upstream
            rest = self.path[len("/vpc-slow"):] or "/slow"
            upstream_url = f"{SLOW_VPC}{rest}"
            route = "vpc-binding-to-slow-service-via-tunnel"
        elif self.path.startswith("/slow"):
            upstream_url = f"{SLOW_SERVICE}{self.path}"
            route = "direct-https-via-edge-proxy"
        else:
            upstream_url = f"{PRIVATE_API}{self.path}"
            route = "vpc-binding-via-tunnel"

        start = time.time()
        try:
            with urllib.request.urlopen(upstream_url, timeout=UPSTREAM_TIMEOUT_S) as resp:
                body = resp.read()
                status = resp.status
                hdrs = dict(resp.headers.items())
        except urllib.error.HTTPError as e:
            body = e.read()
            status = e.code
            hdrs = dict(e.headers.items()) if e.headers else {}
        except Exception as e:
            elapsed = time.time() - start
            err = {
                "error": str(e),
                "error_type": type(e).__name__,
                "upstream_url": upstream_url,
                "route": route,
                "elapsed_s_before_error": round(elapsed, 2),
            }
            out = json.dumps(err, indent=2).encode()
            self.send_response(502)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
            return

        elapsed = time.time() - start
        wrapped = {
            "container_says": "i made an HTTP call and got a response",
            "route": route,
            "upstream_url": upstream_url,
            "upstream_status": status,
            "upstream_x_served_by": hdrs.get("x-served-by"),
            "container_elapsed_s": round(elapsed, 2),
            "upstream_body": _try_json(body),
        }
        out = json.dumps(wrapped, indent=2).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, fmt, *args):
        print(f"[container] {fmt % args}", flush=True)


def _try_json(b: bytes):
    try:
        return json.loads(b)
    except Exception:
        return b.decode(errors="replace")


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("0.0.0.0", 8080), Handler)
    print(f"[container] listening on :8080", flush=True)
    print(f"[container]   /slow*  -> {SLOW_SERVICE}", flush=True)
    print(f"[container]   else    -> {PRIVATE_API}", flush=True)
    srv.serve_forever()
