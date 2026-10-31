"""/api/cheques – upload, OCR, CRUD, batch, images, signature crop, PDF report."""
from __future__ import annotations

import io
import json
import uuid

from flask import Blueprint, current_app, g, request, send_file

from ..db import execute, now_iso, query, row_to_dict
from ..ocr.fields import FIELD_ORDER
from ..services import audit, cheques, storage
from ..services.auth import login_required
from ..services.reports import cheque_pdf
from ..signature.detect import crop_fraction
from .common import ApiError, json_body, ok, pagination

bp = Blueprint("cheques", __name__)
EDITABLE_STATUS = {"processed", "needs_review", "reviewed"}


def _get(cid: int):
    row = query("SELECT * FROM cheques WHERE id=?", (cid,), one=True)
    if row is None:
        raise ApiError(404, "not_found", "Cheque not found.")
    return row


@bp.post("/api/cheques")
@login_required("admin", "operator")
def upload():
    data, img, name = storage.read_upload(request.files.get("file"))
    dup = query("SELECT id FROM cheques WHERE sha256=?", (storage.sha256(data),), one=True)
    cid = cheques.create_from_upload(data, img, name, g.user["id"])
    audit.record("cheque.create", "cheque", cid, {"file": name})
    out = cheques.serialize(_get(cid))
    out["duplicate_of"] = dup["id"] if dup else None
    signer = cheques.match_signer_by_account(out["final"]["account_number"])
    out["suggested_signer"] = {"id": signer["id"], "signer_code": signer["signer_code"],
                               "full_name": signer["full_name"]} if signer else None
    return ok(out, 201)


@bp.post("/api/cheques/batch")
@login_required("admin", "operator")
def batch():
    files = request.files.getlist("files")
    if not files:
        raise ApiError(400, "file_required", "Attach one or more images in field 'files'.")
    if len(files) > current_app.config["MAX_BATCH_FILES"]:
        raise ApiError(400, "too_many_files", f"At most {current_app.config['MAX_BATCH_FILES']} files per batch.")
    batch_id = uuid.uuid4().hex[:12]
    results = []
    for f in files:
        try:
            data, img, name = storage.read_upload(f, "files")
            cid = cheques.create_from_upload(data, img, name, g.user["id"], batch_id)
            results.append({"file": name, "ok": True, **cheques.summary(_get(cid))})
        except ApiError as err:
            results.append({"file": f.filename, "ok": False, "error": err.message})
    audit.record("cheque.batch", "batch", batch_id, {"files": len(files), "ok": sum(r["ok"] for r in results)})
    return ok({"batch_id": batch_id, "results": results,
               "processed": sum(r["ok"] for r in results), "failed": sum(not r["ok"] for r in results)}, 201)


@bp.get("/api/cheques")
@login_required()
def list_cheques():
    page, per_page = pagination()
    where, params = [], []
    status = request.args.get("status")
    if status:
        where.append("status=?"); params.append(status)
    batch_id = request.args.get("batch_id")
    if batch_id:
        where.append("batch_id=?"); params.append(batch_id)
    q = (request.args.get("q") or "").strip()
    if q:
        where.append("(original_filename LIKE ? OR fields_json LIKE ? OR corrected_json LIKE ?)")
        params += [f"%{q}%"] * 3
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    total = query(f"SELECT COUNT(*) c FROM cheques {clause}", params, one=True)["c"]
    rows = query(f"SELECT * FROM cheques {clause} ORDER BY id DESC LIMIT ? OFFSET ?",
                 (*params, per_page, (page - 1) * per_page))
    return ok([cheques.summary(r) for r in rows], total=total, page=page, per_page=per_page)


@bp.get("/api/cheques/<int:cid>")
@login_required()
def get_cheque(cid: int):
    out = cheques.serialize(_get(cid), include_raw=request.args.get("raw") == "1")
    vs = query("SELECT v.*, s.signer_code FROM verifications v LEFT JOIN signers s ON s.id=v.signer_id "
               "WHERE v.cheque_id=? ORDER BY v.id DESC", (cid,))
    out["verifications"] = [{k: d[k] for k in ("id", "decision", "score", "signer_code", "created_at", "review_decision")}
                            for d in (row_to_dict(v, ("details_json",)) for v in vs)]
    signer = cheques.match_signer_by_account(out["final"]["account_number"])
    out["suggested_signer"] = {"id": signer["id"], "signer_code": signer["signer_code"],
                               "full_name": signer["full_name"]} if signer else None
    return ok(out)


