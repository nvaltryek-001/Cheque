"""Signature enrollment: specimen validation, preprocessing, duplicate detection, signing sessions and the
readiness of a signer's reference set.

Why sessions matter
    A verifier is only as good as the reference signatures it compares against. Five crops of one signing
    event, or one signature cut out of the very cheque being verified, say nothing about how the signer's
    hand varies between sittings. Specimens are therefore grouped into *signing sessions* (a recorded date,
    method and consent reference), and a reference set only counts as ready when it holds enough independent
    specimens from enough different days (settings enrollment.min_specimens / enrollment.min_sessions).
    The days are attested by whoever records the session – the software cannot prove when someone signed –
    but it does refuse byte-identical and near pixel-identical images (a second capture of one signing)
    and the audit log shows when each session was recorded.
"""
from __future__ import annotations

import datetime as dt
import re

import cv2
import numpy as np

from ..api.common import ApiError
from ..db import dumps, execute, get_setting, now_iso, query
from ..services.datasets import hamming
from ..signature.preprocess import Processed, SignatureError, perceptual_hash, preprocess
from . import storage

SESSION_METHODS = ("paper_scan", "paper_photo", "tablet", "other")
ORIGINS = ("upload", "dataset_import")
SAME_SIGNING_AGREEMENT = 0.98       # same threshold the verifier uses for "pixel-identical copy"
EARLIEST_SESSION = dt.date(1990, 1, 1)
MAX_SOURCE_REF = 80


# ------------------------------------------------------------------ specimen processing
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


def check_same_signing(proc: Processed, refs: list[Processed], ids: list[int] | None = None) -> None:
    """Refuse an image that is a second capture of a signing already enrolled for this signer.

    Re-scans and re-crops of one signature differ in size, margins and noise, so a perceptual hash does not
    always catch them; after alignment they agree almost pixel for pixel (>= 98 % of the ink). Separate
    signings do not – that is what the verifier relies on when it flags pasted copies. Two photocopies of one
    signing can agree less than that and pass; the signing-session record is the second line of defence."""
    from ..signature.alteration import check
    for i, ref in enumerate(refs):
        a = check(proc, ref)
        if a is None or not (a.aligned and a.near_copy) or a.agreement < SAME_SIGNING_AGREEMENT:
            continue
        sid = ids[i] if ids and i < len(ids) else None
        which = f"specimen #{sid}" if sid else "an enrolled specimen"
        raise ApiError(409, "same_signing",
                       f"This image is the same signing as {which} (ink agreement {a.agreement:.1%}). Another scan "
                       "or crop of one signature is not an independent sample – collect a new signature instead.",
                       {"specimen_id": sid, "agreement": round(a.agreement, 4)})


def add_specimen(signer_id: int, data: bytes, img: np.ndarray, filename: str, user_id: int | None,
                 allow_poor_quality: bool = False, *, session_id: int | None = None, source_ref: str = "",
                 origin: str = "upload", refs: list[Processed] | None = None,
                 ref_ids: list[int] | None = None) -> int:
    """Validate and store one specimen. `refs`/`ref_ids` let a batch load the signer's reference images once;
    the new specimen is appended to them on success."""
    active = query("SELECT COUNT(*) c FROM specimens WHERE signer_id=? AND status='active'", (signer_id,), one=True)["c"]
    max_n = int(get_setting("signature.max_specimens_per_signer", 20))
    if active >= max_n:
        raise ApiError(409, "too_many_specimens",
                       f"Signer already has {active} active specimens (maximum {max_n}). Deactivate or replace one.")
    if session_id is not None:
        session_for(signer_id, session_id)
    proc = process_or_fail(img)
    if not proc.quality["acceptable"] and not allow_poor_quality:
        raise ApiError(422, "poor_quality", "Specimen quality is not acceptable for enrollment.",
                       {"quality": proc.quality})
    sha = storage.sha256(data)
    phash = perceptual_hash(proc)
    check_duplicates(signer_id, sha, phash)
    if refs is None:
        refs, ref_ids = load_reference_set(signer_id)
    check_same_signing(proc, refs, ref_ids)
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "png"
    stored = storage.save_bytes("specimens", data, ext)
    processed = storage.save_image("specimens_processed", proc.gray)
    now = now_iso()
    new_id = execute(
        "INSERT INTO specimens(signer_id, original_filename, stored_name, processed_name, sha256, phash, quality_json, "
        "status, created_by, created_at, updated_at, session_id, source_ref, origin) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (signer_id, filename[:200], stored, processed, sha, phash, dumps(proc.quality), "active",
         user_id, now, now, session_id, clean_source_ref(source_ref), origin if origin in ORIGINS else "upload"))
    refs.append(proc)
    if ref_ids is not None:
        ref_ids.append(new_id)
    return new_id


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


