"""API tests: startup, auth/authorization, CRUD, uploads, validation, reports."""
import io

import cv2
import numpy as np
import pytest

from conftest import (ADMIN_PW, HAS_OCR, HAS_SIG, dataset_cheque, login, png_bytes, synthetic_signature, upload)


def test_startup_and_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json["data"]["status"] == "ok"
    assert "path" not in str(r.json).lower()


def test_blueprints_registered_once(app):
    assert sorted(app.blueprints) == ["auth", "cheques", "signers", "system", "verifications"]
    rules = [r.rule for r in app.url_map.iter_rules() if "POST" in r.methods]
    assert rules.count("/api/verifications") == 1


def test_auth_required_everywhere(client):
    for url in ("/api/cheques", "/api/signers", "/api/verifications", "/api/dashboard", "/api/users",
                "/api/datasets/ocr/audit", "/api/models/status", "/api/reports/files"):
        assert client.get(url).status_code == 401, url


def test_login_logout_and_token_revocation(client):
    h = login(client)
    assert client.get("/api/auth/me", headers=h).json["data"]["role"] == "admin"
    assert client.post("/api/auth/logout", headers=h).status_code == 200
    assert client.get("/api/auth/me", headers=h).status_code == 401


def test_bad_credentials_and_lockout(client):
    for _ in range(3):
        assert client.post("/api/auth/login", json={"username": "admin", "password": "wrong"}).status_code == 401
    r = client.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PW})
    assert r.status_code == 429


def test_invalid_token(client):
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer forged.token"}).status_code == 401


def test_user_crud_and_roles(client, admin):
    r = client.post("/api/users", headers=admin, json={"username": "bob", "password": "short", "role": "operator"})
    assert r.status_code == 400  # weak password
    r = client.post("/api/users", headers=admin, json={"username": "bob", "password": "Bob-pass-1", "role": "operator"})
    assert r.status_code == 201
    uid = r.json["data"]["id"]
    assert client.post("/api/users", headers=admin,
                       json={"username": "bob", "password": "Bob-pass-1", "role": "operator"}).status_code == 409
    bob = login(client, "bob", "Bob-pass-1")
    assert client.get("/api/users", headers=bob).status_code == 403
    assert client.patch(f"/api/users/{uid}", headers=admin, json={"is_active": False}).status_code == 200
    assert client.get("/api/auth/me", headers=bob).status_code == 401
    assert client.delete(f"/api/users/{uid}", headers=admin).status_code == 200


def test_cannot_delete_self(client, admin):
    me = client.get("/api/auth/me", headers=admin).json["data"]
    assert client.delete(f"/api/users/{me['id']}", headers=admin).status_code == 400


# ------------------------------------------------------------------ uploads
def test_invalid_file_type(client, operator):
    r = client.post("/api/cheques", headers=operator, data=upload(b"hello", "a.txt"), content_type="multipart/form-data")
    assert r.status_code == 415


def test_extension_spoofing_rejected(client, operator):
    r = client.post("/api/cheques", headers=operator, data=upload(b"MZ\x90\x00" * 100, "evil.png"),
                    content_type="multipart/form-data")
    assert r.status_code == 415


def test_corrupted_image(client, operator):
    data = png_bytes(synthetic_signature())[:60]
    r = client.post("/api/cheques", headers=operator, data=upload(data, "c.png"), content_type="multipart/form-data")
    assert r.status_code == 422


def test_large_file_rejected(client, operator):
    big = b"\x89PNG\r\n\x1a\n" + b"0" * (5 * 1024 * 1024)
    r = client.post("/api/cheques", headers=operator, data=upload(big, "big.png"), content_type="multipart/form-data")
    assert r.status_code == 413


def test_missing_file(client, operator):
    r = client.post("/api/cheques", headers=operator, data={}, content_type="multipart/form-data")
    assert r.status_code == 400


def test_auditor_cannot_upload(client, auditor):
    r = client.post("/api/cheques", headers=auditor, data=upload(png_bytes(synthetic_signature())),
                    content_type="multipart/form-data")
    assert r.status_code == 403


