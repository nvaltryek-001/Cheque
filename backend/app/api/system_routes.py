"""Health, dashboard, analytics, settings, audit log, datasets, models/training, reports."""
from __future__ import annotations

import io
import json
import re
from pathlib import Path

from flask import Blueprint, current_app, g, request, send_file

from ..db import get_setting, query, row_to_dict, set_setting
from ..services import atomic, audit, jobs, models
from ..services.auth import login_required
from ..services.cheques import final_values
from ..services.datasets import (audit_ocr_dataset, audit_signature_dataset, discover_ocr_images,
                                 load_signature_manifests)
from ..services.defaults import DEFAULT_SETTINGS
from ..services.reports import csv_bytes
from .common import ApiError, json_body, ok, pagination

bp = Blueprint("system", __name__)


@bp.get("/api/health")
def health():
    """Public liveness probe – reveals no configuration or paths."""
    query("SELECT 1", one=True)
    return ok({"status": "ok", "version": current_app.config["APP_VERSION"]})


@bp.get("/api/system/status")
@login_required()
def system_status():
    st = models.status()
    st["datasets"] = {
        "ocr": Path(current_app.config["OCR_DATASET_DIR"]).is_dir(),
        "signature": load_signature_manifests(current_app.config["SIGNATURE_DATASET_DIR"]) is not None,
    }
    st["version"] = current_app.config["APP_VERSION"]
    return ok(st)


@bp.get("/api/dashboard")
@login_required()
def dashboard():
    c = query("SELECT COUNT(*) total, SUM(status='needs_review') needs_review, SUM(status='reviewed') reviewed, "
              "SUM(status='processed') processed, AVG(processing_ms) avg_ms FROM cheques", one=True)
    v = query("SELECT COUNT(*) AS total, SUM(decision='MATCH') AS n_match, SUM(decision='REVIEW') AS n_review, "
              "SUM(decision='MISMATCH') AS n_mismatch, SUM(review_decision IS NULL AND decision<>'MATCH') AS pending "
              "FROM verifications", one=True)
    s = query("SELECT (SELECT COUNT(*) FROM signers) signers, (SELECT COUNT(*) FROM signers WHERE status='active') "
              "active_signers, (SELECT COUNT(*) FROM specimens WHERE status='active') specimens", one=True)
    recent_c = query("SELECT id, original_filename, status, created_at FROM cheques ORDER BY id DESC LIMIT 6")
    recent_v = query("SELECT v.id, v.decision, v.score, v.created_at, s.signer_code FROM verifications v "
                     "LEFT JOIN signers s ON s.id=v.signer_id ORDER BY v.id DESC LIMIT 6")
    return ok({
        "cheques": {k: (c[k] or 0) for k in ("total", "needs_review", "reviewed", "processed")},
        "avg_processing_ms": round(c["avg_ms"]) if c["avg_ms"] else None,
        "verifications": {"total": v["total"] or 0, "match": v["n_match"] or 0, "review": v["n_review"] or 0,
                          "mismatch": v["n_mismatch"] or 0, "pending": v["pending"] or 0},
        "enrollment": dict(s),
        "models": models.status(),
        "recent_cheques": [dict(r) for r in recent_c],
        "recent_verifications": [dict(r) for r in recent_v],
    })