def check_reactivation(specimen) -> None:
    """A deactivated specimen must pass the same-signing check against the active ones before it counts again."""
    raw = cv2.imread(str(storage.safe_path("specimens", specimen["stored_name"])), cv2.IMREAD_COLOR)
    if raw is None:
        return
    try:
        proc = preprocess(raw)
    except SignatureError:
        return
    refs, ids = load_reference_set(specimen["signer_id"])
    keep = [(r, i) for r, i in zip(refs, ids) if i != specimen["id"]]
    check_same_signing(proc, [r for r, _ in keep], [i for _, i in keep])


# ------------------------------------------------------------------ signing sessions
def clean_source_ref(value) -> str:
    ref = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(ref) > MAX_SOURCE_REF:
        raise ApiError(400, "validation_error", f"source_ref can have at most {MAX_SOURCE_REF} characters.")
    return ref


def validate_session(data: dict, partial: bool = False) -> dict:
    out: dict = {}
    if "session_date" in data or not partial:
        raw = str(data.get("session_date") or "").strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            raise ApiError(400, "validation_error", "session_date must be a date written YYYY-MM-DD (the day the "
                                                    "signatures were written).")
        try:
            day = dt.date.fromisoformat(raw)
        except ValueError as exc:
            raise ApiError(400, "validation_error", "session_date is not a valid calendar date.") from exc
        if day > dt.datetime.now(dt.timezone.utc).date() + dt.timedelta(days=1):
            raise ApiError(400, "validation_error", "session_date cannot be in the future.")
        if day < EARLIEST_SESSION:
            raise ApiError(400, "validation_error", f"session_date cannot be before {EARLIEST_SESSION.isoformat()}.")
        out["session_date"] = raw
    if "method" in data or not partial:
        method = str(data.get("method") or "paper_scan").strip().lower()
        if method not in SESSION_METHODS:
            raise ApiError(400, "validation_error", f"method must be one of: {', '.join(SESSION_METHODS)}.")
        out["method"] = method
    for key, limit in (("instrument", 80), ("consent_ref", 80), ("notes", 500)):
        if key in data:
            value = str(data.get(key) or "").strip()
            if len(value) > limit:
                raise ApiError(400, "validation_error", f"{key} can have at most {limit} characters.")
            out[key] = value
    return out


def session_row(session_id: int):
    row = query("SELECT * FROM enrollment_sessions WHERE id=?", (session_id,), one=True)
    if row is None:
        raise ApiError(404, "not_found", "Signing session not found.")
    return row


def session_for(signer_id: int, session_id: int):
    """The session, which must belong to this signer (a specimen can never borrow another signer's session)."""
    try:
        sid = int(session_id)
    except (TypeError, ValueError) as exc:
        raise ApiError(400, "validation_error", "session_id must be an integer.") from exc
    row = session_row(sid)
    if row["signer_id"] != signer_id:
        raise ApiError(400, "validation_error", "That signing session belongs to a different signer.")
    return row


def create_session(signer_id: int, data: dict, user_id: int | None) -> int:
    v = validate_session(data)
    return execute("INSERT INTO enrollment_sessions(signer_id, session_date, method, instrument, consent_ref, notes, "
                   "created_by, created_at) VALUES(?,?,?,?,?,?,?,?)",
                   (signer_id, v["session_date"], v["method"], v.get("instrument", ""), v.get("consent_ref", ""),
                    v.get("notes", ""), user_id, now_iso()))


def list_sessions(signer_id: int) -> list[dict]:
    rows = query("SELECT es.*, "
                 "(SELECT COUNT(*) FROM specimens sp WHERE sp.session_id=es.id AND sp.status='active') specimens_active, "
                 "(SELECT COUNT(*) FROM specimens sp WHERE sp.session_id=es.id) specimens_total "
                 "FROM enrollment_sessions es WHERE es.signer_id=? ORDER BY es.session_date DESC, es.id DESC",
                 (signer_id,))
    return [dict(r) for r in rows]


