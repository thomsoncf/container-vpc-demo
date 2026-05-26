#!/usr/bin/env python3
"""
Probe the Workers VPC binding timeout end-to-end.

Fires N HTTP requests in parallel against
  https://vpc-demo.demoflair.com/direct-vpc-slow?delay=D
and (optionally) the container-mediated variant
  https://vpc-demo.demoflair.com/vpc-slow?delay=D

Each request asks the origin (slow-service on the VM, behind a Workers VPC
binding via a cloudflared tunnel) to sleep for D seconds and then respond.

Streams a one-line result for each probe as soon as it finishes:
    [   5s] direct  delay=100  ->  HTTP 200  elapsed= 100.27s  ok
    [ 270s] direct  delay=280  ->  HTTP 504  elapsed= 270.06s  ERR: Network connection lost.

Usage:
    python3 scripts/probe-vpc-timeout.py
    python3 scripts/probe-vpc-timeout.py --delays 100,200,260,275,290
    python3 scripts/probe-vpc-timeout.py --paths direct
    python3 scripts/probe-vpc-timeout.py --host vpc-demo.demoflair.com
"""

import argparse
import concurrent.futures as cf
import json
import time
import urllib.request
import urllib.error
from dataclasses import dataclass
from typing import Optional

DEFAULT_HOST = "vpc-demo.demoflair.com"
DEFAULT_DELAYS = [100, 200, 260, 265, 269, 271, 275, 280]
DEFAULT_PATHS = ["direct", "container"]
SOCKET_TIMEOUT = 360  # 6 min — well past any expected backend timeout


@dataclass
class Probe:
    path: str        # "direct" or "container"
    delay: int

    @property
    def label(self) -> str:
        return f"{self.path:<9} delay={self.delay:>3}s"


@dataclass
class Result:
    probe: Probe
    http_status: Optional[int]
    elapsed_s: float
    error: Optional[str]
    upstream_status: Optional[int]
    upstream_error: Optional[str]

    def line(self, t_wall: float) -> str:
        parts = [
            f"[{t_wall:6.1f}s]",
            self.probe.label,
            "->",
            f"HTTP {self.http_status if self.http_status is not None else '---'}",
            f"elapsed={self.elapsed_s:7.2f}s",
        ]
        if self.error:
            parts.append(f"ERR: {self.error}")
        elif self.upstream_error:
            parts.append(
                f"upstream_status={self.upstream_status} upstream_err={self.upstream_error}"
            )
        else:
            parts.append(f"upstream_status={self.upstream_status} ok")
        return "  ".join(parts)


def run(probe: Probe, host: str, t_start: float) -> Result:
    nonce = int(time.time() * 1000)
    if probe.path == "direct":
        path = "/direct-vpc-slow"
    elif probe.path == "container":
        path = "/vpc-slow"
    else:
        raise ValueError(f"unknown path: {probe.path}")

    url = f"https://{host}{path}?delay={probe.delay}&nonce={nonce}"
    req = urllib.request.Request(url)
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=SOCKET_TIMEOUT) as resp:
            body = resp.read()
            status = resp.status
    except urllib.error.HTTPError as e:
        body = e.read() if e.fp else b""
        status = e.code
    except Exception as e:
        return Result(
            probe=probe,
            http_status=None,
            elapsed_s=time.time() - started,
            error=f"{type(e).__name__}: {e}",
            upstream_status=None,
            upstream_error=None,
        )

    elapsed = time.time() - started
    upstream_status: Optional[int] = None
    upstream_error: Optional[str] = None
    try:
        j = json.loads(body)
        if probe.path == "direct":
            upstream_status = j.get("upstream_status")
            upstream_error = j.get("error")
        else:
            upstream_status = j.get("upstream_status")
            ub = j.get("upstream_body") or {}
            if isinstance(ub, dict) and ub.get("vpc_error"):
                upstream_error = ub.get("error")
    except Exception:
        pass

    return Result(
        probe=probe,
        http_status=status,
        elapsed_s=elapsed,
        error=None,
        upstream_status=upstream_status,
        upstream_error=upstream_error,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument(
        "--delays",
        default=",".join(str(d) for d in DEFAULT_DELAYS),
        help="comma-separated delay seconds (default: %(default)s)",
    )
    ap.add_argument(
        "--paths",
        default=",".join(DEFAULT_PATHS),
        help="comma-separated of {direct, container} (default: %(default)s)",
    )
    args = ap.parse_args()

    delays = sorted({int(x) for x in args.delays.split(",") if x.strip()})
    paths = [p.strip() for p in args.paths.split(",") if p.strip()]
    probes = [Probe(path=p, delay=d) for p in paths for d in delays]

    print(f"host:   {args.host}")
    print(f"paths:  {paths}")
    print(f"delays: {delays}")
    print(f"probes: {len(probes)} (running in parallel)")
    print("-" * 96)

    t0 = time.time()
    results: list[Result] = []
    with cf.ThreadPoolExecutor(max_workers=len(probes)) as ex:
        futures = {ex.submit(run, p, args.host, t0): p for p in probes}
        for fut in cf.as_completed(futures):
            r = fut.result()
            results.append(r)
            print(r.line(time.time() - t0), flush=True)

    print("-" * 96)
    print("summary (sorted by elapsed)")
    for r in sorted(results, key=lambda r: r.elapsed_s):
        verdict = (
            "OK"
            if (
                r.http_status == 200
                and r.upstream_status == 200
                and not r.error
                and not r.upstream_error
            )
            else "FAIL"
        )
        extra = r.error or r.upstream_error or ""
        print(
            f"  {r.probe.label}  elapsed={r.elapsed_s:7.2f}s  http={r.http_status}  "
            f"upstream={r.upstream_status}  {verdict}  {extra}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