@bp.get("/api/analytics")
@login_required()
def analytics():
    rows = query("SELECT fields_json, corrected_json, status, created_at FROM cheques")
    field_stats: dict = {}
    by_bank: dict = {}
    by_day: dict = {}
    corrections = 0
    for r in rows:
        d = row_to_dict(r, ("fields_json", "corrected_json"))
        fields, corr = d.get("fields") or {}, d.get("corrected") or {}
        corrections += len(corr)
        for k, f in fields.items():
            st = field_stats.setdefault(k, {"ok": 0, "review": 0, "missing": 0, "corrected": 0})
            st[f.get("status", "missing")] = st.get(f.get("status", "missing"), 0) + 1
            if k in corr:
                st["corrected"] += 1
        bank = final_values(d)["bank_name"] or "Unknown"
        by_bank[bank] = by_bank.get(bank, 0) + 1
        day = d["created_at"][:10]
        by_day[day] = by_day.get(day, 0) + 1
    vrows = query("SELECT decision, score, review_decision, created_at FROM verifications")
    decisions: dict = {}
    agreement = {"agree": 0, "disagree": 0}
    for v in vrows:
        decisions[v["decision"]] = decisions.get(v["decision"], 0) + 1
        if v["review_decision"] and v["decision"] != "REVIEW":
            same = (v["decision"] == "MATCH") == (v["review_decision"] == "APPROVED")
            agreement["agree" if same else "disagree"] += 1
    buckets = [0] * 10
    for v in vrows:
        if v["score"] is not None:
            buckets[min(9, int(v["score"] * 10))] += 1
    return ok({"cheques": len(rows), "field_status": field_stats, "by_bank": by_bank,
               "per_day": dict(sorted(by_day.items())[-30:]), "manual_corrections": corrections,
               "decisions": decisions, "score_histogram": buckets, "reviewer_agreement": agreement})


# ----------------------------------------------------------------- settings
@bp.get("/api/settings")
@login_required()
def get_settings():
    return ok({k: get_setting(k, v) for k, v in DEFAULT_SETTINGS.items()})


@bp.put("/api/settings")
@login_required("admin")
def put_settings():
    data = json_body()
    for k, v in data.items():
        if k not in DEFAULT_SETTINGS:
            raise ApiError(400, "validation_error", f"Unknown setting '{k}'.")
        if k.endswith("_threshold") and v is not None:
            try:
                v = float(v)
            except (TypeError, ValueError) as exc:
                raise ApiError(400, "validation_error", f"{k} must be a number between 0 and 1.") from exc
            if not 0 <= v <= 1:
                raise ApiError(400, "validation_error", f"{k} must be between 0 and 1.")
        if k in ("signature.max_specimens_per_signer", "signature.duplicate_hamming", "retention.days",
                 "enrollment.min_specimens", "enrollment.min_sessions"):
            if isinstance(v, bool) or not isinstance(v, int) or v < 0 or v > 1000:
                raise ApiError(400, "validation_error", f"{k} must be a non-negative integer.")
        if k in ("enrollment.min_specimens", "enrollment.min_sessions") and v < 1:
            raise ApiError(400, "validation_error", f"{k} must be at least 1.")
        if k in ("ocr.auto_signature_crop", "enrollment.cap_match_when_not_ready") and not isinstance(v, bool):
            raise ApiError(400, "validation_error", f"{k} must be true or false.")
        set_setting(k, v)
    m, mm = get_setting("signature.match_threshold"), get_setting("signature.mismatch_threshold")
    if m is not None and mm is not None and mm > m:
        raise ApiError(400, "validation_error", "Mismatch threshold cannot exceed the match threshold.")
    audit.record("settings.update", "settings", None, data)
    return get_settings()


@bp.get("/api/audit-log")
@login_required("admin", "auditor")
def audit_log():
    page, per_page = pagination()
    where, params = [], []
    if request.args.get("action"):
        where.append("action LIKE ?"); params.append(request.args["action"] + "%")
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    total = query(f"SELECT COUNT(*) c FROM audit_log {clause}", params, one=True)["c"]
    rows = query(f"SELECT * FROM audit_log {clause} ORDER BY id DESC LIMIT ? OFFSET ?",
                 (*params, per_page, (page - 1) * per_page))
    return ok([dict(r) for r in rows], total=total, page=page, per_page=per_page)


# ----------------------------------------------------------------- datasets
def _audit_file(name: str) -> Path:
    d = Path(current_app.config["REPORTS_DIR"]) / "final"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{name}.json"


