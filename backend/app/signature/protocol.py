"""A valid evaluation protocol for the signature comparator (docs/SIGNATURE_EVALUATION_PROTOCOL.md).

What makes an evaluation of a signature verifier valid, and what this module enforces
  * the people in the test set are not people the model was trained or tuned on (split by SIGNER);
  * the genuine test signatures are independent of the references: written on LATER days than every
    reference specimen, never cut from the same document, never a re-capture of a reference;
  * the references are built the way the deployed system builds them (>= N independent specimens from
    >= M days), so the numbers describe the deployed policy and not a single-specimen shortcut;
  * forgeries are real attempts by other people (skilled forgery) or other signers' genuine signatures
    (random forgery). Synthetic edits of a specimen are NOT forgeries: they are reported separately as
    "edited-copy detection" and never enter a forgery metric;
  * uncertainty is shown: resampling is done per signer, because the pairs of one signer are not independent;
  * thresholds are chosen on the validation split and the test split is used once: a ledger records every
    final run and warns when a test set is reused for another model;
  * a metric the data cannot support is reported as NOT MEASURABLE together with the reason, never as a number.

Evaluation manifest (CSV, kept outside git). Columns:
    file          path relative to the images folder (required)
    signer_id     the person the signature belongs to / is meant to imitate (required)
    kind          genuine | skilled_forgery | synthetic_alteration (required)
    session_date  YYYY-MM-DD the day it was written (required for genuine)
    forger_id     who made the forgery (required for skilled_forgery; must differ from signer_id)
    source_ref    same non-empty value = cut from the same document / the same signing
    derived_from  synthetic_alteration only: the genuine file it was made from
    split         train | val | test (optional; otherwise assigned per signer, deterministically)
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import warnings
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

import cv2
import numpy as np
from PIL import Image

from .alteration import check as alteration_check
from .compare import decide
from .preprocess import Processed, SignatureError, perceptual_hash, preprocess
from .training import choose_thresholds

PROTOCOL_VERSION = "1"
KINDS = ("genuine", "skilled_forgery", "synthetic_alteration")
SPLITS = ("train", "val", "test")
IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "bmp", "tif", "tiff", "webp"}
MAX_PIXELS = 25_000_000      # same ceiling as the upload routes: a small file can declare a huge image
DEFAULT_CFG = {
    "seed": 20261101,
    "fractions": (0.6, 0.2, 0.2),          # signers per split when the manifest gives no split
    "reference_min_specimens": 5,           # the deployed readiness policy (settings enrollment.*)
    "reference_min_sessions": 2,
    "min_queries": 3,                       # genuine queries (later days) a signer needs to be evaluated
    "min_signers": 30,                      # below this the result is underpowered (still reported, with a warning)
    "min_signers_hard": 5,                  # below this nothing is measurable
    "forgery_min_per_signer": 3,
    "forgery_min_forgers": 2,
    "random_forgeries_per_signer": 5,
    "bootstrap": 1000,
    "same_signing_agreement": 0.98,         # aligned ink agreement at which a query counts as a copy of a reference
    "duplicate_hamming": 6,
    "target_far": 0.01,                     # used only to SUGGEST thresholds from the validation split
    "target_frr": 0.05,
}
SIGNER = re.compile(r"[A-Za-z0-9_.-]{1,60}")
DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
# Checks that invalidate the whole run; the others only make the metrics they name "not measurable".
FATAL = {"signer_level_split", "no_duplicate_images_across_people", "model_has_not_seen_these_signers",
         "independent_genuine_signers", "test_set_used_once"}
METRICS = ("genuine_match_rate", "genuine_review_rate", "genuine_mismatch_rate",
           "random_forgery_match_rate", "random_forgery_review_rate", "random_forgery_mismatch_rate",
           "random_forgery_auc", "random_forgery_eer",
           "skilled_forgery_match_rate", "skilled_forgery_review_rate", "skilled_forgery_mismatch_rate",
           "skilled_forgery_auc", "skilled_forgery_eer")


def signer_hash(signer_id: str) -> str:
    """Identifies a signer in a model file without storing the ID itself."""
    return hashlib.sha256(f"signer|{signer_id}".encode()).hexdigest()[:16]


# ------------------------------------------------------------------ data
@dataclass
class Item:
    row: int
    file: str
    path: Path
    signer_id: str
    kind: str
    sha: str
    session_date: str = ""
    forger_id: str = ""
    source_ref: str = ""
    derived_from: str = ""
    split: str = ""


def load_manifest(manifest: Path, images_dir: Path) -> tuple[list[Item], list[dict]]:
    """Usable rows and the row-level problems (line number, message). A bad row never stops the others."""
    base = Path(images_dir).resolve()
    if not base.is_dir():
        raise ValueError("the images folder does not exist")
    try:
        text = Path(manifest).read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ValueError(f"cannot read the manifest: {exc.strerror or exc}") from exc
    reader = csv.DictReader(text.splitlines())
    header = [h.strip() for h in (reader.fieldnames or [])]
    missing = [c for c in ("file", "signer_id", "kind") if c not in header]
    if missing:
        raise ValueError("the evaluation manifest needs the column(s): " + ", ".join(missing))
    items: list[Item] = []
    problems: list[dict] = []
    listed: set[str] = set()
    for n, raw in enumerate(reader, start=2):
        row = {k.strip(): (v or "").strip() for k, v in raw.items() if k}
        if not any(row.values()):
            continue
        problem = _row_problem(row, base)
        if problem is None and row["file"] in listed:
            problem = "the same file is listed twice"
        if problem:
            problems.append({"line": n, "message": problem})
            continue
        listed.add(row["file"])
        path = (base / row["file"]).resolve()
        items.append(Item(n, row["file"], path, row["signer_id"], row["kind"], hashlib.sha256(path.read_bytes()).hexdigest(),
                          row.get("session_date", ""), row.get("forger_id", ""), row.get("source_ref", ""),
                          row.get("derived_from", ""), row.get("split", "")))
    genuine_of = {(i.signer_id, i.file) for i in items if i.kind == "genuine"}
    kept = []
    for it in items:
        if it.kind == "synthetic_alteration" and (it.signer_id, it.derived_from) not in genuine_of:
            problems.append({"line": it.row, "message": "derived_from is not a genuine file of this signer"})
        else:
            kept.append(it)
    problems.sort(key=lambda p: p["line"])
    return kept, problems


def _row_problem(row: dict, base: Path) -> str | None:
    if not SIGNER.fullmatch(row.get("signer_id", "")):
        return "signer_id is empty or has unusual characters"
    kind = row.get("kind", "")
    if kind not in KINDS:
        return f"kind must be one of: {', '.join(KINDS)}"
    if row.get("split") and row["split"] not in SPLITS:
        return "split must be train, val or test"
    if kind == "genuine":
        try:
            if not DAY.fullmatch(row.get("session_date", "")):
                raise ValueError
            date.fromisoformat(row["session_date"])
        except ValueError:
            return "a genuine signature needs session_date (YYYY-MM-DD)"
    if kind == "skilled_forgery":
        if not row.get("forger_id"):
            return "a skilled forgery needs forger_id"
        if row["forger_id"] == row["signer_id"]:
            return "forger_id equals signer_id: a signer imitating themselves is not a forgery"
    if kind == "synthetic_alteration" and not row.get("derived_from"):
        return "a synthetic alteration needs derived_from"
    rel = row.get("file", "")
    if not rel:
        return "file is empty"
    try:
        target = (base / rel).resolve()
    except (OSError, ValueError):
        return "file path is not valid"
    if base not in target.parents:
        return "file is outside the images folder"
    if target.suffix.lower().lstrip(".") not in IMAGE_EXTENSIONS:
        return "unsupported file type"
    if not target.is_file():
        return "file not found"
    return None


# ------------------------------------------------------------------ splits and per-signer plans
def assign_splits(items: list[Item], cfg: dict | None = None) -> tuple[dict[str, str], list[str]]:
    """signer -> split. A split given in the manifest is honoured (a signer in two splits is reported); the rest
    are assigned deterministically from a hash of seed|signer, so the assignment is reproducible and does not
    depend on file order."""
    c = {**DEFAULT_CFG, **(cfg or {})}
    declared: dict[str, set[str]] = defaultdict(set)
    for it in items:
        if it.split:
            declared[it.signer_id].add(it.split)
    problems = [f"a signer appears in more than one split ({', '.join(sorted(v))})"
                for s, v in sorted(declared.items()) if len(v) > 1]
    signers = sorted({it.signer_id for it in items})
    out = {s: sorted(declared[s])[0] for s in signers if declared.get(s)}
    free = sorted((s for s in signers if s not in out), key=lambda s: hashlib.sha256(f"{c['seed']}|{s}".encode()).hexdigest())
    n = len(free)
    n_train, n_val = round(n * c["fractions"][0]), round(n * c["fractions"][1])
    if n >= 3:
        n_val = max(1, n_val)
        n_train = min(n_train, n - n_val - 1)
    for i, s in enumerate(free):
        out[s] = "train" if i < n_train else ("val" if i < n_train + n_val else "test")
    return out, problems


def _independence_key(it: Item) -> tuple[str, str]:
    ref = re.sub(r"\s+", " ", it.source_ref.strip().lower())
    return ("ref", ref) if ref else ("sha", it.sha)


def plan_signer(genuine: list[Item], cfg: dict) -> dict:
    """References = the thinnest set the deployed policy accepts as 'ready', spread over the earliest days;
    queries = independent genuine specimens written on days AFTER every reference day. Specimens that share a
    sitting with the references are not used at all, and specimens from the same source count once."""
    seen, independent = set(), []
    for it in sorted(genuine, key=lambda i: (i.session_date, i.file)):
        key = _independence_key(it)
        if key not in seen:
            seen.add(key)
            independent.append(it)
    by_day: dict[str, list[Item]] = defaultdict(list)
    for it in independent:
        by_day[it.session_date].append(it)
    days = sorted(by_day)
    plan = {"independent": len(independent), "days": len(days), "references": [], "queries": [], "unused": 0, "reason": None}
    need_n, need_d = cfg["reference_min_specimens"], cfg["reference_min_sessions"]
    if len(independent) < need_n or len(days) < need_d:
        plan["reason"] = "references"
        return plan
    pools = [list(by_day[d]) for d in days[:need_d]]
    refs: list[Item] = []
    while len(refs) < need_n and any(pools):                # round-robin over the earliest reference days
        for pool in pools:
            if pool and len(refs) < need_n:
                refs.append(pool.pop(0))
    for d in days[need_d:]:                                 # the earliest days were too thin: continue with later days
        for it in by_day[d]:
            if len(refs) < need_n:
                refs.append(it)
    last_day = max(i.session_date for i in refs)
    queries = [i for i in independent if i.session_date > last_day]
    plan["references"], plan["queries"] = refs, queries
    plan["unused"] = len(independent) - len(refs) - len(queries)
    if len(queries) < cfg["min_queries"]:
        plan["reason"] = "queries"
    return plan


def eligible(plan: dict) -> bool:
    return plan["reason"] is None


# ------------------------------------------------------------------ statistics
def auc(pos, neg) -> float | None:
    """P(genuine score > negative score), ties counting one half."""
    pos, neg = np.asarray(pos, dtype=float), np.asarray(neg, dtype=float)
    if not len(pos) or not len(neg):
        return None
    ns = np.sort(neg)
    wins = (np.searchsorted(ns, pos, side="left") + np.searchsorted(ns, pos, side="right")) / 2
    return float(wins.sum() / (len(pos) * len(neg)))


def eer(pos, neg) -> float | None:
    """Equal error rate of 'accept when score >= t': genuine rejected vs negatives accepted."""
    pos, neg = np.sort(np.asarray(pos, dtype=float)), np.sort(np.asarray(neg, dtype=float))
    if not len(pos) or not len(neg):
        return None
    ts = np.unique(np.concatenate([pos, neg]))
    frr = np.searchsorted(pos, ts, side="left") / len(pos)
    far = 1 - np.searchsorted(neg, ts, side="left") / len(neg)
    i = int(np.argmin(np.abs(far - frr)))
    return float((far[i] + frr[i]) / 2)


def upper_bound_no_events(n: int, confidence: float = 0.95) -> float | None:
    """Exact upper confidence bound for a proportion when none of n independent units had the event."""
    return None if n <= 0 else float(1 - (1 - confidence) ** (1 / n))


def _rng(cfg: dict, tag: str) -> np.random.Generator:
    return np.random.default_rng(int(hashlib.sha256(f"{cfg['seed']}|{tag}".encode()).hexdigest()[:8], 16))


def rate_with_ci(per_signer: dict[str, tuple[int, int]], cfg: dict, tag: str) -> dict:
    """Pooled rate events/trials with a 95 % interval from resampling SIGNERS (trials of one signer are correlated)."""
    sig = [(e, t) for e, t in per_signer.values() if t > 0]
    events, trials = sum(e for e, _ in sig), sum(t for _, t in sig)
    if not trials:
        return {"measurable": False, "reason": "no trials"}
    out = {"measurable": True, "value": round(events / trials, 4), "events": events, "trials": trials,
           "signers": len(sig), "ci95": None}
    if len(sig) >= 5:
        e, t = np.array([x[0] for x in sig], float), np.array([x[1] for x in sig], float)
        idx = _rng(cfg, tag).integers(0, len(sig), size=(cfg["bootstrap"], len(sig)))
        den = t[idx].sum(axis=1)
        vals = e[idx].sum(axis=1) / np.maximum(den, 1)
        out["ci95"] = [round(float(np.percentile(vals, 2.5)), 4), round(float(np.percentile(vals, 97.5)), 4)]
    if events == 0:
        out["upper95_share_of_signers_with_an_event"] = round(upper_bound_no_events(len(sig)), 4)
    return out


def score_metric(per_signer: dict[str, dict], neg_key: str, fn: Callable, cfg: dict, tag: str) -> dict:
    """AUC / EER of genuine scores against one negative class, with a signer-level bootstrap interval."""
    signers = [s for s, d in per_signer.items() if d["genuine"] and d[neg_key]]
    pos = [x for s in signers for x in per_signer[s]["genuine"]]
    neg = [x for s in signers for x in per_signer[s][neg_key]]
    value = fn(pos, neg)
    if value is None:
        return {"measurable": False, "reason": "no scored pairs"}
    out = {"measurable": True, "value": round(value, 4), "signers": len(signers), "ci95": None}
    if len(signers) >= 5:
        rng = _rng(cfg, tag)
        vals = []
        for _ in range(cfg["bootstrap"]):
            pick = rng.integers(0, len(signers), len(signers))
            v = fn([x for i in pick for x in per_signer[signers[i]]["genuine"]],
                   [x for i in pick for x in per_signer[signers[i]][neg_key]])
            if v is not None:
                vals.append(v)
        if vals:
            out["ci95"] = [round(float(np.percentile(vals, 2.5)), 4), round(float(np.percentile(vals, 97.5)), 4)]
    return out


# ------------------------------------------------------------------ ledger of final test-set use
def split_digest(items: list[Item], signer_split: dict[str, str], split: str) -> str:
    """Identifies a test set by its people, kinds and files – not by where the folder happens to be."""
    names = sorted(f"{it.signer_id}|{it.kind}|{it.file}" for it in items if signer_split.get(it.signer_id) == split)
    return hashlib.sha256("\n".join(names).encode()).hexdigest()[:16]


def read_ledger(path: Path | None) -> list[dict]:
    if path is None or not Path(path).is_file():
        return []
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def write_ledger(path: Path, entries: list[dict]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(entries, indent=2))


# ------------------------------------------------------------------ validity checks
def _check(cid: str, status: str, message: str, **extra) -> dict:
    return {"id": cid, "status": status, "message": message, "scope": "run" if cid in FATAL else "metric", **extra}


def _identical_images_across_people(items: list[Item]) -> int:
    owners: dict[str, set[str]] = defaultdict(set)
    for it in items:
        owners[it.sha].add(it.signer_id)
    return sum(1 for v in owners.values() if len(v) > 1)


def _population_checks(items, signer_split, split_problems, split, plans, in_split, ok_signers, good_forgery, forged,
                       model_hashes, c) -> list[dict]:
    checks = [_check("signer_level_split", "fail" if split_problems else "pass",
                     "; ".join(split_problems) or "every signer belongs to exactly one split")]
    shared = _identical_images_across_people(items)
    checks.append(_check("no_duplicate_images_across_people", "fail" if shared else "pass",
                         f"{shared} image(s) are listed for more than one signer" if shared
                         else "no image appears under two different signers"))
    if model_hashes is None:
        checks.append(_check("model_has_not_seen_these_signers", "warn",
                             "the model does not record which signers it was trained and tuned on (it predates this protocol): "
                             "the result is only valid if these signers were collected after the model was trained and tuned"))
    else:
        seen = [s for s in in_split if signer_hash(s) in model_hashes]
        checks.append(_check("model_has_not_seen_these_signers", "fail" if seen else "pass",
                             f"{len(seen)} evaluated signer(s) were used to train or tune the model" if seen
                             else "no evaluated signer was used to train or tune the model"))
    n_ok = len(ok_signers)
    why = Counter(plans[s]["reason"] for s in in_split if not eligible(plans[s]))
    ineligible = {"too few independent specimens or days for the reference set": why.get("references", 0),
                  "too few genuine specimens written after the reference days": why.get("queries", 0)}
    ineligible = {k: v for k, v in ineligible.items() if v}
    need = (f"{c['reference_min_specimens']} independent specimens on {c['reference_min_sessions']}+ days for the references "
            f"and {c['min_queries']}+ genuine specimens written on later days")
    if n_ok < c["min_signers_hard"]:
        checks.append(_check("independent_genuine_signers", "fail",
                             f"{n_ok} of {len(in_split)} signers in '{split}' qualify ({need}); at least {c['min_signers_hard']} "
                             "are needed to measure anything", ineligible=ineligible))
    elif n_ok < c["min_signers"]:
        checks.append(_check("independent_genuine_signers", "warn",
                             f"{n_ok} qualifying signers: underpowered (< {c['min_signers']}). Intervals are wide and the result "
                             "must not be generalised to a population", ineligible=ineligible))
    else:
        checks.append(_check("independent_genuine_signers", "pass", f"{n_ok} qualifying signers", ineligible=ineligible))
    unused = sum(plans[s]["unused"] for s in ok_signers)
    checks.append(_check("queries_are_independent_of_references", "pass" if n_ok else "na",
                         "every genuine query was written on a later day than every reference specimen; "
                         f"{unused} specimen(s) that share a sitting with the references were left out and "
                         "same-source duplicates count once"))
    n_skilled = sum(len(v) for v in forged.values())
    if len(good_forgery) >= c["min_signers_hard"]:
        status = "pass" if len(good_forgery) >= c["min_signers"] else "warn"
        checks.append(_check("skilled_forgeries", status,
                             f"{len(good_forgery)} signers have >= {c['forgery_min_per_signer']} skilled forgeries by "
                             f">= {c['forgery_min_forgers']} different people" + ("" if status == "pass" else " (underpowered)")))
    else:
        checks.append(_check("skilled_forgeries", "fail",
                             f"{len(good_forgery)} of {n_ok} qualifying signers have >= {c['forgery_min_per_signer']} skilled "
                             f"forgeries by >= {c['forgery_min_forgers']} different people ({n_skilled} skilled forgeries in total): "
                             "false acceptance of skilled forgeries is NOT MEASURABLE"))
    n_synth = sum(1 for it in items if it.kind == "synthetic_alteration" and signer_split.get(it.signer_id) == split)
    checks.append(_check("synthetic_alterations_kept_apart", "pass" if n_synth else "na",
                         f"{n_synth} synthetic alteration(s) are excluded from every forgery metric: they are edits of a specimen, "
                         "not attempts by another person"))
    return checks


def _ledger_check(split: str, final: bool, digest: str, model_version: str, ledger: list[dict], keeps_ledger: bool) -> dict:
    if split != "test":
        return _check("test_set_used_once", "na", "exploratory run on the validation split")
    if not final:
        return _check("test_set_used_once", "fail",
                      "the test split is only for the final run (use --final); development runs must use 'val'")
    if not keeps_ledger:
        return _check("test_set_used_once", "warn",
                      "no ledger is kept for this run, so a second look at this test set cannot be detected")
    earlier = [e for e in ledger if e.get("digest") == digest and e.get("final")]
    others = {e.get("model_version") for e in earlier} - {model_version}
    if others:
        return _check("test_set_used_once", "warn",
                      f"this test set has already been used to evaluate {len(others)} other model version(s): choosing or tuning "
                      "a model on these results makes them optimistic")
    if earlier:
        return _check("test_set_used_once", "pass", "this model version was already evaluated on this test set; a repeat gives the same numbers")
    return _check("test_set_used_once", "pass", "first final use of this test set")


# ------------------------------------------------------------------ running the evaluation
def default_decider(model, thresholds: dict | None = None) -> Callable:
    def run(query: Processed, refs: list[Processed]) -> dict:
        r = decide(query, refs, model, thresholds or {})
        return {"decision": r["decision"], "score": r["score"]}
    return run


def _read_image(path: Path) -> np.ndarray | None:
    """cv2.imread, after checking the size the header declares (a 40 KB PNG can declare hundreds of megapixels)."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            with Image.open(path) as im:
                if im.size[0] * im.size[1] > MAX_PIXELS:
                    return None
    except Image.DecompressionBombError:
        return None
    except Exception:                                   # a format Pillow does not know: let OpenCV decide
        pass
    return cv2.imread(str(path), cv2.IMREAD_COLOR)


