"""Cheque processing service shared by single upload, batch upload and re-processing."""
from __future__ import annotations

import json

import cv2
import numpy as np

from ..db import dumps, execute, get_setting, now_iso, query, row_to_dict
from ..ocr.fields import FIELD_LABELS, FIELD_ORDER, extract_cheque
from ..ocr.quality import assess_cheque
from ..signature.detect import find_signature
from . import models, storage
from .audit import mask_account

JSON_FIELDS = ("quality_json", "fields_json", "corrected_json", "validation_json")


def run_pipeline(img: np.ndarray) -> dict:
    quality = assess_cheque(img)
    result = extract_cheque(img, models.ocr_engine(), models.micr_model())
    crop_name, crop_info = None, {"found": False}
    if get_setting("ocr.auto_signature_crop", True):
        crop, crop_info = find_signature(img)
        if crop is not None:
            crop_name = storage.save_image("crops", crop)
    result["signature"] = crop_info
    result["quality"] = quality
    result["crop_name"] = crop_name
    return result


def status_for(result: dict) -> str:
    return "needs_review" if result["needs_review"] or not result["quality"]["acceptable"] else "processed"


def create_from_upload(data: bytes, img: np.ndarray, filename: str, user_id: int | None, batch_id: str | None = None) -> int:
    stored = storage.save_bytes("cheques", data, filename.rsplit(".", 1)[-1].lower() if "." in filename else "png")
    result = run_pipeline(img)
    h, w = img.shape[:2]
    validation = {**result["validation"], "micr": result["micr"], "signature_detection": result["signature"]}
    now = now_iso()
    return execute(
        "INSERT INTO cheques(original_filename, stored_name, sha256, width, height, status, ocr_engine, quality_json, "
        "fields_json, corrected_json, validation_json, raw_text, signature_crop_name, batch_id, processing_ms, created_by, "
        "created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (filename[:200], stored, storage.sha256(data), w, h, status_for(result), result["engine"],
         dumps(result["quality"]), dumps(result["fields"]), dumps({}), dumps(validation),
         result["raw_text"][:20000], result["crop_name"], batch_id, result["processing_ms"], user_id, now, now))


def reprocess(cheque_id: int) -> None:
    row = query("SELECT * FROM cheques WHERE id=?", (cheque_id,), one=True)
    img = storage.load_image("cheques", row["stored_name"])
    result = run_pipeline(img)
    storage.delete("crops", row["signature_crop_name"])
    validation = {**result["validation"], "micr": result["micr"], "signature_detection": result["signature"]}
    status = row["status"] if row["status"] == "reviewed" else status_for(result)
    execute("UPDATE cheques SET fields_json=?, validation_json=?, quality_json=?, raw_text=?, signature_crop_name=?, "
            "ocr_engine=?, processing_ms=?, status=?, updated_at=? WHERE id=?",
            (dumps(result["fields"]), dumps(validation), dumps(result["quality"]),
             result["raw_text"][:20000], result["crop_name"], result["engine"], result["processing_ms"],
             status, now_iso(), cheque_id))


def final_values(d: dict) -> dict:
    fields = d.get("fields") or {}
    corrected = d.get("corrected") or {}
    return {k: corrected.get(k, (fields.get(k) or {}).get("value", "")) for k in FIELD_ORDER}


def serialize(row, include_raw: bool = False) -> dict:
    d = row_to_dict(row, JSON_FIELDS)
    if not include_raw:
        d.pop("raw_text", None)
    d.pop("stored_name", None)
    d["has_signature_crop"] = bool(d.pop("signature_crop_name", None))
    d["final"] = final_values(d)
    d["field_labels"] = FIELD_LABELS
    d["field_order"] = FIELD_ORDER
    return d


def summary(row) -> dict:
    d = row_to_dict(row, JSON_FIELDS)
    final = final_values(d)
    return {
        "id": d["id"], "original_filename": d["original_filename"], "status": d["status"],
        "created_at": d["created_at"], "bank_name": final["bank_name"], "cheque_number": final["cheque_number"],
        "payee_name": final["payee_name"], "amount_figures": final["amount_figures"],
        "account_number_masked": mask_account(final["account_number"]),
        "quality_score": (d.get("quality") or {}).get("score"), "batch_id": d["batch_id"],
        "review_count": sum(1 for f in (d.get("fields") or {}).values() if f.get("status") != "ok"),
    }


def match_signer_by_account(account: str):
    if not account:
        return None
    return query("SELECT * FROM signers WHERE account_number=? AND status='active'", (account,), one=True)


def encode_png(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", img)
    return buf.tobytes() if ok else b""
