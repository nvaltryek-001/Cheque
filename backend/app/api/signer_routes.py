"""/api/signers and /api/specimens – enrollment CRUD (admin-only writes)."""
from __future__ import annotations

import json
import re

import cv2
from flask import Blueprint, current_app, g, request, send_file

from ..db import execute, now_iso, query, row_to_dict
from ..services import audit, storage
from ..services.auth import login_required
from ..services.throttle import rate_limited
from ..services import enrollment
from ..services.datasets import load_signature_manifests
from ..services.enrollment import add_specimen, check_duplicates, process_or_fail
from ..signature.preprocess import perceptual_hash
from .common import ApiError, json_body, ok, pagination, require_fields

bp = Blueprint("signers", __name__)
BANKS = ("axis", "canara", "icici", "syndicate", "sbi", "hdfc", "pnb", "bob", "kotak", "union", "yes", "other")


def _signer(sid: int):
    row = query("SELECT * FROM signers WHERE id=?", (sid,), one=True)
    if row is None:
        raise ApiError(404, "not_found", "Signer not found.")
    return row


def _specimen(spid: int):
    row = query("SELECT * FROM specimens WHERE id=?", (spid,), one=True)
    if row is None:
        raise ApiError(404, "not_found", "Specimen not found.")
    return row


def _signer_out(row, readiness: dict | None = None) -> dict:
    d = dict(row)
    counts = query("SELECT status, COUNT(*) c FROM specimens WHERE signer_id=? GROUP BY status", (row["id"],))
    d["specimens_active"] = next((c["c"] for c in counts if c["status"] == "active"), 0)
    d["specimens_inactive"] = next((c["c"] for c in counts if c["status"] == "inactive"), 0)
    d["enrollment_status"] = "enrolled" if d["specimens_active"] else "no_specimens"
    # reference_status: none | incomplete | ready – "enrolled" only says that a file exists, not that the
    # reference set is independent and large enough to trust a MATCH (see services/enrollment.assess)
    rep = readiness or enrollment.readiness_for([row["id"]])[row["id"]]
    d["reference_status"] = rep["status"]
    d["independent_samples"] = rep["independent_samples"]
    d["independent_days"] = rep["independent_days"]
    return d


def _specimen_out(row) -> dict:
    d = row_to_dict(row, ("quality_json",))
    d.pop("stored_name", None)
    d.pop("processed_name", None)
    d.pop("sha256", None)
    d.pop("phash", None)
    return d


def _validate_signer(data: dict, partial=False) -> dict:
    out = {}
    if "signer_code" in data or not partial:
        code = str(data.get("signer_code", "")).strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,40}", code):
            raise ApiError(400, "validation_error", "Signer ID must be 3–40 letters, digits, '_', '-' or '.'.")
        out["signer_code"] = code
    if "bank" in data or not partial:
        bank = str(data.get("bank", "")).strip().lower()
        if bank not in BANKS:
            raise ApiError(400, "validation_error", f"Bank must be one of: {', '.join(BANKS)}.")
        out["bank"] = bank
    if "account_number" in data:
        acct = re.sub(r"\s", "", str(data.get("account_number") or ""))
        if acct and not re.fullmatch(r"\d{6,20}", acct):
            raise ApiError(400, "validation_error", "Account number must be 6–20 digits.")
        out["account_number"] = acct
    for k, n in (("full_name", 120), ("notes", 1000)):
        if k in data:
            out[k] = str(data.get(k) or "").strip()[:n]
    if "status" in data:
        if data["status"] not in ("active", "inactive"):
            raise ApiError(400, "validation_error", "Status must be active or inactive.")
        out["status"] = data["status"]
    return out


@bp.get("/api/signers")
@login_required()
def list_signers():
    page, per_page = pagination()
    where, params = [], []
    if request.args.get("bank"):
        where.append("bank=?"); params.append(request.args["bank"].lower())
    if request.args.get("status"):
        where.append("status=?"); params.append(request.args["status"])
    q = (request.args.get("q") or "").strip()
    if q:
        where.append("(signer_code LIKE ? OR full_name LIKE ? OR account_number LIKE ?)")
        params += [f"%{q}%"] * 3
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    total = query(f"SELECT COUNT(*) c FROM signers {clause}", params, one=True)["c"]
    rows = query(f"SELECT * FROM signers {clause} ORDER BY signer_code LIMIT ? OFFSET ?",
                 (*params, per_page, (page - 1) * per_page))
    reps = enrollment.readiness_for([r["id"] for r in rows])
    return ok([_signer_out(r, reps[r["id"]]) for r in rows], total=total, page=page, per_page=per_page)