def _load(items: list[Item], dropped: Counter, label: str) -> list[dict]:
    out = []
    for it in items:
        img = _read_image(it.path)
        try:
            if img is None:
                raise SignatureError("unreadable")
            proc = preprocess(img)
        except SignatureError:
            dropped[f"{label}: unreadable or unusable image"] += 1
            continue
        out.append({"item": it, "proc": proc, "phash": perceptual_hash(proc)})
    return out


def _hamming(a: str, b: str) -> int:
    """perceptual_hash() is a string of '0'/'1' characters, not hexadecimal."""
    return sum(x != y for x, y in zip(a, b))


def _is_copy_of_any(query: dict, refs: list[dict], cfg: dict) -> bool:
    for r in refs:
        if _hamming(query["phash"], r["phash"]) <= cfg["duplicate_hamming"]:
            return True
        a = alteration_check(query["proc"], r["proc"])
        if a is not None and a.aligned and a.near_copy and a.agreement >= cfg["same_signing_agreement"]:
            return True
    return False


def _pick_random_forgeries(ok_signers: list[str], plans: dict, c: dict) -> dict[str, list[Item]]:
    """For every signer a few genuine queries of OTHER signers (zero-effort forgeries), chosen reproducibly."""
    flat = [(o, q) for o in ok_signers for q in plans[o]["queries"]]
    rng = _rng(c, "random-forgery")
    picks = {}
    for s in ok_signers:
        pool = [q for o, q in flat if o != s]
        order = rng.permutation(len(pool))[:c["random_forgeries_per_signer"]]
        picks[s] = [pool[i] for i in order]
    return picks


