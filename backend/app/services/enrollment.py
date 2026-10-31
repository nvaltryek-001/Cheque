"""Signature enrollment: specimen validation, preprocessing, duplicate detection."""
from __future__ import annotations

import json

import cv2
import numpy as np

from ..api.common import ApiError
from ..db import dumps, execute, get_setting, now_iso, query
from ..services.datasets import hamming
from ..signature.preprocess import Processed, SignatureError, perceptual_hash, preprocess
from . import storage


def process_or_fail(img: np.ndarray) -> Processed:
    try:
        return preprocess(img)
    except SignatureError as exc:
        raise ApiError(422, "invalid_signature", str(exc)) from exc


def check_duplicates(signer_id: int, sha: str, phash: str, exclude_id: int | None = None) -> None:
    rows = query("SELECT sp.id, sp.signer_id, sp.sha256, sp.phash, s.signer_code FROM specimens sp "
                 "JOIN signers s ON s.id = sp.signer_id WHERE sp.status='active'")
    limit = int(get_setting("signature.duplicate_hamming", 6))
    for r in rows:
        if exclude_id and r["id"] == exclude_id:
            continue
        same = r["sha256"] == sha or hamming(r["phash"], phash) <= limit
        if not same:
            continue
        if r["signer_id"] == signer_id:
            raise ApiError(409, "duplicate_specimen",
                           f"This image duplicates specimen #{r['id']} already enrolled for this signer.",
                           {"specimen_id": r["id"]})
        raise ApiError(409, "duplicate_other_signer",
                       f"This image is nearly identical to a specimen enrolled for signer {r['signer_code']}. "
                       "Enrollment refused – one signature cannot belong to two signers.",
                       {"specimen_id": r["id"], "signer_code": r["signer_code"]})


def add_specimen(signer_id: int, data: bytes, img: np.ndarray, filename: str, user_id: int | None,
                 allow_poor_quality: bool = False) -> int:
    active = query("SELECT COUNT(*) c FROM specimens WHERE signer_id=? AND status='active'", (signer_id,), one=True)["c"]
    max_n = int(get_setting("signature.max_specimens_per_signer", 5))
    if active >= max_n:
        raise ApiError(409, "too_many_specimens",
                       f"Signer already has {active} active specimens (maximum {max_n}). Deactivate or replace one.")
    proc = process_or_fail(img)
    if not proc.quality["acceptable"] and not allow_poor_quality:
        raise ApiError(422, "poor_quality", "Specimen quality is not acceptable for enrollment.",
                       {"quality": proc.quality})
    sha = storage.sha256(data)
    phash = perceptual_hash(proc)
    check_duplicates(signer_id, sha, phash)
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "png"
    stored = storage.save_bytes("specimens", data, ext)
    processed = storage.save_image("specimens_processed", proc.gray)
    now = now_iso()
    return execute(
        "INSERT INTO specimens(signer_id, original_filename, stored_name, processed_name, sha256, phash, quality_json, "
        "status, created_by, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (signer_id, filename[:200], stored, processed, sha, phash, dumps(proc.quality), "active",
         user_id, now, now))


def load_reference_set(signer_id: int) -> tuple[list[Processed], list[int]]:
    rows = query("SELECT * FROM specimens WHERE signer_id=? AND status='active' ORDER BY id", (signer_id,))
    refs, ids = [], []
    for r in rows:
        try:
            raw = cv2.imread(str(storage.safe_path("specimens", r["stored_name"])), cv2.IMREAD_COLOR)
            if raw is None:
                continue
            refs.append(preprocess(raw))
            ids.append(r["id"])
        except (ApiError, SignatureError):
            continue
    return refs, ids
