"""Check a running ChequeVerify AI server from the outside, with your own cheque and signature images.

    python3 scripts/live_check.py https://YOUR-SERVICE.onrender.com \
        --cheques "/path/to/OCR dataset" --signatures "/path/to/signature dataset"

Run it on your own computer. It asks for the administrator password with hidden typing (or reads CV_LIVE_PASSWORD) and keeps
it in memory only. Standard library only, no installation needed.

What it does, in order
  1. waits for the server to wake up (a free Render service sleeps after 15 quiet minutes and takes about a minute to start)
  2. health check, the page and its script/style files, the security headers, the sign-in options, protected routes
     refusing a visitor who is not signed in, a wrong password being refused
  3. signs in as the administrator and checks that the trained models are loaded
  4. uploads cheque images one at a time (the way the Batch page does) and reports how long each took
  5. optionally enrols a few signers from the signature dataset and verifies a held-out genuine signature and a forgery
  6. removes everything it created, unless you pass --keep (then it stays visible in the application)

What it prints: PASS / FAIL lines, counts, decisions and seconds. Never the password, the sign-in token, file names, or any
value read from a cheque, so the output is safe to paste into a report or a chat.

Exit status: 0 = every check passed, 1 = at least one failed, 2 = the server could not be reached or the sign-in failed.
"""
from __future__ import annotations

import argparse
import csv
import datetime
import getpass
import json
import mimetypes
import os
import re
import secrets
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import Counter, defaultdict
from pathlib import Path

IMAGE_EXTS = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".bmp": "image/bmp", ".tif": "image/tiff", ".tiff": "image/tiff"}
FIELDS_PER_CHEQUE = 10
RESULTS: list[tuple[str, bool]] = []
CREATED: dict[str, list[int]] = {"verifications": [], "cheques": [], "signers": []}


# ------------------------------------------------------------------ plumbing
class Server:
    def __init__(self, base: str, timeout: int):
        self.base, self.timeout, self.token = base.rstrip("/"), timeout, None

    def call(self, method, path, json_body=None, files=None, fields=None, token=True, timeout=None):
        """Returns (status, headers, body). body is parsed JSON for JSON answers, otherwise bytes. Never raises for HTTP errors."""
        headers, data = {"Accept": "application/json, text/html;q=0.9, */*;q=0.5"}, None
        if token and self.token:
            headers["Authorization"] = "Bearer " + self.token
        if json_body is not None:
            data, headers["Content-Type"] = json.dumps(json_body).encode(), "application/json"
        if files or fields:
            boundary = uuid.uuid4().hex
            parts = []
            for key, value in (fields or {}).items():
                parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
            for key, items in (files or {}).items():
                for name, content, ctype in (items if isinstance(items, list) else [items]):
                    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"; filename="{name}"\r\n'
                                 f"Content-Type: {ctype}\r\n\r\n".encode() + content + b"\r\n")
            data = b"".join(parts) + f"--{boundary}--\r\n".encode()
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        request = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as resp:
                return resp.status, resp.headers, self._body(resp.read(), resp.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers, self._body(exc.read(), exc.headers)

    @staticmethod
    def _body(raw: bytes, headers):
        if (headers.get_content_type() or "") == "application/json":
            try:
                return json.loads(raw or b"{}")
            except ValueError:
                return {}
        return raw


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, bool(ok)))
    print(("PASS  " if ok else "FAIL  ") + name + (f"   [{detail}]" if detail and not ok else ""), flush=True)
    return bool(ok)


def info(text: str) -> None:
    print("      " + text, flush=True)


def data_of(body) -> dict:
    return body.get("data", {}) if isinstance(body, dict) and isinstance(body.get("data"), (dict, list)) else {}


def error_code(body) -> str:
    return str(((body or {}).get("error") or {}).get("code", "")) if isinstance(body, dict) else ""


def secs(x: float) -> str:
    return f"{x:.1f} s"


def find_images(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_EXTS)


