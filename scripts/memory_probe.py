#!/usr/bin/env python3
"""Upload the largest accepted images in parallel and check that the service survives (used by CI inside a 512 MB container).

    python3 scripts/memory_probe.py http://127.0.0.1:8000 <admin-password> [--side 3462] [--parallel 8]

--side 3462 is a 12-megapixel image, the limit set in render.yaml (CV_MAX_IMAGE_PIXELS). Every upload must be either
processed (201) or answered "busy, retry" (503); anything else, a dropped connection, or a service that no longer
answers /api/health afterwards is a failure (exit 1). Standard library only."""
from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from deploy_simulation import big_png, http  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("base")
    ap.add_argument("password")
    ap.add_argument("--side", type=int, default=3462, help="image width and height in pixels")
    ap.add_argument("--parallel", type=int, default=8, help="uploads started at the same moment")
    args = ap.parse_args(argv)
    base = args.base.rstrip("/")
    try:
        status, body = http("POST", base + "/api/auth/login", body={"username": "admin", "password": args.password})
    except OSError as exc:
        print(f"cannot reach {base}: {exc}")
        return 1
    if status != 200:
        print(f"sign-in failed (HTTP {status})")
        return 1
    token = body["data"]["token"]
    png = big_png(args.side)
    print(f"{args.parallel} uploads of {args.side}x{args.side} = {args.side * args.side / 1e6:.1f} megapixels ({len(png) // 1024} KB each)")
    outcomes: list[object] = []

    def post() -> None:
        try:
            outcomes.append(http("POST", base + "/api/cheques", token, files={"file": ("probe.png", png)}, timeout=240)[0])
        except OSError as exc:                                   # connection reset = the server died
            outcomes.append(f"connection lost: {exc}")
    threads = [threading.Thread(target=post) for _ in range(args.parallel)]
    started = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join(300)
    elapsed = time.time() - started
    counts = {str(k): outcomes.count(k) for k in sorted(set(outcomes), key=str)}
    print(f"finished in {elapsed:.1f} s: {counts}")
    try:
        health = http("GET", base + "/api/health", timeout=15)[0]
    except OSError:
        health = 0
    print(f"/api/health afterwards: HTTP {health}")
    good = len(outcomes) == args.parallel and all(o in (200, 201, 503) for o in outcomes) and any(o in (200, 201) for o in outcomes)
    return 0 if good and health == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