@bp.patch("/api/cheques/<int:cid>")
@login_required("admin", "operator")
def update_cheque(cid: int):
    row = _get(cid)
    data = json_body()
    corrected = json.loads(row["corrected_json"] or "{}")
    changed = []
    for k, v in (data.get("corrections") or {}).items():
        if k not in FIELD_ORDER:
            raise ApiError(400, "validation_error", f"Unknown field '{k}'.")
        if v is None:
            corrected.pop(k, None)
        else:
            corrected[k] = str(v).strip()[:300]
        changed.append(k)
    status = data.get("status", row["status"])
    if status not in EDITABLE_STATUS:
        raise ApiError(400, "validation_error", "Status must be processed, needs_review or reviewed.")
    notes = str(data.get("notes", row["notes"]))[:2000]
    reviewed = status == "reviewed" and row["status"] != "reviewed"
    execute("UPDATE cheques SET corrected_json=?, status=?, notes=?, updated_at=?, "
            "reviewed_by=CASE WHEN ? THEN ? ELSE reviewed_by END, reviewed_at=CASE WHEN ? THEN ? ELSE reviewed_at END "
            "WHERE id=?",
            (json.dumps(corrected), status, notes, now_iso(), reviewed, g.user["id"], reviewed, now_iso(), cid))
    audit.record("cheque.update", "cheque", cid, {"fields": changed, "status": status})
    return ok(cheques.serialize(_get(cid)))


@bp.post("/api/cheques/<int:cid>/reprocess")
@login_required("admin", "operator")
def reprocess(cid: int):
    _get(cid)
    cheques.reprocess(cid)
    audit.record("cheque.reprocess", "cheque", cid)
    return ok(cheques.serialize(_get(cid)))


@bp.delete("/api/cheques/<int:cid>")
@login_required("admin", "operator")
def delete_cheque(cid: int):
    row = _get(cid)
    # verifications keep their own copy of the query signature (cheque_id is set to NULL)
    execute("DELETE FROM cheques WHERE id=?", (cid,))
    storage.delete("cheques", row["stored_name"])
    storage.delete("crops", row["signature_crop_name"])
    audit.record("cheque.delete", "cheque", cid)
    return ok({"deleted": cid})


@bp.get("/api/cheques/<int:cid>/image")
@login_required()
def cheque_image(cid: int):
    row = _get(cid)
    return send_file(storage.safe_path("cheques", row["stored_name"]), max_age=0)


@bp.get("/api/cheques/<int:cid>/signature")
@login_required()
def cheque_signature(cid: int):
    row = _get(cid)
    if not row["signature_crop_name"]:
        raise ApiError(404, "not_found", "No signature crop for this cheque.")
    return send_file(storage.safe_path("crops", row["signature_crop_name"]), mimetype="image/png", max_age=0)


@bp.post("/api/cheques/<int:cid>/signature-crop")
@login_required("admin", "operator")
def set_signature_crop(cid: int):
    row = _get(cid)
    data = json_body()
    box = data.get("box")
    if not isinstance(box, list) or len(box) != 4:
        raise ApiError(400, "validation_error", "box must be [x, y, width, height] as page fractions.")
    img = storage.load_image("cheques", row["stored_name"])
    try:
        crop = crop_fraction(img, box)
    except (ValueError, TypeError) as exc:
        raise ApiError(400, "validation_error", str(exc)) from exc
    name = storage.save_image("crops", crop)
    storage.delete("crops", row["signature_crop_name"])
    execute("UPDATE cheques SET signature_crop_name=?, updated_at=? WHERE id=?", (name, now_iso(), cid))
    audit.record("cheque.signature_crop", "cheque", cid, {"box": box})
    return ok({"has_signature_crop": True})


@bp.get("/api/cheques/<int:cid>/report.pdf")
@login_required()
def cheque_report(cid: int):
    d = cheques.serialize(_get(cid))
    vs = query("SELECT v.*, s.signer_code FROM verifications v LEFT JOIN signers s ON s.id=v.signer_id "
               "WHERE v.cheque_id=? ORDER BY v.id", (cid,))
    try:
        pdf = cheque_pdf(d, [row_to_dict(v, ("details_json",)) for v in vs], g.user["username"],
                         mask=request.args.get("unmask") != "1" or g.user["role"] != "admin")
    except RuntimeError as exc:
        raise ApiError(501, "not_available", str(exc)) from exc
    audit.record("report.cheque_pdf", "cheque", cid)
    return send_file(io.BytesIO(pdf), mimetype="application/pdf", as_attachment=True,
                     download_name=f"cheque-{cid}-report.pdf")
