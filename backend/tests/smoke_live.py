"""Live smoke test against a running server (used by CI inside the Docker container).

    python tests/smoke_live.py http://127.0.0.1:8000 <admin-password>

Uses synthetic images only. Exercises: health, which sign-in methods are offered and how the email sign-in
endpoints answer without a mailbox to read, login, create operator, create signer, enroll a
specimen, upload a cheque (OCR + signature detection), verify from the cheque, verify an edited copy
(must be MISMATCH), manual review, history, CSV + PDF report, persistence of the record, and the
enrollment workflow: signing sessions, batch enrollment, reference-set readiness and the cap on MATCH."""
import datetime
import io
import json
import sys
import urllib.error
import urllib.request
import uuid

import cv2
import numpy as np

BASE, ADMIN_PW = sys.argv[1].rstrip("/"), sys.argv[2]
results = []


def req(method, path, token=None, json_body=None, files=None, fields=None):
    """files: {field: (name, bytes)} or {field: [(name, bytes), ...]} for several files under one field."""
    headers, data = {}, None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if json_body is not None:
        data, headers["Content-Type"] = json.dumps(json_body).encode(), "application/json"
    if files or fields:
        b = uuid.uuid4().hex
        parts = []
        for k, v in (fields or {}).items():
            parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
        for k, item in (files or {}).items():
            for name, content in (item if isinstance(item, list) else [item]):
                parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"; filename="{name}"\r\n'
                             f'Content-Type: image/png\r\n\r\n'.encode() + content + b"\r\n")
        data = b"".join(parts) + f"--{b}--\r\n".encode()
        headers["Content-Type"] = f"multipart/form-data; boundary={b}"
    r = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=120) as resp:
            body = resp.read()
            return resp.status, (json.loads(body) if resp.headers.get_content_type() == "application/json" else body)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""), flush=True)


def signature(seed):
    rng = np.random.default_rng(seed)
    img = np.full((100, 320, 3), 245, np.uint8)
    pts = np.cumsum(rng.normal(0, 6, (40, 2)), axis=0)
    pts = (pts - pts.min(0)) / max(np.ptp(pts), 1) * [280, 70] + [20, 15]
    cv2.polylines(img, [pts.astype(np.int32)], False, (30, 30, 120), 2, cv2.LINE_AA)
    cv2.line(img, (25, 80), (290, 75), (30, 30, 120), 2)
    return img


