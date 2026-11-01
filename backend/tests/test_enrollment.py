"""Signature enrollment workflow: signing sessions, independent-specimen readiness, same-signing refusal,
batch upload and the cap on MATCH decisions for reference sets that are not ready."""
import datetime as dt
import io
import sqlite3

import cv2
import numpy as np
import pytest

from app import db as dbmod
from app.services import enrollment
from conftest import HAS_SIG, login, png_bytes, synthetic_signature, upload

POLICY = {"min_specimens": 5, "min_sessions": 2, "cap_match_when_not_ready": True}


# ---------------------------------------------------------------- helpers
def new_signer(client, admin, code="S-001", bank="axis"):
    r = client.post("/api/signers", headers=admin, json={"signer_code": code, "bank": bank})
    assert r.status_code == 201, r.json
    return r.json["data"]["id"]


def new_session(client, admin, sid, date="2026-10-01", **extra):
    r = client.post(f"/api/signers/{sid}/sessions", headers=admin, json={"session_date": date, **extra})
    assert r.status_code == 201, r.json
    return r.json["data"]["id"]


def enroll(client, admin, sid, seed, session_id=None, **extra):
    form = {k: str(v) for k, v in extra.items()}
    if session_id is not None:
        form["session_id"] = str(session_id)
    return client.post(f"/api/signers/{sid}/specimens", headers=admin,
                       data=upload(png_bytes(synthetic_signature(seed)), **form), content_type="multipart/form-data")


def readiness(client, headers, sid):
    r = client.get(f"/api/signers/{sid}/enrollment", headers=headers)
    assert r.status_code == 200, r.json
    return r.json["data"]


def rescan(img: np.ndarray) -> np.ndarray:
    """A second capture of the same signing: other resolution, margins, noise and JPEG compression."""
    return _capture(img, 0.0)


def crooked(img: np.ndarray, degrees: float = 3.0) -> np.ndarray:
    """Like rescan() but the paper lay a few degrees crooked on the scanner. The perceptual hash no longer
    recognises it (distance 12-32 of 64 bits) while the aligned ink still agrees by more than 98 %."""
    return _capture(img, degrees)