@bp.post("/api/signers")
@login_required("admin")
def create_signer():
    data = _validate_signer(json_body())
    if query("SELECT 1 FROM signers WHERE signer_code=?", (data["signer_code"],), one=True):
        raise ApiError(409, "duplicate", "A signer with this ID already exists.")
    now = now_iso()
    sid = execute("INSERT INTO signers(signer_code, full_name, bank, account_number, notes, created_by, created_at, "
                  "updated_at) VALUES(?,?,?,?,?,?,?,?)",
                  (data["signer_code"], data.get("full_name", ""), data["bank"], data.get("account_number", ""),
                   data.get("notes", ""), g.user["id"], now, now))
    audit.record("signer.create", "signer", sid, {"signer_code": data["signer_code"], "bank": data["bank"]})
    return ok(_signer_out(_signer(sid)), 201)


@bp.get("/api/signers/<int:sid>")
@login_required()
def get_signer(sid: int):
    out = _signer_out(_signer(sid))
    out["specimens"] = [_specimen_out(r) for r in
                        query("SELECT * FROM specimens WHERE signer_id=? ORDER BY id DESC", (sid,))]
    hist = query("SELECT * FROM audit_log WHERE (entity='signer' AND entity_id=?) OR "
                 "(entity='specimen' AND entity_id IN (SELECT CAST(id AS TEXT) FROM specimens WHERE signer_id=?)) "
                 "ORDER BY id DESC LIMIT 50", (str(sid), sid))
    out["history"] = [dict(h) for h in hist]
    out["enrollment"] = enrollment.readiness(sid)
    out["sessions"] = out["enrollment"]["sessions"]
    return ok(out)


@bp.patch("/api/signers/<int:sid>")
@login_required("admin")
def update_signer(sid: int):
    _signer(sid)
    data = _validate_signer(json_body(), partial=True)
    if not data:
        raise ApiError(400, "validation_error", "Nothing to update.")
    if "signer_code" in data and query("SELECT 1 FROM signers WHERE signer_code=? AND id<>?",
                                       (data["signer_code"], sid), one=True):
        raise ApiError(409, "duplicate", "A signer with this ID already exists.")
    sets = ", ".join(f"{k}=?" for k in data)
    execute(f"UPDATE signers SET {sets}, updated_at=? WHERE id=?", (*data.values(), now_iso(), sid))
    audit.record("signer.update", "signer", sid, {k: (v if k != "account_number" else "changed") for k, v in data.items()})
    return ok(_signer_out(_signer(sid)))


@bp.delete("/api/signers/<int:sid>")
@login_required("admin")
def delete_signer(sid: int):
    _signer(sid)
    files = query("SELECT stored_name, processed_name FROM specimens WHERE signer_id=?", (sid,))
    execute("DELETE FROM signers WHERE id=?", (sid,))
    for f in files:
        storage.delete("specimens", f["stored_name"])
        storage.delete("specimens_processed", f["processed_name"])
    audit.record("signer.delete", "signer", sid, {"specimens_removed": len(files)})
    return ok({"deleted": sid})


@bp.get("/api/signers/lookup")
@login_required()
def lookup():
    acct = re.sub(r"\D", "", request.args.get("account", ""))
    row = query("SELECT * FROM signers WHERE account_number=?", (acct,), one=True) if acct else None
    return ok(_signer_out(row) if row else None)


# ------------------------------------------------------------------ signing sessions
@bp.get("/api/signers/<int:sid>/enrollment")
@login_required()
def signer_enrollment(sid: int):
    """Readiness of the signer's reference set (independent specimens, signing days) and its sessions."""
    _signer(sid)
    return ok(enrollment.readiness(sid))