def _score_signers(ok_signers, plans, forged, good_forgery, decider, c, dropped, say) -> tuple[dict, int]:
    """One signer at a time, so memory holds a single signer's images. Returns the outcomes per signer and the number
    of genuine queries removed because they were copies of a reference."""
    random_picks = _pick_random_forgeries(ok_signers, plans, c)
    outcomes: dict[str, dict] = {}
    copies = 0
    for n, s in enumerate(ok_signers, start=1):
        if n % 10 == 0:
            say(f"scored {n}/{len(ok_signers)} signers")
        refs = _load(plans[s]["references"], dropped, "reference")
        if len(refs) < c["reference_min_specimens"]:
            dropped["signer skipped: too few readable references"] += 1
            continue
        queries = []
        for q in _load(plans[s]["queries"], dropped, "genuine query"):
            if _is_copy_of_any(q, refs, c):
                copies += 1
            else:
                queries.append(q)
        if not queries:
            dropped["signer skipped: no usable genuine query"] += 1
            continue
        proc_refs = [r["proc"] for r in refs]

        def run(batch):
            return [decider(x["proc"], proc_refs) for x in batch]

        outcomes[s] = {
            "genuine": run(queries),
            "skilled": run(_load(forged[s], dropped, "skilled forgery")) if s in good_forgery else [],
            "random": run(_load(random_picks[s], dropped, "random forgery")),
        }
    return outcomes, copies