def _cached_audit(name: str, fn, root) -> dict:
    p = _audit_file(name)
    cached = None
    if request.args.get("refresh") != "1" and p.is_file():
        try:
            cached = json.loads(p.read_text())
        except (OSError, ValueError):
            cached = None                      # damaged cache file: build it again below
    if cached is None:
        if request.args.get("refresh") == "1" and g.user["role"] != "admin":
            raise ApiError(403, "forbidden", "Only admins can re-run dataset audits.")
        report = fn(Path(root))
        atomic.write_json(p, report, default=str)
        audit.record("dataset.audit", "dataset", name)
        return report
    return cached


@bp.get("/api/datasets/ocr/audit")
@login_required()
def ocr_audit():
    return ok(_cached_audit("OCR_DATASET_AUDIT", audit_ocr_dataset, current_app.config["OCR_DATASET_DIR"]))


@bp.get("/api/datasets/signature/audit")
@login_required()
def signature_audit():
    rep = _cached_audit("SIGNATURE_DATASET_AUDIT", audit_signature_dataset, current_app.config["SIGNATURE_DATASET_DIR"])
    return ok(rep)


@bp.get("/api/datasets/ocr/items")
@login_required()
def ocr_items():
    page, per_page = pagination()
    items = discover_ocr_images(Path(current_app.config["OCR_DATASET_DIR"]))
    split, bank = request.args.get("split"), request.args.get("bank")
    items = [i for i in items if (not split or i["split"] == split) and (not bank or i["bank"] == bank)]
    sl = items[(page - 1) * per_page: page * per_page]
    return ok([{"split": i["split"], "bank": i["bank"], "filename": i["filename"],
                "key": f'{i["split"]}/{i["bank"]}/{i["filename"]}'} for i in sl],
              total=len(items), page=page, per_page=per_page)


@bp.get("/api/datasets/signature/items")
@login_required()
def signature_items():
    page, per_page = pagination()
    m = load_signature_manifests(current_app.config["SIGNATURE_DATASET_DIR"])
    if m is None:
        return ok([], total=0, page=page, per_page=per_page)
    kind = request.args.get("kind", "genuine")
    rows = m["genuine"] if kind == "genuine" else m["altered"]
    split, bank = request.args.get("split"), request.args.get("bank")
    rows = [r for r in rows if (not split or r["split_norm"] == split) and (not bank or r["bank"] == bank)]
    sl = rows[(page - 1) * per_page: page * per_page]
    return ok([{"filename": r["filename"], "bank": r["bank"], "split": r["split_norm"],
                "signer_id": r.get("signer_id") or r.get("target_signer_id"), "kind": kind} for r in sl],
              total=len(rows), page=page, per_page=per_page)


_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.\-]+$")


@bp.get("/api/datasets/<string:which>/image")
@login_required("admin", "operator")
def dataset_image(which: str):
    """Serve a dataset image by its manifest identity – never by a client-supplied path."""
    name = request.args.get("filename", "")
    if not _SAFE_NAME.match(name):
        raise ApiError(400, "validation_error", "Invalid filename.")
    if which == "ocr":
        for i in discover_ocr_images(Path(current_app.config["OCR_DATASET_DIR"])):
            if i["filename"] == name and i["split"] == request.args.get("split") and i["bank"] == request.args.get("bank"):
                return send_file(i["path"], max_age=0)
    elif which == "signature":
        m = load_signature_manifests(current_app.config["SIGNATURE_DATASET_DIR"])
        for r in (m["genuine"] + m["altered"]) if m else []:
            if r["filename"] == name:
                audit.record("dataset.signature_view", "dataset", name)
                return send_file(r["path"], max_age=0)
    raise ApiError(404, "not_found", "Image not found in dataset manifest.")