@bp.get("/api/enrollment/overview")
@login_required()
def enrollment_overview():
    return ok(enrollment.overview())


@bp.post("/api/signers/<int:sid>/sessions")
@login_required("admin")
def create_session(sid: int):
    """Record a signing session: the day the signer wrote the signatures that will be enrolled with it."""
    _signer(sid)
    data = json_body()
    session_id = enrollment.create_session(sid, data, g.user["id"])
    row = enrollment.session_row(session_id)
    # consent_ref and notes are deliberately not copied into the audit log
    audit.record("session.create", "signer", sid, {"session_id": session_id, "session_date": row["session_date"],
                                                   "method": row["method"]})
    return ok(_session_out(row), 201)


@bp.patch("/api/sessions/<int:session_id>")
@login_required("admin")
def update_session(session_id: int):
    row = enrollment.session_row(session_id)
    data = enrollment.validate_session(json_body(), partial=True)
    if not data:
        raise ApiError(400, "validation_error", "Nothing to update.")
    sets = ", ".join(f"{k}=?" for k in data)
    execute(f"UPDATE enrollment_sessions SET {sets} WHERE id=?", (*data.values(), session_id))
    audit.record("session.update", "signer", row["signer_id"], {"session_id": session_id, "fields": sorted(data)})
    return ok(_session_out(enrollment.session_row(session_id)))


@bp.delete("/api/sessions/<int:session_id>")
@login_required("admin")
def delete_session(session_id: int):
    """Only an empty session can be removed: deleting one under specimens would silently turn them into
    specimens of unknown provenance."""
    row = enrollment.session_row(session_id)
    linked = query("SELECT COUNT(*) c FROM specimens WHERE session_id=?", (session_id,), one=True)["c"]
    if linked:
        raise ApiError(409, "session_has_specimens",
                       f"This session still has {linked} specimen(s). Move or delete them first.")
    execute("DELETE FROM enrollment_sessions WHERE id=?", (session_id,))
    audit.record("session.delete", "signer", row["signer_id"], {"session_id": session_id,
                                                                "session_date": row["session_date"]})
    return ok({"deleted": session_id})


def _session_out(row) -> dict:
    d = dict(row)
    counts = query("SELECT status, COUNT(*) c FROM specimens WHERE session_id=? GROUP BY status", (row["id"],))
    d["specimens_active"] = next((c["c"] for c in counts if c["status"] == "active"), 0)
    d["specimens_total"] = sum(c["c"] for c in counts)
    return d


# ------------------------------------------------------------------ specimens
@bp.post("/api/signers/<int:sid>/specimens")
@login_required("admin")
@rate_limited()
def upload_specimen(sid: int):
    signer = _signer(sid)
    if signer["status"] != "active":
        raise ApiError(409, "signer_inactive", "Activate the signer before enrolling specimens.")
    data, img, name = storage.read_upload(request.files.get("file"))
    allow_poor = request.form.get("allow_poor_quality") == "true"
    session_id = _form_session_id()
    spid = add_specimen(sid, data, img, name, g.user["id"], allow_poor, session_id=session_id,
                        source_ref=request.form.get("source_ref", ""))
    audit.record("specimen.enroll", "specimen", spid, {"signer_id": sid, "allow_poor_quality": allow_poor,
                                                       "session_id": session_id})
    return ok(_specimen_out(_specimen(spid)), 201)