def _compute_metrics(outcomes: dict, good_forgery: list[str], c: dict) -> dict:
    def tally(kind: str, decision: str, only: list[str] | None) -> dict[str, tuple[int, int]]:
        return {s: (sum(1 for x in o[kind] if x["decision"] == decision), len(o[kind]))
                for s, o in outcomes.items() if only is None or s in only}

    scores = {s: {k: [x["score"] for x in o[k] if x["score"] is not None] for k in ("genuine", "skilled", "random")}
              for s, o in outcomes.items()}
    m = {}
    for kind, name, only in (("genuine", "genuine", None), ("random", "random_forgery", None), ("skilled", "skilled_forgery", good_forgery)):
        if kind == "skilled" and len(good_forgery) < c["min_signers_hard"]:
            reason = f"fewer than {c['min_signers_hard']} signers have enough skilled forgeries by different people"
            for suffix in ("match_rate", "review_rate", "mismatch_rate", "auc", "eer"):
                m[f"{name}_{suffix}"] = {"measurable": False, "reason": reason}
            continue
        for decision in ("MATCH", "REVIEW", "MISMATCH"):
            m[f"{name}_{decision.lower()}_rate"] = rate_with_ci(tally(kind, decision, only), c, f"{kind}-{decision}")
        if kind != "genuine":
            subset = {s: scores[s] for s in scores if only is None or s in only}
            m[f"{name}_auc"] = score_metric(subset, kind, auc, c, f"{kind}-auc")
            m[f"{name}_eer"] = score_metric(subset, kind, eer, c, f"{kind}-eer")
    return m