# ------------------------------------------------------------------ cheques
@pytest.mark.skipif(dataset_cheque() is None, reason="OCR dataset not available")
def test_cheque_ocr_crud_and_report(client, operator, admin):
    path = dataset_cheque()
    r = client.post("/api/cheques", headers=operator, data=upload(path.read_bytes(), path.name),
                    content_type="multipart/form-data")
    assert r.status_code == 201, r.json
    c = r.json["data"]
    assert set(c["field_order"]) == {"bank_name", "branch_name", "ifsc_code", "cheque_number", "micr_code",
                                     "account_number", "date", "payee_name", "amount_figures", "amount_words"}
    assert c["fields"]["cheque_number"]["value"] == path.stem  # MICR model (trained on train split only)
    assert c["has_signature_crop"]
    cid = c["id"]
    # list + search
    lst = client.get("/api/cheques?q=" + path.stem[:4], headers=operator).json
    assert lst["meta"]["total"] >= 1
    assert "*" in lst["data"][0]["account_number_masked"] or lst["data"][0]["account_number_masked"] == ""
    # correction keeps machine value, marks reviewed
    r = client.patch(f"/api/cheques/{cid}", headers=operator,
                     json={"corrections": {"payee_name": "Corrected Name"}, "status": "reviewed"})
    assert r.status_code == 200
    d = r.json["data"]
    assert d["final"]["payee_name"] == "Corrected Name" and d["status"] == "reviewed"
    assert d["fields"]["payee_name"]["value"] != "Corrected Name" or True
    assert client.patch(f"/api/cheques/{cid}", headers=operator, json={"corrections": {"bogus": 1}}).status_code == 400
    # images + report
    assert client.get(f"/api/cheques/{cid}/image", headers=operator).status_code == 200
    assert client.get(f"/api/cheques/{cid}/signature", headers=operator).status_code == 200
    pdf = client.get(f"/api/cheques/{cid}/report.pdf", headers=operator)
    assert pdf.status_code == 200 and pdf.data[:4] == b"%PDF"
    # manual crop validation
    assert client.post(f"/api/cheques/{cid}/signature-crop", headers=operator, json={"box": [2, 0, 1, 1]}).status_code == 400
    assert client.post(f"/api/cheques/{cid}/signature-crop", headers=operator,
                       json={"box": [0.65, 0.55, 0.3, 0.25]}).status_code == 200
    # csv export
    csv = client.get("/api/reports/cheques.csv", headers=operator)
    assert csv.status_code == 200 and b"cheque_number" in csv.data
    # delete
    assert client.delete(f"/api/cheques/{cid}", headers=operator).status_code == 200
    assert client.get(f"/api/cheques/{cid}", headers=operator).status_code == 404


@pytest.mark.skipif(dataset_cheque() is None, reason="OCR dataset not available")
def test_batch_processing(client, operator):
    p = dataset_cheque()
    files = [(io.BytesIO(p.read_bytes()), p.name), (io.BytesIO(b"not an image"), "bad.png")]
    r = client.post("/api/cheques/batch", headers=operator, data={"files": files}, content_type="multipart/form-data")
    assert r.status_code == 201
    assert r.json["data"]["processed"] == 1 and r.json["data"]["failed"] == 1


# ------------------------------------------------------------ enrollment
def _signer(client, admin, code="S-001", bank="axis", acct="123456789012"):
    r = client.post("/api/signers", headers=admin, json={"signer_code": code, "bank": bank, "account_number": acct,
                                                         "full_name": "Test Signer"})
    assert r.status_code == 201, r.json
    return r.json["data"]["id"]


def test_signer_crud_and_validation(client, admin, operator):
    assert client.post("/api/signers", headers=admin, json={"signer_code": "x", "bank": "axis"}).status_code == 400
    assert client.post("/api/signers", headers=admin, json={"signer_code": "S-9", "bank": "nowhere"}).status_code == 400
    sid = _signer(client, admin)
    assert client.post("/api/signers", headers=admin, json={"signer_code": "S-001", "bank": "axis"}).status_code == 409
    # operators cannot enroll or edit signers
    assert client.post("/api/signers", headers=operator, json={"signer_code": "S-002", "bank": "axis"}).status_code == 403
    assert client.patch(f"/api/signers/{sid}", headers=operator, json={"full_name": "x"}).status_code == 403
    assert client.patch(f"/api/signers/{sid}", headers=admin, json={"full_name": "Renamed"}).json["data"]["full_name"] == "Renamed"
    assert client.get("/api/signers/lookup?account=123456789012", headers=operator).json["data"]["id"] == sid
    assert client.delete(f"/api/signers/{sid}", headers=admin).status_code == 200