# ------------------------------------------------------------------ reference-set readiness
def policy() -> dict:
    return {"min_specimens": max(1, int(get_setting("enrollment.min_specimens", 5))),
            "min_sessions": max(1, int(get_setting("enrollment.min_sessions", 2))),
            "cap_match_when_not_ready": bool(get_setting("enrollment.cap_match_when_not_ready", True))}


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def assess(rows, pol: dict) -> dict:
    """Is this set of ACTIVE specimens a reliable reference? `rows` need id, session_id, session_date, source_ref.

    * a specimen counts only if it belongs to a recorded signing session (no session = unknown provenance);
    * specimens that share a non-empty source_ref (cut from the same document) count once;
    * the signing days are read from the independent samples only.
    Ready = enough independent samples AND enough different signing days."""
    rows = list(rows)
    sessioned = sorted((r for r in rows if r["session_id"] is not None and r["session_date"]), key=lambda r: r["id"])
    groups: dict = {}
    for r in sessioned:
        ref = re.sub(r"\s+", " ", r["source_ref"] or "").strip().lower()
        groups.setdefault(("ref", ref) if ref else ("id", r["id"]), []).append(r)
    reps = [g[0] for g in groups.values()]
    days = sorted({r["session_date"] for r in reps})
    samples = len(reps)
    unsessioned = len(rows) - len(sessioned)
    merged = len(sessioned) - samples
    need_samples = max(0, pol["min_specimens"] - samples)
    need_days = max(0, pol["min_sessions"] - len(days))
    ready = need_samples == 0 and need_days == 0
    missing, notes = [], []
    if need_samples:
        missing.append(f"{_plural(need_samples, 'more independent specimen')}")
    if need_days:
        missing.append(f"specimens from {_plural(need_days, 'more signing day')}")
    if unsessioned:
        notes.append(f"{_plural(unsessioned, 'active specimen')} not linked to a signing session "
                     f"{'does' if unsessioned == 1 else 'do'} not count (unknown provenance).")
    if merged:
        notes.append(f"{_plural(merged, 'specimen')} from the same source as another count once.")
    return {"ready": ready, "status": "ready" if ready else ("none" if not rows else "incomplete"),
            "active_specimens": len(rows), "independent_samples": samples, "independent_days": len(days),
            "session_dates": days, "unsessioned_specimens": unsessioned, "merged_specimens": merged,
            "missing": missing, "notes": notes, "policy": pol}


def _active_rows(signer_ids: list[int]) -> list:
    """Active specimens of the given signers with their session day (queried in chunks: SQLite limits the number
    of bound parameters of one statement)."""
    rows: list = []
    for i in range(0, len(signer_ids), 500):
        chunk = signer_ids[i:i + 500]
        marks = ",".join("?" * len(chunk))
        rows += query("SELECT sp.id, sp.signer_id, sp.session_id, sp.source_ref, es.session_date FROM specimens sp "
                      "LEFT JOIN enrollment_sessions es ON es.id = sp.session_id "
                      f"WHERE sp.status='active' AND sp.signer_id IN ({marks})", chunk)
    return rows


def readiness_for(signer_ids: list[int]) -> dict[int, dict]:
    """Readiness of many signers with a single query (for lists and the overview)."""
    pol = policy()
    by_signer: dict[int, list] = {sid: [] for sid in signer_ids}
    for r in _active_rows(list(signer_ids)):
        by_signer[r["signer_id"]].append(r)
    return {sid: assess(rows, pol) for sid, rows in by_signer.items()}


def readiness(signer_id: int) -> dict:
    out = readiness_for([signer_id])[signer_id]
    out["signer_id"] = signer_id
    out["sessions"] = list_sessions(signer_id)
    out["consent_recorded"] = bool(out["sessions"]) and all(s["consent_ref"] for s in out["sessions"])
    return out


def overview() -> dict:
    """Counts only: the signer list (GET /api/signers) carries the per-signer status, paginated."""
    ids = [r["id"] for r in query("SELECT id FROM signers")]
    counts = {"ready": 0, "incomplete": 0, "none": 0}
    for rep in readiness_for(ids).values():
        counts[rep["status"]] += 1
    return {"policy": policy(), "signers": len(ids), **{f"signers_{k}": v for k, v in counts.items()}}


REFERENCE_KEYS = ("ready", "status", "active_specimens", "independent_samples", "independent_days",
                  "unsessioned_specimens", "merged_specimens", "missing", "policy")


def apply_reference_policy(result: dict, signer_id: int) -> dict:
    """Attach the reference-set report to a verification result and, when the set is not ready, refuse to
    issue a MATCH (it becomes REVIEW). Only the accepting direction is capped: wrongly accepting a forgery is
    the costly error, and a MISMATCH – including a detected copy-and-alter – stays a MISMATCH, with a note
    that few references cannot capture natural variation."""
    rep = readiness_for([signer_id])[signer_id]
    result["reference_set"] = {k: rep[k] for k in REFERENCE_KEYS}
    if rep["ready"]:
        return result
    pol = rep["policy"]
    need = ("; ".join(rep["missing"]) or "more independent specimens")
    if result.get("decision") == "MATCH" and pol["cap_match_when_not_ready"]:
        result["uncapped_decision"] = "MATCH"
        result["decision"] = "REVIEW"
        result["capped_by_policy"] = True
        result.setdefault("reasons", []).insert(
            0, f"Capped from MATCH to REVIEW: this signer's reference set is not ready (needs {need}). A MATCH "
               f"requires at least {pol['min_specimens']} independent specimens from at least {pol['min_sessions']} "
               "different signing days.")
    elif result.get("decision") == "MISMATCH":
        result.setdefault("reasons", []).append(
            f"Reference set not ready (needs {need}): few or related specimens cannot capture the signer's natural "
            "variation, so a MISMATCH here may be a genuine signature.")
    else:
        result.setdefault("reasons", []).append(f"Reference set not ready (needs {need}).")
    return result
