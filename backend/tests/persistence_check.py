"""Persistence check over HTTP: fingerprint what the API can see, then compare after a restart or a redeploy.

    python tests/persistence_check.py snapshot http://127.0.0.1:8000 <admin-password> > state.json
    python tests/persistence_check.py verify   http://127.0.0.1:8000 <admin-password> < state.json
    python tests/persistence_check.py verify   BASE PASSWORD --state state.json [--allow models,reports]

snapshot  Sets one setting, creates an operator and locks that operator out with wrong passwords (so the
          sign-in throttle has something to remember), then prints a fingerprint of everything the API shows:
          users, settings, signers with the hash of every specimen image, enrollment state, cheques with the
          hash of every cheque image, verification history with query-image hashes, audit-log size, model
          versions and every report file. Run it after the smoke test has created some records.
verify    Signs in again and compares with the fingerprint. Run it after a restart, after the container was
          removed and created again on the same volume, or after the code was upgraded. Everything in the
          snapshot must still exist and be identical; new rows are fine (the audit log grows with every
          sign-in). It also checks that the sign-in token issued before the restart is still accepted (the
          secret key persisted) and that the locked-out operator is still locked out. `--allow models,reports`
          tolerates changed model / report files (an upgrade that ships new ones).
          Exit status 0 = identical, 1 = something differs (listed), 2 = could not run.

Standard library only, so it also runs on a CI host without the project's packages. The fingerprint holds a
short-lived sign-in token: delete the file after the check. Reading images and reports through the API is
audited like any other access.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request

LOCK_USER, LOCK_PASSWORD = "persist_lock", "Persist-lock-123"
SETTING_KEY, SETTING_VALUE = "retention.days", 321
FORMAT = 1


class Api:
    def __init__(self, base: str):
        self.base = base.rstrip("/")

    def call(self, method: str, path: str, token: str | None = None, body=None, raw: bool = False):
        headers, data = {}, None
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if body is not None:
            data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                content = resp.read()
                return resp.status, (content if raw else json.loads(content or b"{}"))
        except urllib.error.HTTPError as err:
            content = err.read()
            try:
                return err.code, (content if raw else json.loads(content or b"{}"))
            except ValueError:
                return err.code, content

    def login(self, user: str, password: str):
        return self.call("POST", "/api/auth/login", body={"username": user, "password": password})

    def rows(self, path: str, token: str) -> list:
        """All rows of a paginated list endpoint."""
        out, page = [], 1
        while True:
            sep = "&" if "?" in path else "?"
            status, body = self.call("GET", f"{path}{sep}per_page=100&page={page}", token)
            if status != 200:
                raise RuntimeError(f"GET {path} -> {status}")
            out += body["data"]
            total = (body.get("meta") or {}).get("total", len(out))
            if len(out) >= total or not body["data"]:
                return out
            page += 1


def sha(content) -> str:
    return hashlib.sha256(content if isinstance(content, bytes) else repr(content).encode()).hexdigest()


def image_hash(api: Api, path: str, token: str) -> str:
    status, content = api.call("GET", path, token, raw=True)
    return sha(content) if status == 200 and isinstance(content, bytes) else f"HTTP {status}"


def fingerprint(api: Api, token: str) -> dict:
    fp: dict = {"format": FORMAT}
    fp["app_version"] = api.call("GET", "/api/health")[1]["data"]["version"]
    fp["users"] = sorted(f'{u["username"]}:{u["role"]}:{int(u["is_active"])}' for u in api.rows("/api/users", token))
    fp["settings"] = api.call("GET", "/api/settings", token)[1]["data"]
    signers = {}
    for s in api.rows("/api/signers", token):
        detail = api.call("GET", f'/api/signers/{s["id"]}', token)[1]["data"]
        signers[s["signer_code"]] = {
            "id": s["id"], "bank": s["bank"], "full_name": s["full_name"],
            "enrollment": {k: detail["enrollment"].get(k) for k in ("status", "independent_samples", "independent_days", "ready")},
            "specimens": {str(sp["id"]): {
                "original": image_hash(api, f'/api/specimens/{sp["id"]}/image?variant=original', token),
                "processed": image_hash(api, f'/api/specimens/{sp["id"]}/image', token)}
                for sp in detail["specimens"]}}
    fp["signers"] = signers
    fp["cheques"] = {str(c["id"]): {
        "file": c["original_filename"], "status": c["status"], "cheque_number": c["cheque_number"],
        "payee_name": c["payee_name"], "amount_figures": c["amount_figures"],
        "image": image_hash(api, f'/api/cheques/{c["id"]}/image', token)} for c in api.rows("/api/cheques", token)}
    fp["verifications"] = {str(v["id"]): {
        "decision": v["decision"], "score": v["score"], "signer_code": v["signer_code"], "model_version": v["model_version"],
        "review_decision": v["review_decision"],
        "query_image": image_hash(api, f'/api/verifications/{v["id"]}/query-image', token)}
        for v in api.rows("/api/verifications", token)}
    fp["audit_log_total"] = api.call("GET", "/api/audit-log?per_page=1", token)[1]["meta"]["total"]
    status = api.call("GET", "/api/models/status", token)[1]["data"]
    fp["models"] = {"signature": {k: status["signature_model"].get(k) for k in ("available", "version", "trained_at")},
                    "micr": {k: status["micr_model"].get(k) for k in ("available", "glyphs", "trained_cheques")}}
    fp["reports"] = {r["name"]: image_hash(api, f'/api/reports/files/{r["name"]}', token)
                     for r in api.call("GET", "/api/reports/files", token)[1]["data"]}
    return fp


def snapshot(api: Api, password: str) -> dict:
    status, body = api.login("admin", password)
    if status != 200:
        raise RuntimeError(f"admin sign-in failed ({status})")
    token = body["data"]["token"]
    status, _ = api.call("PUT", "/api/settings", token, {SETTING_KEY: SETTING_VALUE})
    if status != 200:
        raise RuntimeError(f"settings update failed ({status})")
    status, body = api.call("POST", "/api/users", token, {"username": LOCK_USER, "password": LOCK_PASSWORD, "role": "operator"})
    if status not in (201, 409):                                 # 409 = created by an earlier snapshot
        raise RuntimeError(f"could not create the {LOCK_USER} account (HTTP {status}): {body}")
    for i in range(8):                                           # more failures than the limit, whatever it is set to
        status, _ = api.login(LOCK_USER, f"wrong-password-{i}")
        if status == 429:
            break
    if status != 429:
        raise RuntimeError(f"the throttle never blocked {LOCK_USER} (last status {status})")
    fp = fingerprint(api, token)
    fp["token"] = token
    fp["locked_at"] = time.time()
    return fp


def contains(old: dict, new: dict, where: str, out: list[str]) -> None:
    """Every key of `old` must exist in `new` with the same value (new keys are allowed)."""
    for key, value in old.items():
        if key not in new:
            out.append(f"{where}: {key} is missing after the restart")
        elif isinstance(value, dict) and isinstance(new[key], dict):
            contains(value, new[key], f"{where}/{key}", out)
        elif new[key] != value:
            out.append(f"{where}/{key}: {value!r} became {new[key]!r}")


def verify(api: Api, password: str, old: dict, allow: set[str], lock_window: int) -> tuple[list[str], list[str]]:
    problems, notes = [], []
    if old.get("format") != FORMAT:
        return [f"state file has format {old.get('format')!r}, expected {FORMAT}"], notes
    if time.time() - old["locked_at"] < lock_window - 5:
        status, body = api.login(LOCK_USER, LOCK_PASSWORD)
        if status != 429:
            problems.append(f"sign-in throttle was forgotten: {LOCK_USER} got HTTP {status} instead of 429")
    else:
        notes.append("throttle check skipped: the lock window had already elapsed")
    status, _ = api.call("GET", "/api/auth/me", old["token"])
    if status != 200:
        problems.append(f"the token issued before the restart is rejected (HTTP {status}): the secret key did not persist")
    status, body = api.login("admin", password)
    if status != 200:
        return problems + [f"admin sign-in failed after the restart (HTTP {status})"], notes
    new = fingerprint(api, body["data"]["token"])
    for part in ("users",):
        missing = sorted(set(old[part]) - set(new[part]))
        problems += [f"{part}: {m} is missing" for m in missing]
    contains(old["settings"], new["settings"], "settings", problems)
    for part in ("signers", "cheques", "verifications"):
        contains(old[part], new[part], part, problems)
    if new["audit_log_total"] < old["audit_log_total"]:
        problems.append(f'audit log shrank: {old["audit_log_total"]} -> {new["audit_log_total"]}')
    for part in ("models", "reports"):
        diffs: list[str] = []
        contains(old[part], new[part], part, diffs)
        (notes if part in allow else problems).extend(diffs)
    return problems, notes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("snapshot", "verify"))
    ap.add_argument("base")
    ap.add_argument("password")
    ap.add_argument("--state", help="verify: fingerprint file (default: standard input)")
    ap.add_argument("--allow", default="", help="verify: comma list of parts that may change: models, reports")
    ap.add_argument("--lock-window", type=int, default=300, help="CV_LOGIN_LOCK_SECONDS of the server")
    args = ap.parse_args()
    api = Api(args.base)
    try:
        if args.command == "snapshot":
            json.dump(snapshot(api, args.password), sys.stdout, indent=1, sort_keys=True)
            sys.stdout.write("\n")
            return 0
        old = json.load(open(args.state) if args.state else sys.stdin)
        problems, notes = verify(api, args.password, old, set(filter(None, args.allow.split(","))), args.lock_window)
    except (RuntimeError, urllib.error.URLError, OSError, ValueError, KeyError) as exc:
        print(f"persistence check could not run: {exc}", file=sys.stderr)
        return 2
    for n in notes:
        print("NOTE", n)
    for p in problems:
        print("FAIL", p)
    if not problems:
        print("PASS everything in the snapshot survived")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