def _suggest_thresholds(outcomes: dict, good_forgery: list[str], c: dict) -> dict | None:
    """Thresholds from the VALIDATION split: the lowest 'match' that keeps false acceptance of the negatives at
    target_far, and the highest 'mismatch' that keeps genuine rejection at target_frr."""
    skilled = len(good_forgery) >= c["min_signers_hard"]
    kind, basis = ("skilled", "skilled_forgery") if skilled else ("random", "random_forgery (zero-effort)")
    pos = [x["score"] for o in outcomes.values() for x in o["genuine"] if x["score"] is not None]
    neg = [x["score"] for s, o in outcomes.items() if kind == "random" or s in good_forgery
           for x in o[kind] if x["score"] is not None]
    if not pos or not neg:
        return None
    th = choose_thresholds(pos + neg, [1] * len(pos) + [0] * len(neg), c)
    return {"match": th["match"], "mismatch": th["mismatch"], "basis": basis, "target_far": c["target_far"],
            "target_frr": c["target_frr"], "genuine_scores": len(pos), "negative_scores": len(neg),
            "caveat": None if skilled else "tuned on other signers' signatures only: this is NOT a skilled-forgery operating point"}


def _not_measurable(reason: str) -> dict:
    return {k: {"measurable": False, "reason": reason} for k in METRICS}


