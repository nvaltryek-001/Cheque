"""/api/signature/* (health, preprocess preview) and /api/verifications (CRUD + manual review)."""
from __future__ import annotations

import base64
import json

import cv2
from flask import Blueprint, g, request, send_file

from ..db import dumps, execute, get_setting, now_iso, query, row_to_dict
from ..services import audit, models, storage
from ..services.auth import login_required
from ..services.throttle import rate_limited
from ..services.enrollment import load_reference_set, process_or_fail
from ..signature.compare import decide
from ..signature.detect import crop_fraction, find_signature
from .common import ApiError, json_body, ok, pagination

bp = Blueprint("verifications", __name__)


@bp.get("/api/signature/health")
@login_required()
def signature_health():
    st = models.status()
    counts = query("SELECT (SELECT COUNT(*) FROM signers WHERE status='active') signers, "
                   "(SELECT COUNT(*) FROM specimens WHERE status='active') specimens", one=True)
    return ok({"model": st["signature_model"], "active_signers": counts["signers"],
               "active_specimens": counts["specimens"],
               "ready": counts["specimens"] > 0,
               "message": None if counts["specimens"] else "No specimens enrolled – enroll signers before verifying."})


@bp.post("/api/signature/preprocess")
@login_required("admin", "operator")
@rate_limited()
def preprocess_preview():
    """Run preprocessing on an uploaded image and return the normalised result (nothing is stored)."""
    _, img, _ = storage.read_upload(request.files.get("file"))
    if request.form.get("mode") == "cheque":
        crop, info = find_signature(img)
        if crop is None:
            raise ApiError(422, "signature_not_found", info.get("reason", "Signature not found."))
        img = crop
    proc = process_or_fail(img)
    okk, buf = cv2.imencode(".png", proc.gray)
    return ok({"quality": proc.quality, "bbox": proc.bbox,
               "image": "data:image/png;base64," + base64.b64encode(buf.tobytes()).decode()})


def _thresholds() -> dict:
    out = {}
    m = get_setting("signature.match_threshold")
    mm = get_setting("signature.mismatch_threshold")
    if m is not None:
        out["match"] = float(m)
    if mm is not None:
        out["mismatch"] = float(mm)
    return out


@bp.post("/api/verifications")
@login_required("admin", "operator")
@rate_limited()
def verify():
    form = request.form if (request.form or request.files) else (request.get_json(silent=True) or {})
    try:
        signer_id = int(form.get("signer_id", 0))
    except (TypeError, ValueError) as exc:
        raise ApiError(400, "validation_error", "signer_id must be an integer.") from exc
    signer = query("SELECT * FROM signers WHERE id=?", (signer_id,), one=True)
    if signer is None:
        raise ApiError(404, "not_found", "Select an enrolled signer.")
    if signer["status"] != "active":
        raise ApiError(409, "signer_inactive", "This signer is inactive.")
    refs, ref_ids = load_reference_set(signer_id)
    if not refs:
        raise ApiError(409, "no_reference", "This signer has no active reference specimens. Enroll a specimen first.")

    cheque_id = form.get("cheque_id")
    if cheque_id:
        crow = query("SELECT * FROM cheques WHERE id=?", (int(cheque_id),), one=True)
        if crow is None:
            raise ApiError(404, "not_found", "Cheque not found.")
        box = form.get("box")
        if box:
            box = json.loads(box) if isinstance(box, str) else box
            try:
                query_img = crop_fraction(storage.load_image("cheques", crow["stored_name"]), box)
            except (ValueError, TypeError) as exc:
                raise ApiError(400, "validation_error", str(exc)) from exc
        elif crow["signature_crop_name"]:
            query_img = storage.load_image("crops", crow["signature_crop_name"])
        else:
            raise ApiError(422, "signature_not_found", "No signature region for this cheque – select one manually.")
    else:
        _, img, _ = storage.read_upload(request.files.get("file"))
        if form.get("mode") == "cheque":
            crop, info = find_signature(img)
            if crop is None:
                raise ApiError(422, "signature_not_found", info.get("reason", "Signature not found."))
            img = crop
        query_img = img
        cheque_id = None

    q = process_or_fail(query_img)
    result = decide(q, refs, models.fusion_model(), _thresholds())
    for p in result["per_specimen"]:
        p["specimen_id"] = ref_ids[p["index"]]
    result["query_quality"] = q.quality
    qname = storage.save_image("queries", query_img)
    vid = execute("INSERT INTO verifications(cheque_id, signer_id, query_name, decision, score, details_json, "
                  "model_version, created_by, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                  (int(cheque_id) if cheque_id else None, signer_id, qname, result["decision"], result["score"],
                   dumps(result), result["model_version"], g.user["id"], now_iso()))
    audit.record("verification.create", "verification", vid,
                 {"signer_id": signer_id, "cheque_id": cheque_id, "decision": result["decision"]})
    return ok(_out(_get(vid)), 201)


