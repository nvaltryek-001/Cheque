"""Bulk import of a genuine-signature collection (see docs/SIGNATURE_ENROLLMENT.md).

A collection is a folder of signature images plus a manifest CSV that says, for every image, which signer wrote
it and on which day. Each (signer, day, method, instrument, consent reference) becomes one signing session and
every image is enrolled into it through the same checks as the web upload (quality, duplicates, same-signing,
pixel limit). Nothing is read from outside the images folder, and the report never contains names or images.

Manifest columns (header row required, extra columns are ignored):
    signer_code    required   e.g. P017 (3-40 letters, digits, '_', '-', '.')
    session_date   required   YYYY-MM-DD, the day the signature was written
    file           required   path relative to the images folder
    bank           required for a signer that does not exist yet (with create_signers)
    method         paper_scan | paper_photo | tablet | other   (default paper_scan)
    instrument     free text (pen, scanner...)
    consent_ref    reference of the signed consent form (required unless allow_missing_consent)
    source_ref     optional: images cut from the same document share one value
    full_name, account_number   optional, only used when the signer is created
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

from flask import current_app

from ..api.common import ApiError
from ..api.signer_routes import BANKS
from ..db import execute, now_iso, query
from . import audit, enrollment, storage

REQUIRED = ("signer_code", "session_date", "file")
SIGNER_CODE = re.compile(r"[A-Za-z0-9_.-]{3,40}")


class ManifestError(Exception):
    """The manifest as a whole cannot be used (nothing is imported)."""


def read_manifest(path: Path) -> list[dict]:
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ManifestError(f"cannot read the manifest: {exc.strerror or exc}") from exc
    reader = csv.DictReader(text.splitlines())
    header = [h.strip() for h in (reader.fieldnames or [])]
    missing = [c for c in REQUIRED if c not in header]
    if missing:
        raise ManifestError("the manifest needs the column(s): " + ", ".join(missing))
    rows = []
    for n, raw in enumerate(reader, start=2):                       # line 1 is the header
        row = {k.strip(): (v or "").strip() for k, v in raw.items() if k}
        if not any(row.values()):
            continue
        row["_line"] = n
        rows.append(row)
    if not rows:
        raise ManifestError("the manifest has no rows")
    return rows


def _problem(row: dict, base: Path, create_signers: bool, require_consent: bool) -> str | None:
    if not SIGNER_CODE.fullmatch(row.get("signer_code", "")):
        return "signer_code must be 3-40 letters, digits, '_', '-' or '.'"
    try:
        enrollment.validate_session({"session_date": row.get("session_date"), "method": row.get("method") or "paper_scan",
                                     "instrument": row.get("instrument", ""), "consent_ref": row.get("consent_ref", "")})
    except ApiError as exc:
        return exc.message
    if require_consent and not row.get("consent_ref"):
        return "consent_ref is missing (use allow_missing_consent only for test data)"
    if row.get("bank") and row["bank"].lower() not in BANKS:
        return "bank is not one of: " + ", ".join(BANKS)
    rel = row.get("file", "")
    if not rel:
        return "file is empty"
    try:
        target = (base / rel).resolve()
    except (OSError, ValueError):
        return "file path is not valid"
    if base != target and base not in target.parents:
        return "file is outside the images folder"
    ext = target.suffix.lower().lstrip(".")
    if ext not in current_app.config["ALLOWED_EXTENSIONS"]:
        return "unsupported file type"
    if not target.is_file():
        return "file not found"
    if target.stat().st_size > current_app.config["MAX_CONTENT_LENGTH"]:
        return "file exceeds the size limit"
    if query("SELECT id FROM signers WHERE signer_code=?", (row["signer_code"],), one=True) is None:
        if not create_signers:
            return "unknown signer (use create_signers to add new signers)"
        if (row.get("bank") or "").lower() not in BANKS:
            return "bank is required for a new signer"
    return None


def import_collection(manifest: Path, images_dir: Path, *, create_signers: bool = False, allow_poor_quality: bool = False,
                      allow_missing_consent: bool = False, dry_run: bool = False, user_id: int | None = None) -> dict:
    """Validate the whole manifest first; with dry_run nothing is written. Needs an application context."""
    base = Path(images_dir).resolve()
    if not base.is_dir():
        raise ManifestError("the images folder does not exist")
    rows = read_manifest(manifest)
    results, planned = [], []
    seen_files = set()
    for row in rows:
        problem = _problem(row, base, create_signers, not allow_missing_consent)
        key = (row.get("signer_code"), row.get("file"))
        if problem is None and key in seen_files:
            problem = "the same file is listed twice for this signer"
        seen_files.add(key)
        if problem:
            results.append({"line": row["_line"], "signer_code": row.get("signer_code", ""), "ok": False, "error": problem})
        else:
            planned.append(row)
    summary = {"rows": len(rows), "valid": len(planned), "invalid": len(results), "dry_run": dry_run,
               "enrolled": 0, "rejected": 0, "signers_created": 0, "sessions_created": 0, "results": results, "signers": {}}
    if dry_run:
        for row in planned:
            results.append({"line": row["_line"], "signer_code": row["signer_code"], "ok": True, "planned": True})
        results.sort(key=lambda r: r["line"])
        return summary

    signer_ids: dict[str, int] = {}
    sessions: dict[tuple, int] = {}
    refs_by_signer: dict[int, tuple[list, list]] = {}
    for row in planned:
        code = row["signer_code"]
        if code not in signer_ids:
            existing = query("SELECT id, status FROM signers WHERE signer_code=?", (code,), one=True)
            if existing is None:
                now = now_iso()
                signer_ids[code] = execute(
                    "INSERT INTO signers(signer_code, full_name, bank, account_number, source, notes, created_by, "
                    "created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (code, row.get("full_name", "")[:120], row["bank"].lower(), re.sub(r"\D", "", row.get("account_number", "")),
                     "collection", "Created by import-collection", user_id, now, now))
                summary["signers_created"] += 1
            elif existing["status"] != "active":
                results.append({"line": row["_line"], "signer_code": code, "ok": False, "error": "signer is inactive"})
                summary["rejected"] += 1
                continue
            else:
                signer_ids[code] = existing["id"]
        sid = signer_ids[code]
        skey = (sid, row["session_date"], (row.get("method") or "paper_scan").lower(), row.get("instrument", ""),
                row.get("consent_ref", ""))
        if skey not in sessions:
            sessions[skey] = enrollment.create_session(sid, {
                "session_date": row["session_date"], "method": row.get("method") or "paper_scan",
                "instrument": row.get("instrument", ""), "consent_ref": row.get("consent_ref", "")}, user_id)
            summary["sessions_created"] += 1
        if sid not in refs_by_signer:
            refs_by_signer[sid] = enrollment.load_reference_set(sid)
        refs, ref_ids = refs_by_signer[sid]
        try:
            data = (base / row["file"]).read_bytes()
            img = storage.decode_image(data)
            if img is None:
                raise ApiError(422, "corrupted_image", "The image could not be decoded.")
            enrollment.add_specimen(sid, data, img, Path(row["file"]).name, user_id, allow_poor_quality,
                                    session_id=sessions[skey], source_ref=row.get("source_ref", ""),
                                    refs=refs, ref_ids=ref_ids)
            results.append({"line": row["_line"], "signer_code": code, "ok": True})
            summary["enrolled"] += 1
        except ApiError as exc:
            results.append({"line": row["_line"], "signer_code": code, "ok": False, "error": exc.message,
                            "code": exc.code})
            summary["rejected"] += 1
    results.sort(key=lambda r: r["line"])
    for code, sid in signer_ids.items():
        rep = enrollment.readiness(sid)
        summary["signers"][code] = {"status": rep["status"], "independent_samples": rep["independent_samples"],
                                    "independent_days": rep["independent_days"], "missing": rep["missing"]}
    audit.record("collection.import", "dataset", "collection",
                 {"rows": summary["rows"], "enrolled": summary["enrolled"], "rejected": summary["rejected"],
                  "invalid": summary["invalid"],
                  "signers_created": summary["signers_created"], "sessions_created": summary["sessions_created"]})
    return summary