# ------------------------------------------------------------ models/training
def _readiness() -> dict:
    ocr = _cached_audit("OCR_DATASET_AUDIT", audit_ocr_dataset, current_app.config["OCR_DATASET_DIR"])
    sig = _cached_audit("SIGNATURE_DATASET_AUDIT", audit_signature_dataset, current_app.config["SIGNATURE_DATASET_DIR"])
    has_ocr = ocr.get("total_images", 0) > 0
    has_sig = sig.get("genuine_rows", 0) > 0
    return {"tasks": [
        {"id": "handwritten_ocr", "name": "Handwritten field recognition (PaddleOCR fine-tune)",
         "status": "BLOCKED",
         "reason": "No transcription labels exist and the filled fields are machine-printed, not handwritten.",
         "requirements": "Line-level crops of real handwritten date/payee/amount fields with verified transcriptions "
                         "(PaddleOCR rec format), ideally 2,000+ lines across banks, split by cheque."},
        {"id": "micr", "name": "MICR E-13B digit templates", "status": "READY" if has_ocr else "BLOCKED",
         "reason": "Weak labels: filename stem = cheque number (first MICR group). Templates learned from train only.",
         "action": "micr_train"},
        {"id": "ocr_eval", "name": "OCR field-extraction evaluation", "status": "READY" if has_ocr else "BLOCKED",
         "reason": "Measures cheque-number accuracy, account registry agreement, amount self-consistency and fill rate.",
         "action": "ocr_eval"},
        {"id": "signature_fusion", "name": "Signature similarity fusion (logistic regression)",
         "status": "READY (limited)" if has_sig else "BLOCKED",
         "reason": "Trained on train-split pairs; thresholds on validation. Positives are simulated re-captures "
                   "because only one genuine specimen per signer exists, and the \"altered\" negatives are script-made "
                   "edits of the specimen: the results are not genuine-acceptance or forgery-detection rates.",
         "action": "signature_train"},
        {"id": "signature_evaluation", "name": "Valid signature evaluation (genuine acceptance, skilled forgeries)",
         "status": "BLOCKED",
         "reason": "Needs genuine signatures written on later days than the references and forgeries made by other "
                   "people; the delivered data has one specimen per signer and none of either. Protocol: "
                   "docs/SIGNATURE_EVALUATION_PROTOCOL.md (python manage.py eval-signatures).",
         "requirements": "≥30 test signers with ≥5 independent specimens on ≥2 days plus ≥3 specimens on later days, "
                         "≥3 skilled forgeries per signer by ≥2 other people, split by signer."},
        {"id": "signer_specific", "name": "Signer-specific / Siamese model", "status": "BLOCKED",
         "reason": "Requires several independent genuine specimens per signer and real skilled forgeries.",
         "requirements": "≥5 genuine specimens per signer collected on different days, ≥3 skilled forgeries per "
                         "signer, ≥200 signers, splits by signer."},
    ]}


@bp.get("/api/models/status")
@login_required()
def model_status():
    return ok(models.status())


@bp.get("/api/models/readiness")
@login_required()
def readiness():
    return ok(_readiness())


@bp.post("/api/models/<string:kind>/run")
@login_required("admin")
def run_model(kind: str):
    mapping = {"signature": "signature_train", "micr": "micr_train", "ocr-eval": "ocr_eval"}
    if kind not in mapping:
        raise ApiError(404, "not_found", "Unknown job.")
    cfg = json_body()
    if kind == "ocr-eval" and cfg.get("split", "test") not in ("train", "val", "test"):
        raise ApiError(400, "validation_error", "split must be train, val or test.")
    allowed = {"split", "positives_per_signer", "impostors_per_signer", "target_far", "target_frr", "synchronous"}
    cfg = {k: v for k, v in cfg.items() if k in allowed}
    try:
        run_id = jobs.start(current_app._get_current_object(), mapping[kind], cfg, g.user["id"])
    except RuntimeError as exc:
        raise ApiError(409, "job_running", str(exc)) from exc
    audit.record("model.run", "model_run", run_id, {"kind": mapping[kind]})
    return ok({"run_id": run_id}, 202)


@bp.get("/api/models/runs")
@login_required()
def list_runs():
    page, per_page = pagination()
    total = query("SELECT COUNT(*) c FROM model_runs", one=True)["c"]
    rows = query("SELECT r.id, r.kind, r.status, r.model_version, r.message, r.started_at, r.finished_at, "
                 "u.username started_by_name FROM model_runs r LEFT JOIN users u ON u.id=r.started_by "
                 "ORDER BY r.id DESC LIMIT ? OFFSET ?", (per_page, (page - 1) * per_page))
    return ok([dict(r) for r in rows], total=total, page=page, per_page=per_page)