def evaluate(items: list[Item], *, split: str, decider: Callable, model_version: str = "", final: bool = False,
             cfg: dict | None = None, ledger_path: Path | None = None, model_signer_hashes: set[str] | None = None,
             progress: Callable[[str], None] | None = None) -> dict:
    """Run the protocol on one split. `decider(query, refs) -> {"decision", "score"}` is the system under test.
    `model_signer_hashes`: signer_hash() of every signer the model was trained or tuned on, when known."""
    if split not in ("val", "test"):
        raise ValueError("the protocol is run on 'val' (development) or 'test' (final)")
    c = {**DEFAULT_CFG, **(cfg or {})}
    say = progress or (lambda _m: None)
    final = bool(final) and split == "test"
    signer_split, split_problems = assign_splits(items, c)
    in_split = sorted(s for s, sp in signer_split.items() if sp == split)
    by_signer: dict[str, dict[str, list[Item]]] = {s: defaultdict(list) for s in in_split}
    for it in items:
        if it.signer_id in by_signer:
            by_signer[it.signer_id][it.kind].append(it)
    plans = {s: plan_signer(by_signer[s]["genuine"], c) for s in in_split}
    ok_signers = [s for s in in_split if eligible(plans[s])]
    forged = {s: by_signer[s]["skilled_forgery"] for s in ok_signers}
    good_forgery = [s for s in ok_signers if len(forged[s]) >= c["forgery_min_per_signer"]
                    and len({f.forger_id for f in forged[s]}) >= c["forgery_min_forgers"]]
    digest = split_digest(items, signer_split, split)
    ledger = read_ledger(ledger_path)
    checks = _population_checks(items, signer_split, split_problems, split, plans, in_split, ok_signers, good_forgery, forged,
                                model_signer_hashes, c)
    checks.append(_ledger_check(split, final, digest, model_version, ledger, ledger_path is not None))
    report: dict = {
        "protocol_version": PROTOCOL_VERSION, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "split": split, "final": final, "model_version": model_version, "test_set_digest": digest,
        "population": {"signers_in_split": len(in_split), "signers_qualifying": len(ok_signers),
                       "reference_specimens_per_signer": c["reference_min_specimens"], "reference_days": c["reference_min_sessions"],
                       "skilled_forgeries_listed": sum(len(v) for v in forged.values()),
                       "synthetic_alterations_set_aside": sum(1 for it in items if it.kind == "synthetic_alteration"
                                                              and signer_split.get(it.signer_id) == split)},
        "config": {k: (list(v) if isinstance(v, tuple) else v) for k, v in c.items()},
    }
    fatal = [k for k in checks if k["status"] == "fail" and k["scope"] == "run"]
    if fatal:
        report["metrics"] = _not_measurable("; ".join(k["message"] for k in fatal))
    else:
        say(f"scoring {len(ok_signers)} signers")
        dropped: Counter = Counter()
        outcomes, copies = _score_signers(ok_signers, plans, forged, good_forgery, decider, c, dropped, say)
        checks.append(_check("no_leakage_between_references_and_queries", "warn" if copies else "pass",
                             f"{copies} genuine image(s) were copies of a reference (identical, near-identical or the same signing "
                             "re-captured) and were removed" if copies else "no genuine query is a copy of a reference"))
        report["population"].update({
            "signers_scored": len(outcomes),
            "genuine_queries_scored": sum(len(o["genuine"]) for o in outcomes.values()),
            "skilled_forgeries_scored": sum(len(o["skilled"]) for o in outcomes.values()),
            "random_forgeries_scored": sum(len(o["random"]) for o in outcomes.values()),
            "dropped": dict(dropped)})
        scored_forgery_signers = [s for s in good_forgery if s in outcomes]
        report["metrics"] = _compute_metrics(outcomes, scored_forgery_signers, c)
        if split == "val":
            report["suggested_thresholds"] = _suggest_thresholds(outcomes, scored_forgery_signers, c)
        if final and ledger_path is not None and not any(
                e.get("digest") == digest and e.get("model_version") == model_version for e in ledger):
            ledger.append({"digest": digest, "model_version": model_version, "final": True, "at": report["generated_at"],
                           "signers": len(outcomes)})
            write_ledger(ledger_path, ledger)
    report["validity"] = {"checks": checks, "valid": not fatal,
                          "failed": [k["id"] for k in checks if k["status"] == "fail"],
                          "warnings": [k["id"] for k in checks if k["status"] == "warn"]}
    report["claims"] = claims(report)
    return report