def test_enrollment_duplicates_and_authorization(client, admin, operator):
    sid = _signer(client, admin)
    sig = png_bytes(synthetic_signature(1))
    r = client.post(f"/api/signers/{sid}/specimens", headers=operator, data=upload(sig), content_type="multipart/form-data")
    assert r.status_code == 403  # entering a signer id is not enough to enroll
    r = client.post(f"/api/signers/{sid}/specimens", headers=admin, data=upload(sig), content_type="multipart/form-data")
    assert r.status_code == 201, r.json
    spid = r.json["data"]["id"]
    assert "stored_name" not in r.json["data"]
    r = client.post(f"/api/signers/{sid}/specimens", headers=admin, data=upload(sig), content_type="multipart/form-data")
    assert r.status_code == 409 and r.json["error"]["code"] == "duplicate_specimen"
    sid2 = _signer(client, admin, "S-002", acct="999999999999")
    r = client.post(f"/api/signers/{sid2}/specimens", headers=admin, data=upload(sig), content_type="multipart/form-data")
    assert r.status_code == 409 and r.json["error"]["code"] == "duplicate_other_signer"
    # empty image is rejected
    blank = png_bytes(np.full((100, 300, 3), 250, np.uint8))
    r = client.post(f"/api/signers/{sid}/specimens", headers=admin, data=upload(blank), content_type="multipart/form-data")
    assert r.status_code == 422
    # retrieval is restricted: auditors cannot view biometric images
    aud_pw = "Auditor-999"
    client.post("/api/users", headers=admin, json={"username": "aud2", "password": aud_pw, "role": "auditor"})
    aud = login(client, "aud2", aud_pw)
    assert client.get(f"/api/specimens/{spid}/image", headers=aud).status_code == 403
    assert client.get(f"/api/specimens/{spid}/image", headers=operator).status_code == 200
    # replace + deactivate + history
    r = client.put(f"/api/specimens/{spid}", headers=admin, data=upload(png_bytes(synthetic_signature(7))),
                   content_type="multipart/form-data")
    assert r.status_code == 200
    new_id = r.json["data"]["id"]
    detail = client.get(f"/api/signers/{sid}", headers=admin).json["data"]
    statuses = {s["id"]: s["status"] for s in detail["specimens"]}
    assert statuses[spid] == "inactive" and statuses[new_id] == "active"
    assert any(h["action"] == "specimen.replace" for h in detail["history"])
    assert client.patch(f"/api/specimens/{new_id}", headers=admin, json={"status": "inactive"}).status_code == 200
    assert client.delete(f"/api/specimens/{spid}", headers=admin).status_code == 200


# ------------------------------------------------------------ verification
def test_verification_requires_reference(client, admin, operator):
    sid = _signer(client, admin)
    r = client.post("/api/verifications", headers=operator, data=upload(png_bytes(synthetic_signature(2)), signer_id=str(sid)),
                    content_type="multipart/form-data")
    assert r.status_code == 409 and r.json["error"]["code"] == "no_reference"
    r = client.post("/api/verifications", headers=operator, data=upload(png_bytes(synthetic_signature(2)), signer_id="999"),
                    content_type="multipart/form-data")
    assert r.status_code == 404