def upload_file(path: Path) -> tuple[str, bytes, str]:
    return ("image" + path.suffix.lower(), path.read_bytes(), IMAGE_EXTS.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream")


# ------------------------------------------------------------------ the checks
def wake_and_health(srv: Server, wait: int) -> bool:
    start, attempts = time.monotonic(), 0
    while True:
        attempts += 1
        try:
            status, _, body = srv.call("GET", "/api/health", token=False, timeout=30)
            if status == 200 and data_of(body).get("status") == "ok":
                took = time.monotonic() - start
                check("health: /api/health answers ok", True)
                info(f"answered after {secs(took)} ({attempts} attempt{'s' if attempts != 1 else ''})"
                     + ("; a sleeping free service needs up to a minute for the first answer" if took > 10 else ""))
                return True
        except (urllib.error.URLError, OSError, TimeoutError):
            pass
        if time.monotonic() - start > wait:
            check("health: /api/health answers ok", False, f"no answer within {wait} s")
            return False
        time.sleep(5)


def page_and_headers(srv: Server) -> None:
    status, headers, body = srv.call("GET", "/", token=False)
    page = body.decode("utf-8", "replace") if isinstance(body, bytes) else ""
    check("page: the application is served at /", status == 200 and 'id="root"' in page)
    assets = sorted(set(re.findall(r'(?:src|href)="(/assets/[^"]+)"', page)))
    bad = []
    for asset in assets:
        s, h, b = srv.call("GET", asset, token=False)
        if s != 200 or not b:
            bad.append(s)
    check("page: its script and style files load", bool(assets) and not bad, f"{len(assets)} files, bad statuses {bad}")
    info(f"{len(assets)} script/style files, all served" if assets and not bad else "")
    https = srv.base.startswith("https://")
    wanted = {"Content-Security-Policy": None, "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer"}
    api_headers = srv.call("GET", "/api/health", token=False)[1]
    for label, hdrs in (("page", headers), ("API", api_headers)):
        missing = [k for k, v in wanted.items() if not hdrs.get(k) or (v and hdrs.get(k, "").lower() != v.lower())]
        if https and not hdrs.get("Strict-Transport-Security"):
            missing.append("Strict-Transport-Security")
        check(f"headers: security headers on the {label}", not missing, "missing or wrong: " + ", ".join(missing))
    check("headers: API answers are not cached", "no-store" in (api_headers.get("Cache-Control") or ""))


def public_surface(srv: Server) -> None:
    status, _, body = srv.call("GET", "/api/auth/config", token=False)
    cfg = data_of(body)
    check("sign-in options are public", status == 200 and {"password_signin", "email_signin"} <= set(cfg), str(status))
    if "storage_temporary" in cfg:
        info("storage: TEMPORARY (no disk; accounts and cheques are erased on restart or sleep)" if cfg["storage_temporary"]
             else "storage: persistent")
    info(f"password sign-in {'on' if cfg.get('password_signin') else 'off'}, email sign-in {'on' if cfg.get('email_signin') else 'off'}")
    for path in ("/api/cheques", "/api/users", "/api/signers", "/api/settings", "/api/audit-log", "/api/verifications"):
        s, _, b = srv.call("GET", path, token=False)
        if not check(f"anonymous visitor refused: GET {path}", s == 401 and not data_of(b), str(s)):
            break
    s, _, b = srv.call("POST", "/api/auth/login", {"username": "livecheck-nobody", "password": secrets.token_urlsafe(18)}, token=False)
    check("a wrong password is refused", s == 401 and "token" not in json.dumps(b), str(s))


def sign_in(srv: Server, user: str, password: str) -> bool:
    for attempt in range(3):
        s, h, b = srv.call("POST", "/api/auth/login", {"username": user, "password": password}, token=False)
        if s == 429:                                            # throttled: wait the advised time (at most a minute), try again
            wait = h.get("Retry-After") or "20"
            time.sleep(min(60, int(wait)) if wait.isdigit() else 20)
            continue
        break
    token = data_of(b).get("token") if s == 200 else None
    if not check(f"sign-in as '{user}'", bool(token), f"HTTP {s} {error_code(b)}"):
        return False
    srv.token = token
    s, _, b = srv.call("GET", "/api/auth/me")
    role = data_of(b).get("role") if s == 200 else None
    check("the signed-in account is an administrator", role == "admin", f"role {role}")
    return True


def models_loaded(srv: Server) -> None:
    s, _, b = srv.call("GET", "/api/models/status")
    d = data_of(b)
    check("trained models are loaded (MICR + signature)", s == 200 and d.get("micr_model", {}).get("available") and d.get("signature_model", {}).get("available"), str(s))
    if s == 200:
        eng = d.get("ocr_engine", {})
        check("OCR engine is available", bool(eng.get("available")), "no OCR engine")


def pick_round_robin(images: list[Path], limit: int) -> list[Path]:
    groups: dict[str, list[Path]] = defaultdict(list)
    for p in images:
        groups[p.parent.name].append(p)
    order, picked = sorted(groups), []
    while len(picked) < limit and any(groups.values()):
        for k in order:
            if groups[k] and len(picked) < limit:
                picked.append(groups[k].pop(0))
    return picked


def cheques(srv: Server, root: Path, limit: int) -> None:
    images = find_images(root)
    if not images:
        check("cheque images found in --cheques", False, "no .png/.jpg files under that folder")
        return
    chosen = pick_round_robin(images, limit)
    info(f"{len(images)} images in the folder, trying {len(chosen)} (spread across the bank folders)")
    times, filled_total, review_total, done = [], 0, 0, 0
    for i, path in enumerate(chosen, 1):
        t0 = time.monotonic()
        s, _, b = srv.call("POST", "/api/cheques", files={"file": upload_file(path)})
        dt = time.monotonic() - t0
        d = data_of(b)
        fields = d.get("fields", {}) if s == 201 else {}
        filled = sum(1 for f in fields.values() if f.get("value"))
        review = sum(1 for f in fields.values() if f.get("status") != "ok")
        ok = s == 201 and len(d.get("field_order", [])) == FIELDS_PER_CHEQUE
        check(f"cheque {i}/{len(chosen)} ({path.parent.name}): uploaded and read", ok, f"HTTP {s} {error_code(b)}")
        if ok:
            CREATED["cheques"].append(d["id"])
            times.append(dt)
            filled_total += filled
            review_total += review
            done += 1
            info(f"{secs(dt)}; {filled} of {FIELDS_PER_CHEQUE} fields read; {review} flagged for review; "
                 f"signature region {'found' if d.get('has_signature_crop') else 'not found'}")
        elif s in (502, 503, 504):
            info("the server was busy or restarting; stopping the cheque test here")
            break
    if times:
        info(f"summary: {done} cheques, median {secs(statistics.median(times))}, slowest {secs(max(times))}; "
             f"on average {filled_total / done:.1f} of {FIELDS_PER_CHEQUE} fields read, {review_total / done:.1f} flagged for review")
    # two more through the batch endpoint, one request each, under one batch id (what the Batch page does)
    if len(chosen) >= 2 and done >= 2:
        batch_id = secrets.token_hex(6)
        ids = []
        for path in chosen[:2]:
            s, _, b = srv.call("POST", "/api/cheques/batch", fields={"batch_id": batch_id}, files={"files": upload_file(path)})
            rows = data_of(b).get("results", []) if s in (200, 201) else []
            ids += [r["id"] for r in rows if r.get("ok") and r.get("id")]
        CREATED["cheques"] += [i for i in ids if i not in CREATED["cheques"]]
        s, _, b = srv.call("GET", f"/api/cheques?batch_id={batch_id}&per_page=10")
        got = len(data_of(b)) if s == 200 else 0
        check("batch: two cheques sent one by one share one batch id", len(ids) == 2 and got in (0, 2), f"{len(ids)} processed, {got} listed")


def read_manifest(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return [{(k or "").strip(): (v or "").strip() for k, v in row.items()} for row in csv.DictReader(fh)]


def signatures(srv: Server, root: Path, how_many: int) -> None:
    """The delivered signature dataset holds one genuine specimen per signer and altered copies of it, so this enrols that one
    specimen and verifies (a) the same image and (b) each altered copy. It shows that the pipeline works end to end; it cannot
    measure accuracy, and a single specimen never makes a reference set ready, so no result can be MATCH."""
    base = next((p.parent for p in [root / "genuine_signatures.csv", *sorted(root.glob("*/genuine_signatures.csv")), *sorted(root.glob("*/*/genuine_signatures.csv"))] if p.is_file()), None)
    if base is None:
        check("signature dataset found in --signatures", False, "genuine_signatures.csv not found")
        return
    genuine = read_manifest(base / "genuine_signatures.csv")
    forged_path = base / "forged_signatures.csv"
    forged = read_manifest(forged_path) if forged_path.is_file() else []
    by_signer: dict[str, dict] = {}
    for r in genuine:
        r["path"] = base / "signature_dataset" / r.get("split", "") / r.get("bank", "") / r.get("filename", "")
        if r["path"].is_file():
            by_signer.setdefault(r["signer_id"], r)
    fake_for: dict[str, list[dict]] = defaultdict(list)
    for r in forged:
        r["path"] = base / "forged_signatures" / r.get("split", "") / r.get("bank", "") / r.get("filename", "")
        if r["path"].is_file():
            fake_for[r["target_signer_id"]].append(r)
    usable = [s for s in sorted(by_signer) if fake_for.get(s)]
    chosen, banks_seen = [], set()
    for s in usable:                                            # one signer per bank first, then any
        if by_signer[s]["bank"] not in banks_seen and len(chosen) < how_many:
            banks_seen.add(by_signer[s]["bank"])
            chosen.append(s)
    chosen += [s for s in usable if s not in chosen][: max(0, how_many - len(chosen))]
    info(f"{len(by_signer)} signers with an image in the manifest, {len(usable)} with altered copies; testing {len(chosen)}")
    decisions: Counter = Counter()
    scores: dict[str, list[float]] = defaultdict(list)
    for n, signer in enumerate(chosen, 1):
        row = by_signer[signer]
        s, _, b = srv.call("POST", "/api/signers", {"signer_code": "LIVECHK-" + secrets.token_hex(3).upper(), "bank": row["bank"].lower()})
        if not check(f"signer {n}/{len(chosen)}: created", s == 201, f"HTTP {s} {error_code(b)}"):
            continue
        sid = data_of(b)["id"]
        CREATED["signers"].append(sid)
        s, _, b = srv.call("POST", f"/api/signers/{sid}/specimens", files={"file": upload_file(row["path"])})
        if not check(f"signer {n}/{len(chosen)}: genuine specimen enrolled", s == 201, f"HTTP {s} {error_code(b)}"):
            continue
        queries = [("genuine (the enrolled image)", row)] + [(f"altered copy {k}", r) for k, r in enumerate(fake_for[signer][:2], 1)]
        for kind, q in queries:
            s, _, b = srv.call("POST", "/api/verifications", fields={"signer_id": sid, "mode": "signature"}, files={"file": upload_file(q["path"])})
            d = data_of(b)
            ok = s == 201 and d.get("decision") in ("MATCH", "REVIEW", "MISMATCH")
            check(f"signer {n}/{len(chosen)}: {kind} verified", ok, f"HTTP {s} {error_code(b)}")
            if ok:
                CREATED["verifications"].append(d["id"])
                group = "genuine" if kind.startswith("genuine") else "altered"
                decisions[(group, d["decision"])] += 1
                scores[group].append(float(d.get("score") or 0))
                if group == "altered":
                    check(f"signer {n}/{len(chosen)}: {kind} is not accepted as MATCH", d["decision"] != "MATCH", d["decision"])
    if decisions:
        info("decisions: " + ", ".join(f"{k[0]} -> {k[1]} x{v}" for k, v in sorted(decisions.items())))
    if scores["genuine"] and scores["altered"]:
        info(f"mean score: genuine {statistics.mean(scores['genuine']):.2f}, altered {statistics.mean(scores['altered']):.2f} "
             f"(a handful of samples: this shows the pipeline works end to end, it does not measure accuracy; one specimen per signer "
             f"can never make a reference set ready, so MATCH is capped at REVIEW by design)")


def clean_up(srv: Server) -> None:
    left = 0
    for kind in ("verifications", "cheques", "signers"):
        for ident in CREATED[kind]:
            s, _, _ = srv.call("DELETE", f"/api/{kind}/{ident}")
            left += s not in (200, 204)
    if any(CREATED.values()):
        info("test records removed" if not left else f"{left} test records could not be removed (delete them in the application)")


# ------------------------------------------------------------------ main
def main() -> int:
    ap = argparse.ArgumentParser(description="Check a running ChequeVerify AI server from the outside. Prints no private data.")
    ap.add_argument("url", help="e.g. https://chequeverify-ai.onrender.com")
    ap.add_argument("--user", default="admin", help="administrator user name (default: admin)")
    ap.add_argument("--cheques", type=Path, help="folder with cheque images (searched recursively)")
    ap.add_argument("--signatures", type=Path, help="folder holding genuine_signatures.csv and forged_signatures.csv")
    ap.add_argument("--limit", type=int, default=6, help="how many cheques to upload (default 6)")
    ap.add_argument("--signers", type=int, default=3, help="how many signers to test (default 3)")
    ap.add_argument("--wait", type=int, default=240, help="seconds to wait for a sleeping server to wake (default 240)")
    ap.add_argument("--timeout", type=int, default=240, help="seconds to wait for one request (default 240)")
    ap.add_argument("--keep", action="store_true", help="keep the cheques, signers and verifications it created, so you can look at them in the application")
    args = ap.parse_args()

    if not args.url.startswith(("https://", "http://127.0.0.1", "http://localhost")):
        print("Use the https:// address of the server (plain http is only accepted for 127.0.0.1 / localhost).")
        return 2
    srv = Server(args.url, args.timeout)
    host = urllib.parse.urlparse(args.url).netloc
    print(f"ChequeVerify AI live check of {host}  ({datetime.datetime.now().strftime('%Y-%m-%d %H:%M')})\n")

    if not wake_and_health(srv, args.wait):
        return 2
    page_and_headers(srv)
    public_surface(srv)
    password = os.environ.get("CV_LIVE_PASSWORD") or getpass.getpass(f"Password for '{args.user}' on {host} (hidden while you type): ")
    if not sign_in(srv, args.user, password):
        print("\nSign-in failed. Check the password you typed into CV_ADMIN_PASSWORD on Render (at least 12 characters, letters plus digits or symbols).")
        return 2
    del password
    try:
        models_loaded(srv)
        if args.cheques:
            cheques(srv, args.cheques, max(1, args.limit))
        if args.signatures:
            signatures(srv, args.signatures, max(1, args.signers))
    finally:
        if not args.keep:
            clean_up(srv)

    failed = [name for name, ok in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed")
    for name in failed:
        print("  FAILED: " + name)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