# ------------------------------------------------------------------ what may be claimed
def claims(report: dict) -> dict:
    """Statements the evidence supports and statements it does not, written from the report itself so a summary
    cannot say more than the data allows."""
    m, pop, val = report["metrics"], report["population"], report["validity"]
    c = report.get("config", DEFAULT_CFG)
    allowed, not_allowed = [], []
    n = pop.get("signers_scored", 0)

    def pct(x):
        return f"{100 * x:.1f}%"

    def span(met):
        ci = met.get("ci95")
        return f" (95% interval {pct(ci[0])}–{pct(ci[1])})" if ci else ""

    ok = val["valid"]
    g, s, r = m.get("genuine_match_rate", {}), m.get("skilled_forgery_match_rate", {}), m.get("random_forgery_match_rate", {})
    if ok and g.get("measurable"):
        allowed.append(f"On {n} signers (references: {pop['reference_specimens_per_signer']} specimens from "
                       f"{pop['reference_days']}+ days; queries written on later days) genuine signatures were MATCHed "
                       f"{pct(g['value'])}{span(g)} at the operating point used.")
    else:
        not_allowed.append("Any genuine-acceptance or false-rejection rate for real signers.")
    if ok and s.get("measurable"):
        allowed.append(f"Skilled forgeries by other people were MATCHed {pct(s['value'])}{span(s)} over {s['signers']} signers.")
    else:
        not_allowed.append("Any statement about resistance to skilled forgery.")
    if ok and r.get("measurable"):
        allowed.append(f"Other signers' genuine signatures were MATCHed {pct(r['value'])}{span(r)} of the time (zero-effort forgery).")
    if not ok or n < c["min_signers"]:
        not_allowed.append(f"Population-level accuracy: fewer than {c['min_signers']} evaluated signers.")
    if report.get("split") != "test" or not report.get("final"):
        not_allowed.append("Any final performance figure: this was not a final run on the test split.")
    not_allowed.append("Detection of forgeries on the strength of synthetic alterations: those are edits of a specimen and may "
                       "only be cited as edited-copy detection.")
    not_allowed.append("Performance on whole cheques, other scanners, or signatures captured differently from the evaluation data.")
    not_allowed.append("Suitability for production use: this protocol measures one component on one data set.")
    return {"allowed": allowed, "not_allowed": not_allowed}


# ------------------------------------------------------------------ the existing signature dataset
def describe_signature_dataset(manifests: dict) -> dict:
    """What the delivered dataset (signature/) can and cannot measure under this protocol, from its manifests alone.
    Counts only: no file names, no signer IDs."""
    genuine, altered = manifests["genuine"], manifests["altered"]
    per_signer = Counter(r["signer_id"] for r in genuine)
    splits_of = defaultdict(set)
    for r in genuine:
        splits_of[r["signer_id"]].add(r["split_norm"])
    overlap = sum(1 for v in splits_of.values() if len(v) > 1)
    per_split = {sp: sum(1 for r in genuine if r["split_norm"] == sp) for sp in SPLITS}
    columns = {k.lower() for k in genuine[0]} if genuine else set()
    has_dates = any("date" in col for col in columns)
    multi = sum(1 for v in per_signer.values() if v >= 2)
    checks = [
        _check("signer_level_split", "fail" if overlap else "pass",
               f"{overlap} signer(s) appear in more than one split" if overlap
               else f"{len(per_signer)} signers, each in exactly one split (genuine specimens train/val/test: "
                    f"{per_split['train']}/{per_split['val']}/{per_split['test']})"),
        _check("independent_genuine_signers", "fail",
               f"{multi} of {len(per_signer)} signers have two or more genuine specimens (most specimens per signer: "
               f"{max(per_signer.values(), default=0)}); the manifest has {'a' if has_dates else 'no'} signing-date column and each "
               "specimen was cut from the signer's cheque, so there are no independent genuine queries: genuine acceptance and "
               "false rejection are NOT MEASURABLE"),
        _check("skilled_forgeries", "fail",
               f"0 skilled forgeries (the manifest records no forger); the {len(altered)} 'forged' rows are script-generated "
               "stroke edits of the specimen itself: false acceptance of skilled forgeries is NOT MEASURABLE"),
        _check("synthetic_alterations_kept_apart", "pass",
               f"{len(altered)} synthetic alterations are reported only as edited-copy detection, never as forgeries"),
        _check("test_set_used_once", "warn",
               "the test split of this dataset was used repeatedly while the signature model was developed: treat any number "
               "taken from it as exploratory"),
    ]
    metrics = _not_measurable("one genuine specimen per signer, cut from the signer's cheque; no signing dates; no real forgeries")
    for key in METRICS:
        if key.startswith("random_forgery"):
            metrics[key] = {"measurable": True, "caveat": "single-reference baseline only: the genuine queries are simulated "
                                                          "re-captures of the reference image, not independent signings"}
    metrics["edited_copy_detection"] = {
        "measurable": True, "caveat": f"{len(altered)} synthetic stroke edits of the specimen itself; about 10 % contain no visible "
                                      "edit and cannot be detected; says nothing about forgeries by another person"}
    return {
        "protocol_version": PROTOCOL_VERSION, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataset": "signature/ as delivered", "split": "all", "final": False, "model_version": "",
        "population": {"signers": len(per_signer), "genuine_specimens": len(genuine),
                       "signers_with_two_or_more_genuine_specimens": multi, "skilled_forgeries_listed": 0,
                       "synthetic_alterations_set_aside": len(altered)},
        "validity": {"checks": checks, "valid": False, "failed": [k["id"] for k in checks if k["status"] == "fail"],
                     "warnings": [k["id"] for k in checks if k["status"] == "warn"]},
        "metrics": metrics, "config": dict(DEFAULT_CFG, fractions=list(DEFAULT_CFG["fractions"])),
        "claims": {
            "allowed": [
                "Edited copies of an enrolled specimen (synthetic stroke additions or erasures) are detected by an alignment check; "
                "the counts are in reports/final/MODEL_EVALUATION.md. This is tamper-evidence for copied images, not forgery detection.",
                "On a single-reference baseline with simulated re-captures, signatures of different signers are separated by the "
                "similarity score (same report).",
            ],
            "not_allowed": [
                "Any genuine-acceptance or false-rejection rate for real signers.",
                "Any statement about resistance to skilled forgery.",
                "Population-level accuracy: every signer has one specimen, so no signer can be evaluated with independent signatures.",
                "Detection of forgeries on the strength of synthetic alterations: those are edits of a specimen and may only be "
                "cited as edited-copy detection.",
                "Performance on whole cheques, other scanners, or signatures captured differently from the evaluation data.",
                "Suitability for production use: this protocol measures one component on one data set.",
            ],
        },
    }