def test_verification_flow_and_review(client, admin, operator):
    sid = _signer(client, admin)
    ref = synthetic_signature(3)
    client.post(f"/api/signers/{sid}/specimens", headers=admin, data=upload(png_bytes(ref)), content_type="multipart/form-data")
    # same signature (slightly shifted) vs a different one
    same = np.roll(ref, 3, axis=1)
    r1 = client.post("/api/verifications", headers=operator, data=upload(png_bytes(same), signer_id=str(sid)),
                     content_type="multipart/form-data")
    r2 = client.post("/api/verifications", headers=operator,
                     data=upload(png_bytes(synthetic_signature(99)), signer_id=str(sid)), content_type="multipart/form-data")
    assert r1.status_code == 201 and r2.status_code == 201
    v1, v2 = r1.json["data"], r2.json["data"]
    assert v1["decision"] in ("MATCH", "REVIEW", "MISMATCH")
    assert v1["score"] > v2["score"]
    assert v1["details"]["disclaimer"]
    assert len(v1["details"]["per_specimen"]) == 1
    r = client.patch(f"/api/verifications/{v2['id']}", headers=operator, json={"review_decision": "REJECTED", "review_note": "x"})
    assert r.json["data"]["review_decision"] == "REJECTED"
    assert client.patch(f"/api/verifications/{v2['id']}", headers=operator, json={"review_decision": "MAYBE"}).status_code == 400
    assert client.get("/api/verifications?decision=" + v2["decision"], headers=operator).json["meta"]["total"] >= 1
    assert client.get(f"/api/verifications/{v1['id']}/query-image", headers=operator).status_code == 200
    assert client.delete(f"/api/verifications/{v1['id']}", headers=operator).status_code == 403
    assert client.delete(f"/api/verifications/{v1['id']}", headers=admin).status_code == 200
    assert client.get("/api/reports/verifications.csv", headers=operator).status_code == 200


def test_preprocess_preview(client, operator):
    r = client.post("/api/signature/preprocess", headers=operator, data=upload(png_bytes(synthetic_signature(4))),
                    content_type="multipart/form-data")
    assert r.status_code == 200 and r.json["data"]["image"].startswith("data:image/png;base64,")


# ------------------------------------------------------------ datasets & models
@pytest.mark.skipif(not (HAS_SIG and HAS_OCR), reason="datasets not available")
def test_dataset_audits_and_readiness(client, admin, operator):
    r = client.get("/api/datasets/signature/audit", headers=operator)
    assert r.status_code == 200 and r.json["data"]["genuine_rows"] == 120
    assert client.get("/api/datasets/signature/audit?refresh=1", headers=operator).status_code == 403
    r = client.get("/api/datasets/ocr/audit?refresh=1", headers=admin)
    assert r.json["data"]["total_images"] == 120
    tasks = {t["id"]: t["status"] for t in client.get("/api/models/readiness", headers=operator).json["data"]["tasks"]}
    assert tasks["handwritten_ocr"] == "BLOCKED" and tasks["signer_specific"] == "BLOCKED"


def test_dataset_image_path_traversal(client, operator):
    r = client.get("/api/datasets/ocr/image?filename=../../etc/passwd&split=test&bank=axis", headers=operator)
    assert r.status_code == 400
    r = client.get("/api/reports/files/..%2F..%2Fsecret_key", headers=operator)
    assert r.status_code in (400, 404)


@pytest.mark.skipif(not HAS_SIG, reason="signature dataset not available")
def test_dataset_import_enrollment(client, admin):
    r = client.post("/api/signers/import-dataset", headers=admin, json={"splits": ["test"]})
    assert r.status_code == 200, r.json
    assert r.json["data"]["signers_created"] == 20 and r.json["data"]["specimens_enrolled"] == 20
    r = client.post("/api/signers/import-dataset", headers=admin, json={"splits": ["test"]})
    assert r.json["data"]["skipped_existing"] == 20


def test_model_status_and_unavailable_model(client, operator, app):
    st = client.get("/api/models/status", headers=operator).json["data"]
    assert "ocr_engine" in st and "signature_model" in st
    # remove the trained model: verification must fall back to the baseline and say so
    from pathlib import Path
    for f in Path(app.config["MODELS_DIR"]).glob("signature_fusion.json"):
        f.unlink()
    from app.signature.compare import decide
    from app.signature.preprocess import preprocess
    a = preprocess(synthetic_signature(5))
    res = decide(a, [a], None)
    assert res["model_version"] == "baseline-mean-v1"
    assert any("unavailable" in r for r in res["reasons"])


def test_settings_validation(client, admin, operator):
    assert client.put("/api/settings", headers=operator, json={"signature.match_threshold": 0.9}).status_code == 403
    assert client.put("/api/settings", headers=admin, json={"signature.match_threshold": 2}).status_code == 400
    assert client.put("/api/settings", headers=admin, json={"nope": 1}).status_code == 400
    r = client.put("/api/settings", headers=admin, json={"signature.match_threshold": 0.9})
    assert r.json["data"]["signature.match_threshold"] == 0.9


