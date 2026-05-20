"""
Demo container app.

For each inbound HTTP request on :8080, fetches the same path from the
private API at http://acme-products.internal/  -- a hostname that doesn't
exist on the public internet. It only resolves because the Worker's
outboundByHost handler intercepts the request and forwards it through
the Workers VPC binding into the cloudflared tunnel.
"""

import json
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PRIVATE_API = "http://acme-products.internal"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        upstream_url = f"{PRIVATE_API}{self.path}"
        try:
            with urllib.request.urlopen(upstream_url, timeout=10) as resp:
                body = resp.read()
                status = resp.status
                upstream_headers = dict(resp.headers.items())
        except urllib.error.HTTPError as e:
            body = e.read()
            status = e.code
            upstream_headers = dict(e.headers.items()) if e.headers else {}
        except Exception as e:
            status = 502
            body = json.dumps({"error": str(e), "upstream": upstream_url}).encode()
            upstream_headers = {"content-type": "application/json"}

        wrapped = {
            "container_says": "i made an HTTP call to a private hostname",
            "upstream_url": upstream_url,
            "upstream_status": status,
            "upstream_x_served_by": upstream_headers.get("x-served-by"),
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
    print("[container] listening on :8080, proxying to", PRIVATE_API, flush=True)
    srv.serve_forever()