@bp.get("/api/models/runs/<int:rid>")
@login_required()
def get_run(rid: int):
    row = query("SELECT * FROM model_runs WHERE id=?", (rid,), one=True)
    if row is None:
        raise ApiError(404, "not_found", "Run not found.")
    d = row_to_dict(row, ("config_json", "metrics_json"))
    if d.get("metrics") and "predictions" in d["metrics"]:
        d["metrics"]["predictions"] = d["metrics"]["predictions"][:200]
    return ok(d)


# -------------------------------------------------------------------- reports
@bp.get("/api/reports/files")
@login_required()
def report_files():
    d = Path(current_app.config["REPORTS_DIR"]) / "final"
    files = sorted(p for p in d.glob("*") if p.suffix in (".md", ".json")) if d.is_dir() else []
    return ok([{"name": p.name, "size": p.stat().st_size, "modified": p.stat().st_mtime} for p in files])


@bp.get("/api/reports/files/<string:name>")
@login_required()
def report_file(name: str):
    if not _SAFE_NAME.match(name) or not name.endswith((".md", ".json")):
        raise ApiError(400, "validation_error", "Invalid report name.")
    base = (Path(current_app.config["REPORTS_DIR"]) / "final").resolve()
    p = (base / name).resolve()
    if base not in p.parents or not p.is_file():
        raise ApiError(404, "not_found", "Report not found.")
    audit.record("report.download", "report", name)
    return send_file(p, as_attachment=True, download_name=name, max_age=0)


@bp.get("/api/reports/<string:kind>.csv")
@login_required()
def export_csv(kind: str):
    from ..ocr.fields import FIELD_ORDER
    from ..services.audit import mask_account
    if kind == "cheques":
        rows = query("SELECT * FROM cheques ORDER BY id")
        out = []
        for r in rows:
            d = row_to_dict(r, ("fields_json", "corrected_json"))
            f = final_values(d)
            f["account_number"] = mask_account(f["account_number"])
            out.append([d["id"], d["original_filename"], d["status"], d["created_at"]] + [f[k] for k in FIELD_ORDER])
        data = csv_bytes(["id", "file", "status", "created_at"] + FIELD_ORDER, out)
    elif kind == "verifications":
        rows = query("SELECT v.id, v.cheque_id, s.signer_code, v.decision, v.score, v.model_version, v.review_decision, "
                     "v.review_note, v.created_at FROM verifications v LEFT JOIN signers s ON s.id=v.signer_id ORDER BY v.id")
        data = csv_bytes(["id", "cheque_id", "signer", "decision", "score", "model_version", "review_decision",
                          "review_note", "created_at"], [list(r) for r in rows])
    elif kind == "audit-log":
        if g.user["role"] not in ("admin", "auditor"):
            raise ApiError(403, "forbidden", "Audit log export requires admin or auditor role.")
        rows = query("SELECT id, created_at, username, action, entity, entity_id, detail, ip FROM audit_log ORDER BY id")
        data = csv_bytes(["id", "created_at", "user", "action", "entity", "entity_id", "detail", "ip"], [list(r) for r in rows])
    elif kind == "signers":
        rows = query("SELECT s.id, s.signer_code, s.full_name, s.bank, s.account_number, s.status, s.source, "
                     "(SELECT COUNT(*) FROM specimens WHERE signer_id=s.id AND status='active') specimens, s.created_at "
                     "FROM signers s ORDER BY s.signer_code")
        data = csv_bytes(["id", "signer_id", "name", "bank", "account", "status", "source", "active_specimens", "created_at"],
                         [[r[0], r[1], r[2], r[3], mask_account(r[4]), r[5], r[6], r[7], r[8]] for r in rows])
    else:
        raise ApiError(404, "not_found", "Unknown export.")
    audit.record("report.export", "report", kind)
    return send_file(io.BytesIO(data), mimetype="text/csv", as_attachment=True, download_name=f"{kind}.csv")