png = lambda img: cv2.imencode(".png", img)[1].tobytes()
sig = signature(42)
cheque = np.full((470, 1024, 3), 248, np.uint8)
cv2.putText(cheque, "CANARA BANK  IFSC: CNRB0002854", (40, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (60, 60, 60), 2)
cv2.putText(cheque, "Pay  Test Payee", (40, 120), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (20, 20, 20), 2)
cheque[290:390, 690:1010] = sig
edited = sig.copy()
cv2.line(edited, (60, 20), (200, 80), (40, 40, 40), 2)

s, b = req("GET", "/api/health")
check("health", s == 200 and b["data"]["status"] == "ok", b)
s, b = req("GET", "/")
check("frontend served", s == 200 and b"<div id=\"root\">" in b)
s, b = req("GET", "/api/auth/config")
check("the sign-in methods are public", s == 200 and {"email_signin", "password_signin", "email_domains"} <= set(b["data"]), b)
offered = b["data"]
if not offered["email_signin"]:
    s, b = req("POST", "/api/auth/email/request", json_body={"email": "first.last@saividya.ac.in"})
    check("email sign-in is refused while no mail delivery is configured",
          s == 503 and b["error"]["code"] == "email_signin_unavailable", b)
s, b = req("POST", "/api/auth/email/verify", json_body={"email": "first.last@saividya.ac.in", "code": "00000000"})
check("a code that was never issued signs nobody in", s in (401, 503) and "token" not in json.dumps(b), b)
s, b = req("POST", "/api/auth/email/request", json_body={"email": "someone@example.com"})
check("an address from another domain is refused", s in (400, 503), b)
s, b = req("POST", "/api/auth/login", json_body={"username": "admin", "password": ADMIN_PW})
check("admin login", s == 200, b)
A = b["data"]["token"]
s, _ = req("GET", "/api/cheques")
check("unauthenticated request rejected", s == 401)
req("POST", "/api/users", A, {"username": "smokeop", "password": "Smoke-op-123", "role": "operator"})
s, b = req("POST", "/api/auth/login", json_body={"username": "smokeop", "password": "Smoke-op-123"})
O = b["data"]["token"]
s, b = req("POST", "/api/signers", A, {"signer_code": "SMOKE-1", "bank": "canara", "full_name": "Smoke Test"})
check("create signer", s in (201, 409), b)
sid = b["data"]["id"] if s == 201 else req("GET", "/api/signers?q=SMOKE-1", A)[1]["data"][0]["id"]
s, b = req("POST", f"/api/signers/{sid}/specimens", A, files={"file": ("spec.png", png(sig))})
check("enroll specimen", s in (201, 409), b)
s, b = req("POST", f"/api/signers/{sid}/specimens", O, files={"file": ("spec2.png", png(signature(7)))})
check("operator cannot enroll", s == 403)
s, b = req("POST", "/api/cheques", O, files={"file": ("smoke-cheque.png", png(cheque))})
check("upload cheque + OCR", s == 201 and len(b["data"]["field_order"]) == 10, b)
cid = b["data"]["id"]
check("signature detected on cheque", b["data"]["has_signature_crop"])
s, b = req("POST", "/api/verifications", O, fields={"signer_id": sid, "cheque_id": cid})
check("verify signature from cheque", s == 201 and b["data"]["decision"] in ("MATCH", "REVIEW", "MISMATCH"), b)
vid = b["data"]["id"]
s, b = req("POST", "/api/verifications", O, files={"file": ("edited.png", png(edited))},
           fields={"signer_id": sid, "mode": "signature"})
check("edited copy rejected (MISMATCH)", s == 201 and b["data"]["decision"] == "MISMATCH", b.get("data", b))
s, b = req("PATCH", f"/api/verifications/{vid}", O, {"review_decision": "APPROVED", "review_note": "smoke"})
check("manual review saved", s == 200 and b["data"]["review_decision"] == "APPROVED", b)
s, b = req("GET", "/api/verifications?per_page=50", O)
check("history lists verifications", s == 200 and b["meta"]["total"] >= 2, b)
s, b = req("GET", f"/api/cheques/{cid}/report.pdf", O)
check("PDF report", s == 200 and b[:4] == b"%PDF")
s, b = req("GET", "/api/reports/verifications.csv", O)
check("CSV export", s == 200 and b.count(b"\n") >= 3)
# the single unsessioned specimen of SMOKE-1 is not a reference set a MATCH may rest on
s, b = req("GET", f"/api/verifications/{vid}", O)
ref_set = b["data"]["details"]["reference_set"]
check("one unsessioned specimen = reference set not ready", ref_set["ready"] is False and ref_set["independent_samples"] == 0, ref_set)
check("no MATCH without a ready reference set", b["data"]["decision"] != "MATCH", b["data"]["decision"])

# enrollment workflow: two signing sessions on different days, batch upload, readiness
today = datetime.date.today()
s, b = req("POST", "/api/signers", A, {"signer_code": "SMOKE-2", "bank": "axis"})
check("create second signer", s in (201, 409), b)
sid2 = b["data"]["id"] if s == 201 else req("GET", "/api/signers?q=SMOKE-2", A)[1]["data"][0]["id"]
if s == 201:
    sessions = []
    for days_ago, seeds in ((30, (101, 102, 103)), (3, (104, 105))):
        s, b = req("POST", f"/api/signers/{sid2}/sessions", A,
                   {"session_date": (today - datetime.timedelta(days=days_ago)).isoformat(), "method": "paper_scan",
                    "consent_ref": "SMOKE-CONSENT"})
        check(f"record signing session {days_ago} days ago", s == 201, b)
        sess = b["data"]["id"]
        s, b = req("POST", f"/api/signers/{sid2}/specimens/batch", A, fields={"session_id": sess},
                   files={"files": [(f"sig{n}.png", png(signature(n))) for n in seeds]})
        check(f"batch enroll {len(seeds)} signatures", s == 201 and b["data"]["enrolled"] == len(seeds), b)
    s, b = req("GET", f"/api/signers/{sid2}/enrollment", O)
    check("reference set ready after 5 specimens on 2 days", s == 200 and b["data"]["ready"] is True
          and b["data"]["independent_days"] == 2, b)
    s, b = req("POST", f"/api/signers/{sid2}/specimens/batch", A, fields={"session_id": sess},
               files={"files": [("again.png", png(signature(104)))]})
    check("the same image is refused as a duplicate", s == 200 and b["data"]["rejected"] == 1, b)
    s, b = req("POST", f"/api/signers/{sid2}/specimens/batch", O, fields={"session_id": sess},
               files={"files": [("op.png", png(signature(106)))]})
    check("operator cannot batch-enroll", s == 403, b)
s, b = req("GET", "/api/enrollment/overview", O)
check("enrollment overview", s == 200 and b["data"]["signers"] >= 2 and b["data"]["signers_ready"] >= 1, b)

s, b = req("GET", "/api/specimens/1/image", None)
check("specimen image needs auth", s == 401)
s, b = req("GET", "/api/models/status", O)
check("models loaded", s == 200 and b["data"]["micr_model"]["available"] and b["data"]["signature_model"]["available"], b)
print(json.dumps({"passed": sum(r[1] for r in results), "failed": [r[0] for r in results if not r[1]]}))
sys.exit(0 if all(r[1] for r in results) else 1)