def _form_session_id() -> int | None:
    raw = (request.form.get("session_id") or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise ApiError(400, "validation_error", "session_id must be an integer.") from exc


@bp.post("/api/signers/<int:sid>/specimens/batch")
@login_required("admin")
@rate_limited(cost=lambda: max(1, len(request.files.getlist("files"))))
def upload_specimens_batch(sid: int):
    """Enroll several signatures of one signing session in one request (field 'files').

    Every file is judged on its own: a rejected file (duplicate, same signing as an enrolled one, poor quality,
    unreadable) does not stop the others. The reference images are loaded once for the whole batch."""
    signer = _signer(sid)
    if signer["status"] != "active":
        raise ApiError(409, "signer_inactive", "Activate the signer before enrolling specimens.")
    files = request.files.getlist("files")
    if not files:
        raise ApiError(400, "file_required", "Attach one or more images in field 'files'.")
    if len(files) > current_app.config["MAX_BATCH_FILES"]:
        raise ApiError(400, "too_many_files", f"At most {current_app.config['MAX_BATCH_FILES']} files per batch.")
    session_id = _form_session_id()
    if session_id is None:
        raise ApiError(400, "validation_error", "session_id is required: record the signing session first, then "
                                                "enroll its signatures (this is what makes them count as independent).")
    enrollment.session_for(sid, session_id)
    allow_poor = request.form.get("allow_poor_quality") == "true"
    refs, ref_ids = enrollment.load_reference_set(sid)
    results = []
    for f in files:
        try:
            data, img, name = storage.read_upload(f, "files")
            spid = add_specimen(sid, data, img, name, g.user["id"], allow_poor, session_id=session_id,
                                refs=refs, ref_ids=ref_ids)
            results.append({"file": name, "ok": True, "specimen": _specimen_out(_specimen(spid))})
        except ApiError as err:
            results.append({"file": f.filename, "ok": False,
                            "error": {"code": err.code, "message": err.message}})
    enrolled = sum(r["ok"] for r in results)
    audit.record("specimen.batch_enroll", "signer", sid, {"session_id": session_id, "files": len(files),
                                                          "enrolled": enrolled})
    return ok({"session_id": session_id, "results": results, "enrolled": enrolled,
               "rejected": len(results) - enrolled, "enrollment": enrollment.readiness(sid)}, 201 if enrolled else 200)


@bp.put("/api/specimens/<int:spid>")
@login_required("admin")
@rate_limited()
def replace_specimen(spid: int):
    old = _specimen(spid)
    data, img, name = storage.read_upload(request.files.get("file"))
    proc = process_or_fail(img)
    if not proc.quality["acceptable"] and request.form.get("allow_poor_quality") != "true":
        raise ApiError(422, "poor_quality", "Specimen quality is not acceptable for enrollment.", {"quality": proc.quality})
    check_duplicates(old["signer_id"], storage.sha256(data), perceptual_hash(proc), exclude_id=spid)
    # old specimen is kept (inactive) for audit; the new one takes its place and, unless told otherwise, its session
    session_id = _form_session_id()
    if session_id is None:
        session_id = old["session_id"]
    source_ref = request.form.get("source_ref")
    execute("UPDATE specimens SET status='inactive', updated_at=? WHERE id=?", (now_iso(), spid))
    try:
        new_id = add_specimen(old["signer_id"], data, img, name, g.user["id"], True, session_id=session_id,
                              source_ref=old["source_ref"] if source_ref is None else source_ref)
    except ApiError:
        execute("UPDATE specimens SET status=?, updated_at=? WHERE id=?", (old["status"], now_iso(), spid))
        raise
    execute("UPDATE specimens SET replaced_by=? WHERE id=?", (new_id, spid))
    audit.record("specimen.replace", "specimen", spid, {"replaced_by": new_id})
    return ok(_specimen_out(_specimen(new_id)))


@bp.patch("/api/specimens/<int:spid>")
@login_required("admin")
def update_specimen(spid: int):
    """Change a specimen's status (active/inactive) and/or the signing session / source it belongs to.
    Linking an older specimen to the session in which it was really written is how existing enrollments are
    brought under the session rules; it is recorded in the audit log."""
    sp = _specimen(spid)
    data = json_body()
    if not any(k in data for k in ("status", "session_id", "source_ref")):
        raise ApiError(400, "validation_error", "Provide status, session_id and/or source_ref.")
    sets, params, detail = [], [], {}
    if "status" in data:
        if data["status"] not in ("active", "inactive"):
            raise ApiError(400, "validation_error", "Status must be active or inactive.")
        if data["status"] == "active" and sp["status"] != "active":
            check_duplicates(sp["signer_id"], sp["sha256"], sp["phash"], exclude_id=spid)
            enrollment.check_reactivation(sp)
        sets.append("status=?"); params.append(data["status"])
    if "session_id" in data:
        new_session = data["session_id"]
        if new_session is not None:
            if isinstance(new_session, bool) or not isinstance(new_session, int):
                raise ApiError(400, "validation_error", "session_id must be an integer or null.")
            enrollment.session_for(sp["signer_id"], new_session)
        sets.append("session_id=?"); params.append(new_session)
        detail["session_id"] = new_session
    if "source_ref" in data:
        sets.append("source_ref=?"); params.append(enrollment.clean_source_ref(data["source_ref"]))
        detail["source_ref_changed"] = True
    execute(f"UPDATE specimens SET {', '.join(sets)}, updated_at=? WHERE id=?", (*params, now_iso(), spid))
    if "status" in data:
        audit.record(f"specimen.{'activate' if data['status'] == 'active' else 'deactivate'}", "specimen", spid)
    if detail:
        audit.record("specimen.update", "specimen", spid, detail)
    return ok(_specimen_out(_specimen(spid)))


@bp.delete("/api/specimens/<int:spid>")
@login_required("admin")
def delete_specimen(spid: int):
    sp = _specimen(spid)
    execute("UPDATE specimens SET replaced_by=NULL WHERE replaced_by=?", (spid,))
    execute("DELETE FROM specimens WHERE id=?", (spid,))
    storage.delete("specimens", sp["stored_name"])
    storage.delete("specimens_processed", sp["processed_name"])
    audit.record("specimen.delete", "specimen", spid, {"signer_id": sp["signer_id"]})
    return ok({"deleted": spid})


@bp.get("/api/specimens/<int:spid>/image")
@login_required("admin", "operator")
def specimen_image(spid: int):
    """Biometric data: authenticated admin/operator only, every access is audited."""
    sp = _specimen(spid)
    variant = request.args.get("variant", "processed")
    if variant == "original":
        path = storage.safe_path("specimens", sp["stored_name"])
    else:
        path = storage.safe_path("specimens_processed", sp["processed_name"])
    audit.record("specimen.view", "specimen", spid, {"variant": variant})
    return send_file(path, max_age=0)


# ------------------------------------------------------------ dataset import
@bp.post("/api/signers/import-dataset")
@login_required("admin")
def import_dataset():
    """Enroll the genuine specimens listed in genuine_signatures.csv.

    The original files are only read; copies are stored in private storage.
    Signer IDs, banks and account numbers come from the manifest and filenames –
    no names are invented (full_name stays empty)."""
    data = json_body()
    splits = data.get("splits") or ["train", "val", "test"]
    m = load_signature_manifests(current_app.config["SIGNATURE_DATASET_DIR"])
    if m is None:
        raise ApiError(404, "dataset_missing", "Signature dataset not found (configure CV_SIGNATURE_DATASET).")
    created = enrolled = skipped = 0
    errors = []
    for r in m["genuine"]:
        if r["split_norm"] not in splits:
            continue
        code = r["signer_id"]
        row = query("SELECT * FROM signers WHERE signer_code=?", (code,), one=True)
        if row is None:
            now = now_iso()
            sid = execute("INSERT INTO signers(signer_code, full_name, bank, account_number, source, notes, created_by, "
                          "created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                          (code, "", r["bank"], r["path"].stem if r["path"].stem.isdigit() else "",
                           f"dataset:{r['split_norm']}", "Imported from genuine_signatures.csv",
                           g.user["id"], now, now))
            created += 1
        else:
            sid = row["id"]
        if query("SELECT 1 FROM specimens WHERE signer_id=? AND original_filename=?", (sid, r["filename"]), one=True):
            skipped += 1
            continue
        try:
            raw = r["path"].read_bytes()
            img = storage.decode_image(raw)
            if img is None:
                raise ApiError(422, "corrupted_image", "unreadable")
            add_specimen(sid, raw, img, r["filename"], g.user["id"], allow_poor_quality=True,
                         origin="dataset_import")
            enrolled += 1
        except (ApiError, OSError) as exc:
            errors.append({"file": r["filename"], "error": getattr(exc, "message", str(exc))})
    audit.record("signer.import_dataset", "dataset", "signature",
                 {"splits": splits, "created": created, "enrolled": enrolled, "skipped": skipped, "errors": len(errors)})
    return ok({"signers_created": created, "specimens_enrolled": enrolled, "skipped_existing": skipped,
               "errors": errors[:50]})