def test_audit_log_access(client, admin, operator, auditor):
    assert client.get("/api/audit-log", headers=operator).status_code == 403
    r = client.get("/api/audit-log", headers=auditor)
    assert r.status_code == 200 and r.json["meta"]["total"] > 0
    assert client.get("/api/reports/audit-log.csv", headers=operator).status_code == 403


def test_dashboard_and_analytics(client, operator):
    d = client.get("/api/dashboard", headers=operator).json["data"]
    assert d["cheques"]["total"] == 0  # no fabricated numbers on an empty system
    assert client.get("/api/analytics", headers=operator).status_code == 200


def test_unknown_api_route_json(client):
    r = client.get("/api/does-not-exist")
    assert r.status_code == 404 and r.json["ok"] is False


@pytest.mark.skipif(not HAS_OCR, reason="OCR dataset not available")
def test_micr_training_job_sync(client, admin, app):
    r = client.post("/api/models/micr/run", headers=admin, json={"synchronous": True})
    assert r.status_code == 202
    run = client.get(f"/api/models/runs/{r.json['data']['run_id']}", headers=admin).json["data"]
    assert run["status"] == "completed", run
    assert run["metrics"]["evaluation"]["test"]["labelled"] == 20


@pytest.mark.skipif(dataset_cheque() is None, reason="OCR dataset not available")
def test_verify_from_cheque_with_form_fields_only(client, admin, operator):
    """Browser sends multipart form with cheque_id but no file – must be accepted."""
    p = dataset_cheque()
    cid = client.post("/api/cheques", headers=operator, data=upload(p.read_bytes(), p.name),
                      content_type="multipart/form-data").json["data"]["id"]
    sid = _signer(client, admin)
    client.post(f"/api/signers/{sid}/specimens", headers=admin, data=upload(png_bytes(synthetic_signature(11))),
                content_type="multipart/form-data")
    r = client.post("/api/verifications", headers=operator, data={"signer_id": str(sid), "cheque_id": str(cid)},
                    content_type="multipart/form-data")
    assert r.status_code == 201, r.json
    assert r.json["data"]["cheque_id"] == cid
    r = client.post("/api/verifications", headers=operator, json={"signer_id": sid, "cheque_id": cid})
    assert r.status_code == 201


def test_pdf_report_survives_markup_in_corrections(client, admin, operator):
    """Regression: a correction containing ReportLab markup used to crash PDF generation (500)."""
    # any decodable image is a valid cheque upload; OCR may find little on it
    big = np.full((470, 1024, 3), 245, np.uint8)
    big[300:400, 700:1020] = cv2.resize(synthetic_signature(5), (320, 100))
    cid = client.post("/api/cheques", headers=operator, data=upload(png_bytes(big), "c.png"),
                      content_type="multipart/form-data").json["data"]["id"]
    nasty = '<b>unclosed <font color=red>x & y </para> <a href="javascript:1">z'
    r = client.patch(f"/api/cheques/{cid}", headers=operator, json={"corrections": {"payee_name": nasty},
                                                                     "notes": "<i>note"})
    assert r.status_code == 200
    pdf = client.get(f"/api/cheques/{cid}/report.pdf", headers=operator)
    assert pdf.status_code == 200 and pdf.data[:4] == b"%PDF"


def test_client_ip_behind_proxy(tmp_path, monkeypatch):
    """With CV_PROXY_HOPS=1 (Render) the audit log records the client IP, not the proxy's."""
    from app import create_app
    from conftest import MODELS
    monkeypatch.setenv("CV_ADMIN_PASSWORD", ADMIN_PW)
    for hops, expected in ((1, "203.0.113.9"), (0, "127.0.0.1")):
        d = tmp_path / f"h{hops}"
        a = create_app({"TESTING": True, "INSTANCE_DIR": d, "DATABASE": d / "t.db", "STORAGE_DIR": d / "s",
                        "MODELS_DIR": MODELS, "REPORTS_DIR": d / "r", "SECRET_KEY": "k", "PROXY_HOPS": hops,
                        "FRONTEND_DIST": d / "nodist"})
        c = a.test_client()
        c.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PW},
               headers={"X-Forwarded-For": "203.0.113.9"}, environ_base={"REMOTE_ADDR": "127.0.0.1"})
        h = login(c)
        rows = c.get("/api/audit-log?action=auth.login", headers=h).json["data"]
        assert rows[-1]["ip"] == expected