# ------------------------------------------------------------------ report text
def render_markdown(report: dict) -> str:
    """Human-readable report with counts and rates only – no file names, no signer IDs, no images."""
    v, pop = report["validity"], report["population"]
    head = f"Protocol version {report['protocol_version']} · split **{report['split']}**"
    head += " (final run)" if report.get("final") else " (exploratory)" if report["split"] != "all" else ""
    if report.get("model_version"):
        head += f" · model `{report['model_version']}`"
    lines = ["# Signature evaluation report", "", head + f" · generated {report['generated_at']}", "",
             "**Validity: " + ("VALID for the metrics below" if v["valid"] else
                               "NOT VALID – the metrics are not measurable or not trustworthy") + "**", "", "## Population", ""]
    lines += [f"* {k.replace('_', ' ')}: {val}" for k, val in pop.items() if not isinstance(val, dict)]
    if pop.get("dropped"):
        lines += [f"* dropped – {k}: {n}" for k, n in sorted(pop["dropped"].items())]
    lines += ["", "## Validity checks", "", "| Check | Status | Detail |", "|---|---|---|"]
    lines += [f"| {c['id']} | {c['status'].upper()} | {c['message']} |" for c in v["checks"]]
    lines += ["", "## Metrics", "", "| Metric | Result |", "|---|---|"]
    grouped: dict[str, list[str]] = defaultdict(list)
    for name, m in report["metrics"].items():
        if not m.get("measurable"):
            grouped[m.get("reason", "")].append(name)
    for reason, names in grouped.items():
        lines.append(f"| {', '.join(names)} | **not measurable** – {reason} |")
    caveated: dict[str, list[str]] = defaultdict(list)
    for name, m in report["metrics"].items():
        if not m.get("measurable"):
            continue
        if "value" not in m:
            caveated[m.get("caveat", "")].append(name)
            continue
        ci = f" (95% interval {m['ci95'][0]}–{m['ci95'][1]})" if m.get("ci95") else ""
        counts = f" [{m['events']}/{m['trials']} over {m['signers']} signers]" if "events" in m else f" [{m['signers']} signers]"
        ub = (f"; no event occurred, so the 95% upper bound for the share of signers with one is "
              f"{m['upper95_share_of_signers_with_an_event']}") if "upper95_share_of_signers_with_an_event" in m else ""
        lines.append(f"| {name} | {m['value']}{ci}{counts}{ub} |")
    for caveat, names in caveated.items():
        lines.append(f"| {', '.join(names)} | measurable, with this caveat: {caveat} |")
    th = report.get("suggested_thresholds")
    if th:
        lines += ["", "## Suggested thresholds (from this validation split)", "",
                  f"match {th['match']} · mismatch {th['mismatch']} · basis: {th['basis']}"
                  + (f" · **{th['caveat']}**" if th.get("caveat") else "")]
    lines += ["", "## What may be claimed", ""] + [f"* {x}" for x in report["claims"]["allowed"]]
    if not report["claims"]["allowed"]:
        lines.append("* Nothing about real-world accuracy.")
    lines += ["", "## What may NOT be claimed", ""] + [f"* {x}" for x in report["claims"]["not_allowed"]]
    return "\n".join(lines) + "\n"