def _get(vid: int):
    row = query("SELECT v.*, s.signer_code, s.full_name signer_name, s.bank signer_bank, u.username created_by_name, "
                "r.username reviewed_by_name FROM verifications v LEFT JOIN signers s ON s.id=v.signer_id "
                "LEFT JOIN users u ON u.id=v.created_by LEFT JOIN users r ON r.id=v.reviewed_by WHERE v.id=?",
                (vid,), one=True)
    if row is None:
        raise ApiError(404, "not_found", "Verification not found.")
    return row


def _out(row) -> dict:
    d = row_to_dict(row, ("details_json",))
    d.pop("query_name", None)
    return d


@bp.get("/api/verifications")
@login_required()
def list_verifications():
    page, per_page = pagination()
    where, params = [], []
    for arg, col in (("decision", "v.decision"), ("signer_id", "v.signer_id"), ("cheque_id", "v.cheque_id")):
        if request.args.get(arg):
            where.append(f"{col}=?"); params.append(request.args[arg])
    if request.args.get("reviewed") == "0":
        where.append("v.review_decision IS NULL")
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    total = query(f"SELECT COUNT(*) c FROM verifications v {clause}", params, one=True)["c"]
    rows = query("SELECT v.id, v.cheque_id, v.signer_id, v.decision, v.score, v.model_version, v.review_decision, "
                 "v.created_at, s.signer_code, s.bank signer_bank, u.username created_by_name FROM verifications v "
                 "LEFT JOIN signers s ON s.id=v.signer_id LEFT JOIN users u ON u.id=v.created_by "
                 f"{clause} ORDER BY v.id DESC LIMIT ? OFFSET ?", (*params, per_page, (page - 1) * per_page))
    return ok([dict(r) for r in rows], total=total, page=page, per_page=per_page)


@bp.get("/api/verifications/<int:vid>")
@login_required()
def get_verification(vid: int):
    return ok(_out(_get(vid)))


@bp.patch("/api/verifications/<int:vid>")
@login_required("admin", "operator")
def review(vid: int):
    _get(vid)
    data = json_body()
    decision = data.get("review_decision")
    if decision not in ("APPROVED", "REJECTED", None):
        raise ApiError(400, "validation_error", "review_decision must be APPROVED, REJECTED or null.")
    note = str(data.get("review_note", ""))[:1000]
    execute("UPDATE verifications SET review_decision=?, review_note=?, reviewed_by=?, reviewed_at=? WHERE id=?",
            (decision, note, g.user["id"] if decision else None, now_iso() if decision else None, vid))
    audit.record("verification.review", "verification", vid, {"review_decision": decision})
    return ok(_out(_get(vid)))


@bp.delete("/api/verifications/<int:vid>")
@login_required("admin")
def delete_verification(vid: int):
    row = _get(vid)
    execute("DELETE FROM verifications WHERE id=?", (vid,))
    storage.delete("queries", row["query_name"])
    audit.record("verification.delete", "verification", vid)
    return ok({"deleted": vid})


@bp.get("/api/verifications/<int:vid>/query-image")
@login_required("admin", "operator")
def query_image(vid: int):
    row = _get(vid)
    if not row["query_name"]:
        raise ApiError(404, "not_found", "No query image stored.")
    return send_file(storage.safe_path("queries", row["query_name"]), mimetype="image/png", max_age=0)