def _capture(img: np.ndarray, degrees: float) -> np.ndarray:
    big = cv2.resize(img, None, fx=1.3, fy=1.3, interpolation=cv2.INTER_CUBIC)
    if degrees:
        h, w = big.shape[:2]
        big = cv2.warpAffine(big, cv2.getRotationMatrix2D((w / 2, h / 2), degrees, 1.0), (w, h),
                             flags=cv2.INTER_CUBIC, borderValue=(245, 245, 245))
    big = cv2.copyMakeBorder(big, 12, 9, 17, 5, cv2.BORDER_CONSTANT, value=(245, 245, 245))
    noise = np.random.default_rng(0).normal(0, 4, big.shape)
    noisy = np.clip(big.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    return cv2.imdecode(cv2.imencode(".jpg", noisy, [cv2.IMWRITE_JPEG_QUALITY, 70])[1], 1)


def ready_signer(client, admin, code="S-READY"):
    """Five independent specimens from two signing days."""
    sid = new_signer(client, admin, code)
    s1, s2 = new_session(client, admin, sid, "2026-09-12"), new_session(client, admin, sid, "2026-10-03")
    for seed, sess in ((11, s1), (12, s1), (13, s1), (14, s2), (15, s2)):
        assert enroll(client, admin, sid, seed, sess).status_code == 201
    return sid


# ---------------------------------------------------------------- schema
def test_migration_upgrades_an_older_database_and_is_idempotent(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE users(id INTEGER PRIMARY KEY);
        CREATE TABLE signers(id INTEGER PRIMARY KEY, signer_code TEXT, source TEXT NOT NULL DEFAULT '');
        CREATE TABLE specimens(id INTEGER PRIMARY KEY, signer_id INTEGER, status TEXT);
        INSERT INTO signers(id, signer_code, source) VALUES (1,'A','dataset:train'),(2,'B','');
        INSERT INTO specimens(id, signer_id, status) VALUES (1,1,'active'),(2,2,'active');
        CREATE TABLE enrollment_sessions(id INTEGER PRIMARY KEY);
    """)
    conn.commit()
    conn.close()
    conn = dbmod.connect(str(path))
    assert dbmod.migrate(conn) == ["specimens.session_id", "specimens.source_ref", "specimens.origin",
                                   "users.email", "users.pending_approval"]     # the last two: sign-in by email
    conn.commit()
    assert dbmod.migrate(conn) == []
    rows = {r["id"]: r for r in conn.execute("SELECT * FROM specimens")}
    assert rows[1]["origin"] == "dataset_import" and rows[2]["origin"] == "upload"
    assert rows[1]["session_id"] is None and rows[1]["source_ref"] == ""
    conn.close()


def test_new_installs_get_the_enrollment_defaults(client, admin):
    s = client.get("/api/settings", headers=admin).json["data"]
    assert s["enrollment.min_specimens"] == 5 and s["enrollment.min_sessions"] == 2
    assert s["enrollment.cap_match_when_not_ready"] is True
    assert s["signature.max_specimens_per_signer"] == 20


@pytest.mark.parametrize("key,value", [
    ("enrollment.min_specimens", 0), ("enrollment.min_sessions", 0), ("enrollment.min_specimens", True),
    ("enrollment.min_sessions", "2"), ("enrollment.min_specimens", 5000),
    ("enrollment.cap_match_when_not_ready", "yes"), ("enrollment.cap_match_when_not_ready", 1),
])
def test_invalid_enrollment_settings_are_refused(client, admin, key, value):
    assert client.put("/api/settings", headers=admin, json={key: value}).status_code == 400


def test_enrollment_settings_can_be_changed_by_admin_only(client, admin, operator):
    assert client.put("/api/settings", headers=operator, json={"enrollment.min_specimens": 3}).status_code == 403
    r = client.put("/api/settings", headers=admin, json={"enrollment.min_specimens": 3, "enrollment.min_sessions": 3,
                                                         "enrollment.cap_match_when_not_ready": False})
    assert r.status_code == 200 and r.json["data"]["enrollment.min_sessions"] == 3


# ---------------------------------------------------------------- the independence rule (pure function)
def row(i, session=None, date=None, ref=""):
    return {"id": i, "session_id": session, "session_date": date, "source_ref": ref}


def test_specimens_without_a_session_do_not_count():
    rep = enrollment.assess([row(i) for i in range(1, 9)], POLICY)
    assert not rep["ready"] and rep["status"] == "incomplete"
    assert rep["active_specimens"] == 8 and rep["independent_samples"] == 0 and rep["unsessioned_specimens"] == 8
    assert "more independent specimens" in rep["missing"][0] and "8 active specimens" in rep["notes"][0]


def test_no_specimens_is_status_none():
    assert enrollment.assess([], POLICY)["status"] == "none"


def test_five_specimens_from_one_day_are_not_enough():
    rep = enrollment.assess([row(i, 1, "2026-10-01") for i in range(1, 6)], POLICY)
    assert rep["independent_samples"] == 5 and rep["independent_days"] == 1 and not rep["ready"]
    assert rep["missing"] == ["specimens from 1 more signing day"]


def test_two_sessions_on_the_same_date_are_one_day():
    rows = [row(1, 1, "2026-10-01"), row(2, 1, "2026-10-01"), row(3, 2, "2026-10-01"), row(4, 2, "2026-10-01"),
            row(5, 2, "2026-10-01")]
    assert enrollment.assess(rows, POLICY)["independent_days"] == 1


def test_enough_specimens_over_enough_days_is_ready():
    rows = [row(1, 1, "2026-09-01"), row(2, 1, "2026-09-01"), row(3, 1, "2026-09-01"),
            row(4, 2, "2026-10-01"), row(5, 2, "2026-10-01")]
    rep = enrollment.assess(rows, POLICY)
    assert rep["ready"] and rep["status"] == "ready" and rep["missing"] == [] and rep["session_dates"] == [
        "2026-09-01", "2026-10-01"]


def test_specimens_from_the_same_source_count_once():
    rows = [row(1, 1, "2026-09-01", "Cheque 000123"), row(2, 1, "2026-09-01", "  cheque   000123 "),
            row(3, 1, "2026-09-01"), row(4, 2, "2026-10-01"), row(5, 2, "2026-10-01")]
    rep = enrollment.assess(rows, POLICY)
    assert rep["active_specimens"] == 5 and rep["independent_samples"] == 4 and rep["merged_specimens"] == 1
    assert not rep["ready"] and rep["missing"] == ["1 more independent specimen"]
    assert "count once" in rep["notes"][0]


def test_a_source_spanning_two_days_gives_only_one_day():
    rows = [row(1, 1, "2026-09-01", "X"), row(2, 2, "2026-10-01", "X")]
    rep = enrollment.assess(rows, {**POLICY, "min_specimens": 1})
    assert rep["independent_samples"] == 1 and rep["independent_days"] == 1 and rep["session_dates"] == ["2026-09-01"]


def test_the_policy_is_configurable():
    rows = [row(1, 1, "2026-09-01"), row(2, 2, "2026-10-01")]
    assert not enrollment.assess(rows, POLICY)["ready"]
    assert enrollment.assess(rows, {**POLICY, "min_specimens": 2, "min_sessions": 2})["ready"]


# ---------------------------------------------------------------- sessions API
@pytest.mark.parametrize("body", [
    {}, {"session_date": ""}, {"session_date": "01/10/2026"}, {"session_date": "20261001"}, {"session_date": "2026-13-01"},
    {"session_date": "2026-02-30"}, {"session_date": "1989-12-31"}, {"session_date": "2999-01-01"},
    {"session_date": "2026-10-01", "method": "fax"}, {"session_date": "2026-10-01", "notes": "x" * 501},
    {"session_date": "2026-10-01", "consent_ref": "x" * 81},
])
def test_session_validation(client, admin, body):
    sid = new_signer(client, admin)
    r = client.post(f"/api/signers/{sid}/sessions", headers=admin, json=body)
    assert r.status_code == 400 and r.json["error"]["code"] == "validation_error", r.json


def test_session_defaults_and_fields(client, admin):
    sid = new_signer(client, admin)
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    r = client.post(f"/api/signers/{sid}/sessions", headers=admin, json={
        "session_date": today, "method": "tablet", "instrument": "Wacom One", "consent_ref": "CF-2026-014", "notes": "morning"})
    assert r.status_code == 201
    d = r.json["data"]
    assert (d["session_date"], d["method"], d["consent_ref"], d["specimens_active"]) == (today, "tablet", "CF-2026-014", 0)
    assert client.post(f"/api/signers/{sid}/sessions", headers=admin,
                       json={"session_date": "2026-10-01"}).json["data"]["method"] == "paper_scan"


def test_only_admins_write_sessions_and_everybody_signed_in_reads(client, admin, operator, auditor):
    sid = new_signer(client, admin)
    for who in (operator, auditor):
        assert client.post(f"/api/signers/{sid}/sessions", headers=who, json={"session_date": "2026-10-01"}).status_code == 403
        assert client.get(f"/api/signers/{sid}/enrollment", headers=who).status_code == 200
        assert client.get("/api/enrollment/overview", headers=who).status_code == 200
    assert client.get(f"/api/signers/{sid}/enrollment").status_code == 401
    assert client.post("/api/signers/999/sessions", headers=admin, json={"session_date": "2026-10-01"}).status_code == 404


def test_update_and_delete_sessions(client, admin):
    sid = new_signer(client, admin)
    s = new_session(client, admin, sid)
    r = client.patch(f"/api/sessions/{s}", headers=admin, json={"session_date": "2026-09-30", "notes": "moved"})
    assert r.status_code == 200 and r.json["data"]["session_date"] == "2026-09-30"
    assert client.patch(f"/api/sessions/{s}", headers=admin, json={}).status_code == 400
    assert client.patch(f"/api/sessions/{s}", headers=admin, json={"session_date": "tomorrow"}).status_code == 400
    assert client.patch("/api/sessions/999", headers=admin, json={"notes": "x"}).status_code == 404
    spid = enroll(client, admin, sid, 21, s).json["data"]["id"]
    r = client.delete(f"/api/sessions/{s}", headers=admin)
    assert r.status_code == 409 and r.json["error"]["code"] == "session_has_specimens"
    assert client.delete(f"/api/specimens/{spid}", headers=admin).status_code == 200
    assert client.delete(f"/api/sessions/{s}", headers=admin).status_code == 200
    assert readiness(client, admin, sid)["sessions"] == []


def test_session_audit_entries_do_not_copy_consent_reference_or_notes(client, admin):
    sid = new_signer(client, admin)
    new_session(client, admin, sid, consent_ref="CONSENT-SECRET-77", notes="free text about the signer")
    log = client.get("/api/audit-log?action=session&per_page=50", headers=admin).json["data"]
    assert log and all("CONSENT-SECRET-77" not in str(e) and "free text" not in str(e) for e in log)


def test_session_actions_appear_in_the_signers_history(client, admin):
    sid = new_signer(client, admin)
    s = new_session(client, admin, sid, "2026-09-01")
    client.patch(f"/api/sessions/{s}", headers=admin, json={"notes": "x"})
    client.delete(f"/api/sessions/{s}", headers=admin)
    actions = [h["action"] for h in client.get(f"/api/signers/{sid}", headers=admin).json["data"]["history"]]
    assert {"session.create", "session.update", "session.delete"} <= set(actions)


# ---------------------------------------------------------------- specimens and sessions
def test_specimen_can_be_enrolled_without_a_session_but_does_not_count(client, admin):
    sid = new_signer(client, admin)
    r = enroll(client, admin, sid, 31)
    assert r.status_code == 201 and r.json["data"]["session_id"] is None and r.json["data"]["origin"] == "upload"
    rep = readiness(client, admin, sid)
    assert rep["status"] == "incomplete" and rep["active_specimens"] == 1 and rep["independent_samples"] == 0


def test_specimen_keeps_session_and_source(client, admin):
    sid = new_signer(client, admin)
    s = new_session(client, admin, sid)
    r = enroll(client, admin, sid, 32, s, source_ref="  Sheet 4,   row 2 ")
    d = r.json["data"]
    assert d["session_id"] == s and d["source_ref"] == "Sheet 4, row 2"
    detail = client.get(f"/api/signers/{sid}", headers=admin).json["data"]
    assert detail["sessions"][0]["specimens_active"] == 1 and detail["enrollment"]["independent_samples"] == 1


def test_a_session_of_another_signer_cannot_be_used(client, admin):
    a, b = new_signer(client, admin, "S-A"), new_signer(client, admin, "S-B")
    sess_b = new_session(client, admin, b)
    r = enroll(client, admin, a, 33, sess_b)
    assert r.status_code == 400 and "different signer" in r.json["error"]["message"]
    assert enroll(client, admin, a, 33, 9999).status_code == 404
    assert enroll(client, admin, a, 33, "abc").status_code == 400
    ok = enroll(client, admin, a, 34, new_session(client, admin, a)).json["data"]["id"]
    assert client.patch(f"/api/specimens/{ok}", headers=admin, json={"session_id": sess_b}).status_code == 400
    assert client.patch(f"/api/specimens/{ok}", headers=admin, json={"session_id": "1"}).status_code == 400


def test_existing_specimens_can_be_linked_to_the_session_they_were_written_in(client, admin, operator):
    sid = new_signer(client, admin)
    ids = [enroll(client, admin, sid, 40 + i).json["data"]["id"] for i in range(5)]
    assert readiness(client, admin, sid)["independent_samples"] == 0
    s1, s2 = new_session(client, admin, sid, "2026-08-01"), new_session(client, admin, sid, "2026-09-01")
    for n, spid in enumerate(ids):
        r = client.patch(f"/api/specimens/{spid}", headers=admin, json={"session_id": s1 if n < 3 else s2})
        assert r.status_code == 200 and r.json["data"]["session_id"] in (s1, s2)
    assert readiness(client, admin, sid)["ready"]
    assert client.patch(f"/api/specimens/{ids[0]}", headers=operator, json={"session_id": s1}).status_code == 403
    assert client.patch(f"/api/specimens/{ids[0]}", headers=admin, json={}).status_code == 400
    # unlinking removes the specimen from the independent count again
    client.patch(f"/api/specimens/{ids[0]}", headers=admin, json={"session_id": None})
    assert readiness(client, admin, sid)["independent_samples"] == 4
    log = client.get("/api/audit-log?action=specimen.update", headers=admin).json["data"]
    assert any('"session_id"' in e["detail"] for e in log)


def test_status_only_patch_still_works(client, admin):
    sid = new_signer(client, admin)
    spid = enroll(client, admin, sid, 50, new_session(client, admin, sid)).json["data"]["id"]
    assert client.patch(f"/api/specimens/{spid}", headers=admin, json={"status": "inactive"}).json["data"]["status"] == "inactive"
    assert readiness(client, admin, sid)["active_specimens"] == 0
    assert client.patch(f"/api/specimens/{spid}", headers=admin, json={"status": "active"}).status_code == 200
    assert client.patch(f"/api/specimens/{spid}", headers=admin, json={"status": "paused"}).status_code == 400


def test_replacing_a_specimen_keeps_its_session_and_source(client, admin):
    sid = new_signer(client, admin)
    s = new_session(client, admin, sid)
    old = enroll(client, admin, sid, 51, s, source_ref="Form 9").json["data"]["id"]
    r = client.put(f"/api/specimens/{old}", headers=admin, data=upload(png_bytes(synthetic_signature(52))),
                   content_type="multipart/form-data")
    assert r.status_code == 200
    assert r.json["data"]["session_id"] == s and r.json["data"]["source_ref"] == "Form 9"
    assert readiness(client, admin, sid)["independent_samples"] == 1       # the replaced one is inactive


def test_inactive_specimens_do_not_count(client, admin):
    sid = ready_signer(client, admin)
    assert readiness(client, admin, sid)["ready"]
    first = client.get(f"/api/signers/{sid}", headers=admin).json["data"]["specimens"][-1]["id"]
    client.patch(f"/api/specimens/{first}", headers=admin, json={"status": "inactive"})
    rep = readiness(client, admin, sid)
    assert not rep["ready"] and rep["independent_samples"] == 4 and rep["missing"] == ["1 more independent specimen"]


# ---------------------------------------------------------------- same signing
def test_a_second_capture_of_an_enrolled_signature_is_refused(client, admin):
    sid = new_signer(client, admin)
    s = new_session(client, admin, sid)
    base = synthetic_signature(60)
    assert client.post(f"/api/signers/{sid}/specimens", headers=admin, data=upload(png_bytes(base), session_id=str(s)),
                       content_type="multipart/form-data").status_code == 201
    r = client.post(f"/api/signers/{sid}/specimens", headers=admin,
                    data=upload(png_bytes(crooked(base)), session_id=str(s)), content_type="multipart/form-data")
    assert r.status_code == 409 and r.json["error"]["code"] == "same_signing", r.json
    assert r.json["error"]["details"]["agreement"] >= 0.98 and "specimen #" in r.json["error"]["message"]
    assert readiness(client, admin, sid)["active_specimens"] == 1


def test_a_plain_rescan_is_caught_by_the_older_hash_rule(client, admin):
    sid = new_signer(client, admin)
    base = synthetic_signature(64)
    client.post(f"/api/signers/{sid}/specimens", headers=admin, data=upload(png_bytes(base)),
                content_type="multipart/form-data")
    r = client.post(f"/api/signers/{sid}/specimens", headers=admin, data=upload(png_bytes(rescan(base))),
                    content_type="multipart/form-data")
    assert r.status_code == 409 and r.json["error"]["code"] == "duplicate_specimen"


def test_different_signatures_are_never_taken_for_the_same_signing(client, admin):
    sid = new_signer(client, admin)
    s = new_session(client, admin, sid)
    for seed in range(100, 112):
        r = enroll(client, admin, sid, seed, s)
        assert r.status_code == 201, (seed, r.json)
    assert readiness(client, admin, sid)["active_specimens"] == 12


def test_the_same_signing_is_only_compared_with_this_signers_specimens(client, admin):
    a, b = new_signer(client, admin, "S-A"), new_signer(client, admin, "S-B")
    base = synthetic_signature(61)
    assert enroll(client, admin, a, 61).status_code == 201
    r = client.post(f"/api/signers/{b}/specimens", headers=admin, data=upload(png_bytes(crooked(base))),
                    content_type="multipart/form-data")
    assert r.status_code == 201                  # another signer's file is not a reference for this one


def test_a_deactivated_duplicate_cannot_be_reactivated(client, admin):
    sid = new_signer(client, admin)
    base = synthetic_signature(62)
    first = client.post(f"/api/signers/{sid}/specimens", headers=admin, data=upload(png_bytes(base)),
                        content_type="multipart/form-data").json["data"]["id"]
    client.patch(f"/api/specimens/{first}", headers=admin, json={"status": "inactive"})
    again = client.post(f"/api/signers/{sid}/specimens", headers=admin, data=upload(png_bytes(crooked(base))),
                        content_type="multipart/form-data")
    assert again.status_code == 201                      # the first capture is inactive, so this is not a second one
    r = client.patch(f"/api/specimens/{first}", headers=admin, json={"status": "active"})
    assert r.status_code == 409 and r.json["error"]["code"] == "same_signing"


def test_replacing_with_a_rescan_is_allowed(client, admin):
    sid = new_signer(client, admin)
    base = synthetic_signature(63)
    old = client.post(f"/api/signers/{sid}/specimens", headers=admin, data=upload(png_bytes(base)),
                      content_type="multipart/form-data").json["data"]["id"]
    r = client.put(f"/api/specimens/{old}", headers=admin, data=upload(png_bytes(crooked(base))),
                   content_type="multipart/form-data")
    assert r.status_code == 200 and r.json["data"]["status"] == "active"


# ---------------------------------------------------------------- batch upload
def batch(client, admin, sid, seeds, session_id, extra=None, files=None):
    data = {"files": files if files is not None else [(io.BytesIO(png_bytes(synthetic_signature(s))), f"sig{s}.png")
                                                     for s in seeds]}
    if session_id is not None:
        data["session_id"] = str(session_id)
    data.update(extra or {})
    return client.post(f"/api/signers/{sid}/specimens/batch", headers=admin, data=data, content_type="multipart/form-data")


def test_batch_enrolls_a_whole_session_and_reports_each_file(client, admin):
    sid = new_signer(client, admin)
    s = new_session(client, admin, sid)
    base = synthetic_signature(70)
    files = [(io.BytesIO(png_bytes(base)), "a.png"),
             (io.BytesIO(png_bytes(synthetic_signature(71))), "b.png"),
             (io.BytesIO(png_bytes(crooked(base))), "a_again.png"),        # same signing as a.png
             (io.BytesIO(png_bytes(synthetic_signature(71))), "b_copy.png"),   # byte-identical to b.png
             (io.BytesIO(b"not an image"), "c.png"),
             (io.BytesIO(png_bytes(synthetic_signature(72))), "d.png")]
    r = batch(client, admin, sid, [], s, files=files)
    assert r.status_code == 201, r.json
    d = r.json["data"]
    by_file = {x["file"]: x for x in d["results"]}
    assert d["enrolled"] == 3 and d["rejected"] == 3
    assert by_file["a.png"]["ok"] and by_file["b.png"]["ok"] and by_file["d.png"]["ok"]
    assert by_file["a_again.png"]["error"]["code"] == "same_signing"
    assert by_file["b_copy.png"]["error"]["code"] in ("duplicate_specimen", "same_signing")
    assert by_file["c.png"]["error"]["code"] == "unsupported_type"
    assert d["enrollment"]["independent_samples"] == 3 and d["enrollment"]["independent_days"] == 1
    assert all(x["specimen"]["session_id"] == s for x in d["results"] if x["ok"])


def test_batch_needs_a_session_files_and_a_limit(client, admin, operator, app):
    sid = new_signer(client, admin)
    s = new_session(client, admin, sid)
    r = batch(client, admin, sid, [1], None)
    assert r.status_code == 400 and "session_id is required" in r.json["error"]["message"]
    assert batch(client, admin, sid, [], s, files=[]).status_code == 400
    r = batch(client, admin, sid, list(range(200, 200 + app.config["MAX_BATCH_FILES"] + 1)), s)
    assert r.status_code == 400 and r.json["error"]["code"] == "too_many_files"
    assert batch(client, operator, sid, [1], s).status_code == 403
    other = new_signer(client, admin, "S-OTHER")
    assert batch(client, admin, other, [1], s).status_code == 400            # session belongs to another signer
    assert batch(client, admin, 999, [1], s).status_code == 404
    client.patch(f"/api/signers/{sid}", headers=admin, json={"status": "inactive"})
    assert batch(client, admin, sid, [1], s).status_code == 409


def test_batch_with_only_rejected_files_returns_200_and_keeps_going_after_the_limit(client, admin):
    sid = new_signer(client, admin)
    s = new_session(client, admin, sid)
    client.put("/api/settings", headers=admin, json={"signature.max_specimens_per_signer": 2})
    r = batch(client, admin, sid, [301, 302, 303], s)
    assert r.status_code == 201 and r.json["data"]["enrolled"] == 2
    codes = [x["error"]["code"] for x in r.json["data"]["results"] if not x["ok"]]
    assert codes == ["too_many_specimens"]
    r = batch(client, admin, sid, [304], s)
    assert r.status_code == 200 and r.json["data"]["enrolled"] == 0


def test_batch_is_audited_without_file_names(client, admin):
    sid = new_signer(client, admin)
    s = new_session(client, admin, sid)
    batch(client, admin, sid, [401, 402], s)
    entry = client.get("/api/audit-log?action=specimen.batch_enroll", headers=admin).json["data"][0]
    assert '"enrolled": 2' in entry["detail"] and "sig401" not in entry["detail"]


# ---------------------------------------------------------------- readiness through the API
def test_readiness_follows_the_enrollment(client, admin):
    sid = new_signer(client, admin)
    assert readiness(client, admin, sid)["status"] == "none"
    s1 = new_session(client, admin, sid, "2026-09-12")
    for seed in (11, 12, 13, 14, 15):
        enroll(client, admin, sid, seed, s1)
    rep = readiness(client, admin, sid)
    assert rep["status"] == "incomplete" and rep["independent_samples"] == 5 and rep["independent_days"] == 1
    assert rep["missing"] == ["specimens from 1 more signing day"] and rep["consent_recorded"] is False
    s2 = new_session(client, admin, sid, "2026-10-03", consent_ref="CF-1")
    enroll(client, admin, sid, 16, s2)
    rep = readiness(client, admin, sid)
    assert rep["ready"] and rep["independent_samples"] == 6 and rep["independent_days"] == 2
    assert rep["consent_recorded"] is False                       # the first session has no consent reference
    client.patch(f"/api/sessions/{s1}", headers=admin, json={"consent_ref": "CF-0"})
    assert readiness(client, admin, sid)["consent_recorded"] is True


def test_signer_list_and_detail_carry_the_reference_status(client, admin, operator):
    ready = ready_signer(client, admin, "S-RDY")
    thin = new_signer(client, admin, "S-THIN")
    enroll(client, admin, thin, 80)
    empty = new_signer(client, admin, "S-NONE")
    rows = {r["signer_code"]: r for r in client.get("/api/signers?per_page=100", headers=operator).json["data"]}
    assert rows["S-RDY"]["reference_status"] == "ready" and rows["S-RDY"]["independent_samples"] == 5
    assert rows["S-THIN"]["reference_status"] == "incomplete" and rows["S-THIN"]["enrollment_status"] == "enrolled"
    assert rows["S-NONE"]["reference_status"] == "none"
    detail = client.get(f"/api/signers/{ready}", headers=operator).json["data"]
    assert detail["enrollment"]["ready"] and len(detail["sessions"]) == 2
    assert client.get(f"/api/signers/{empty}", headers=operator).json["data"]["enrollment"]["status"] == "none"


def test_overview_counts_every_signer(client, admin):
    ready_signer(client, admin, "S-RDY")
    thin = new_signer(client, admin, "S-THIN")
    enroll(client, admin, thin, 81)
    new_signer(client, admin, "S-NONE")
    ov = client.get("/api/enrollment/overview", headers=admin).json["data"]
    assert (ov["signers"], ov["signers_ready"], ov["signers_incomplete"], ov["signers_none"]) == (3, 1, 1, 1)
    assert ov["policy"]["min_specimens"] == 5 and "items" not in ov
    h = client.get("/api/signature/health", headers=admin).json["data"]
    assert h["signers_reference_ready"] == 1


def test_readiness_of_many_signers_is_computed_in_chunks(client, admin, app):
    ids = [new_signer(client, admin, f"S-{n:04d}") for n in range(3)]
    with app.app_context():
        many = enrollment.readiness_for(ids + list(range(1000, 2200)))        # more ids than one statement binds
    assert set(many) == set(ids + list(range(1000, 2200))) and all(many[i]["status"] == "none" for i in ids)


def test_changing_the_policy_changes_readiness(client, admin):
    sid = new_signer(client, admin)
    s = new_session(client, admin, sid)
    enroll(client, admin, sid, 82, s)
    assert not readiness(client, admin, sid)["ready"]
    client.put("/api/settings", headers=admin, json={"enrollment.min_specimens": 1, "enrollment.min_sessions": 1})
    assert readiness(client, admin, sid)["ready"]


# ---------------------------------------------------------------- the cap on MATCH
def fake_result(decision="MATCH"):
    return {"decision": decision, "score": 0.93, "reasons": ["Best similarity 0.93."], "per_specimen": []}


def test_policy_caps_a_match_for_a_reference_set_that_is_not_ready(client, admin, app):
    sid = new_signer(client, admin)
    enroll(client, admin, sid, 90, new_session(client, admin, sid))
    with app.app_context():
        res = enrollment.apply_reference_policy(fake_result("MATCH"), sid)
    assert res["decision"] == "REVIEW" and res["uncapped_decision"] == "MATCH" and res["capped_by_policy"] is True
    assert res["reasons"][0].startswith("Capped from MATCH to REVIEW") and "at least 5 independent" in res["reasons"][0]
    assert res["reference_set"]["ready"] is False and res["reference_set"]["independent_samples"] == 1


def test_policy_leaves_a_ready_reference_set_alone(client, admin, app):
    sid = ready_signer(client, admin)
    with app.app_context():
        res = enrollment.apply_reference_policy(fake_result("MATCH"), sid)
    assert res["decision"] == "MATCH" and "capped_by_policy" not in res and res["reference_set"]["ready"] is True


def test_policy_never_softens_a_mismatch(client, admin, app):
    sid = new_signer(client, admin)
    enroll(client, admin, sid, 91)
    with app.app_context():
        mismatch = enrollment.apply_reference_policy(fake_result("MISMATCH"), sid)
        review = enrollment.apply_reference_policy(fake_result("REVIEW"), sid)
    assert mismatch["decision"] == "MISMATCH" and "may be a genuine signature" in mismatch["reasons"][-1]
    assert review["decision"] == "REVIEW" and "capped_by_policy" not in review


def test_the_cap_can_be_switched_off_but_the_report_stays(client, admin, app):
    sid = new_signer(client, admin)
    enroll(client, admin, sid, 92)
    client.put("/api/settings", headers=admin, json={"enrollment.cap_match_when_not_ready": False})
    with app.app_context():
        res = enrollment.apply_reference_policy(fake_result("MATCH"), sid)
    assert res["decision"] == "MATCH" and res["reference_set"]["ready"] is False
    assert res["reasons"][-1].startswith("Reference set not ready")


def verify_with(client, headers, sid, monkeypatch, decision="MATCH"):
    from app.api import verification_routes as vr
    monkeypatch.setattr(vr, "decide", lambda *a, **k: {
        "decision": decision, "score": 0.97, "thresholds": {}, "model_version": "test", "reasons": ["forced"],
        "per_specimen": [{"index": 0, "score": 0.97, "features": {}}], "best_index": 0})
    return client.post("/api/verifications", headers=headers,
                       data=upload(png_bytes(synthetic_signature(500)), signer_id=str(sid)), content_type="multipart/form-data")


def test_verification_route_applies_the_cap(client, admin, operator, monkeypatch):
    thin = new_signer(client, admin, "S-THIN")
    enroll(client, admin, thin, 93)
    r = verify_with(client, operator, thin, monkeypatch)
    assert r.status_code == 201
    v = r.json["data"]
    assert v["decision"] == "REVIEW" and v["details"]["capped_by_policy"] is True
    assert v["details"]["reference_set"]["independent_samples"] == 0
    assert client.get("/api/audit-log?action=verification.create", headers=admin).json["data"][0]["detail"].count(
        '"capped_by_policy": true') == 1


def test_verification_route_keeps_a_match_for_a_ready_reference_set(client, admin, operator, monkeypatch):
    sid = ready_signer(client, admin)
    v = verify_with(client, operator, sid, monkeypatch).json["data"]
    assert v["decision"] == "MATCH" and v["details"]["reference_set"]["ready"] is True


# ---------------------------------------------------------------- imported dataset
@pytest.mark.skipif(not HAS_SIG, reason="signature dataset not available")
def test_dataset_import_is_marked_and_never_counts_as_independent(client, admin):
    r = client.post("/api/signers/import-dataset", headers=admin, json={"splits": ["train"]})
    assert r.status_code == 200 and r.json["data"]["specimens_enrolled"] > 0
    sid = client.get("/api/signers?per_page=1", headers=admin).json["data"][0]["id"]
    detail = client.get(f"/api/signers/{sid}", headers=admin).json["data"]
    assert detail["specimens"] and all(s["origin"] == "dataset_import" and s["session_id"] is None
                                       for s in detail["specimens"])
    assert detail["enrollment"]["ready"] is False and detail["enrollment"]["independent_samples"] == 0
