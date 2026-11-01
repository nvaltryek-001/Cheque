"""Pair construction, fusion training, threshold selection and evaluation for the
signature comparator.

Task definition (what the data actually supports)
  Given an enrolled reference image R and a query image Q, decide whether Q is
  the same signature as R (possibly re-captured) or a different/altered one.

Pairs (built inside one split – signers never cross splits)
  positive  R vs a simulated re-capture of R (rotation, scale, blur, noise,
            brightness, JPEG). These are NOT independent genuine specimens:
            the dataset has only one genuine image per signer.
  altered   R vs a re-capture of each synthetic alteration of R from
            forged_signatures/ (added/erased strokes – synthetic, not real forgeries)
  impostor  R vs a re-capture of genuine signatures of other signers in the
            same split (random/zero-effort forgery)
Every query - positive or negative - goes through the same re-capture
simulation. Without this, the alterations (pixel-identical to R apart from the
edits) look MORE similar than the re-captured positives, and a model learns the
shortcut "untransformed = altered", which is meaningless on real cheques.
Fusion weights are constrained to be non-negative so that more agreement on any
feature can never lower the score.

Protocol: fusion weights fit on TRAIN pairs only; thresholds chosen on VAL
pairs only; TEST pairs scored once with the frozen model + thresholds.
"""
from __future__ import annotations

import hashlib
import math
import random
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from .compare import FEATURES, FusionModel, features
from .preprocess import SignatureError, preprocess

PAIR_CONFIG = {"positives_per_signer": 3, "impostors_per_signer": 3, "seed": 1337,
               "target_far": 0.05, "target_frr": 0.05, "l2": 0.01, "epochs": 3000, "lr": 0.1}


def recapture(img: np.ndarray, rng: random.Random) -> np.ndarray:
    h, w = img.shape[:2]
    pad = int(0.15 * max(h, w))
    border = cv2.copyMakeBorder(img, pad, pad, pad, pad, cv2.BORDER_REPLICATE)
    H, W = border.shape[:2]
    m = cv2.getRotationMatrix2D((W / 2, H / 2), rng.uniform(-4, 4), rng.uniform(0.9, 1.1))
    m[:, 2] += (rng.uniform(-0.03, 0.03) * W, rng.uniform(-0.03, 0.03) * H)
    out = cv2.warpAffine(border, m, (W, H), borderMode=cv2.BORDER_REPLICATE)
    sigma = rng.uniform(0, 0.9)
    if sigma > 0.2:
        out = cv2.GaussianBlur(out, (0, 0), sigma)
    alpha, beta = rng.uniform(0.85, 1.15), rng.uniform(-18, 18)
    out = np.clip(out.astype(np.float32) * alpha + beta, 0, 255)
    noise = np.random.default_rng(rng.randint(0, 2**31)).normal(0, rng.uniform(0, 6), out.shape)
    out = np.clip(out + noise, 0, 255).astype(np.uint8)
    ok, buf = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, rng.randint(55, 92)])
    return cv2.imdecode(buf, cv2.IMREAD_COLOR) if ok else out


def build_pairs(manifests: dict, split: str, cfg: dict) -> list[dict]:
    rng = random.Random(f'{cfg["seed"]}-{split}')
    genuine = [r for r in manifests["genuine"] if r["split_norm"] == split]
    altered_by_target = defaultdict(list)
    for r in manifests["altered"]:
        if r["split_norm"] == split:
            altered_by_target[r["target_signer_id"]].append(r)
    cache: dict[str, object] = {}

    def load(path):
        key = str(path)
        if key not in cache:
            img = cv2.imread(key, cv2.IMREAD_COLOR)
            cache[key] = img
        return cache[key]

    pairs = []
    signer_ids = [g["signer_id"] for g in genuine]
    for g in genuine:
        ref_img = load(g["path"])
        if ref_img is None:
            continue
        try:
            ref = preprocess(ref_img)
        except SignatureError:
            continue
        queries = []
        for k in range(cfg["positives_per_signer"]):
            queries.append(("positive", recapture(ref_img, rng), f'recapture#{k}'))
        for a in altered_by_target.get(g["signer_id"], []):
            img = load(a["path"])
            if img is not None:
                queries.append(("altered", recapture(img, rng), a["filename"]))
        others = [s for s in signer_ids if s != g["signer_id"]]
        for s in rng.sample(others, min(cfg["impostors_per_signer"], len(others))):
            o = next(x for x in genuine if x["signer_id"] == s)
            img = load(o["path"])
            if img is not None:
                queries.append(("impostor", recapture(img, rng), o["filename"]))
        for kind, qimg, qname in queries:
            try:
                q = preprocess(qimg)
            except SignatureError:
                continue
            f, _ = features(q, ref)
            pairs.append({"split": split, "signer": g["signer_id"], "bank": g["bank"], "kind": kind,
                          "label": 1 if kind == "positive" else 0, "query": qname, "features": f})
    return pairs


def _matrix(pairs: list[dict], fill: dict | None = None) -> np.ndarray:
    X = np.array([[p["features"][k] for k in FEATURES] for p in pairs], dtype=np.float64)
    if fill is not None:
        for j, k in enumerate(FEATURES):
            col = X[:, j]
            col[np.isnan(col)] = fill[k]
    return X


def fit_fusion(train_pairs: list[dict], cfg: dict) -> tuple[dict, float, dict, dict]:
    X = _matrix(train_pairs)
    mean = {k: float(np.nanmean(X[:, j])) for j, k in enumerate(FEATURES)}
    X = _matrix(train_pairs, mean)
    std = {k: float(X[:, j].std() or 1.0) for j, k in enumerate(FEATURES)}
    Z = (X - np.array([mean[k] for k in FEATURES])) / np.array([std[k] for k in FEATURES])
    y = np.array([p["label"] for p in train_pairs], dtype=np.float64)
    # class-balanced weights
    pos = y.mean()
    sw = np.where(y == 1, 0.5 / max(pos, 1e-6), 0.5 / max(1 - pos, 1e-6))
    w = np.zeros(Z.shape[1])
    b = 0.0
    for _ in range(cfg["epochs"]):
        p = 1 / (1 + np.exp(-(Z @ w + b)))
        g = (p - y) * sw
        w -= cfg["lr"] * (Z.T @ g / len(y) + cfg["l2"] * w)
        w = np.maximum(w, 0.0)  # monotone: similarity can only raise the score
        b -= cfg["lr"] * g.mean()
    return {k: float(w[j]) for j, k in enumerate(FEATURES)}, float(b), mean, std


def roc_auc(scores: list[float], labels: list[int]) -> float | None:
    pos = [s for s, l in zip(scores, labels) if l == 1]
    neg = [s for s, l in zip(scores, labels) if l == 0]
    if not pos or not neg:
        return None
    wins = 0.0
    for p in pos:
        for n in neg:
            wins += 1.0 if p > n else (0.5 if p == n else 0.0)
    return wins / (len(pos) * len(neg))


def rates(scores, labels, t) -> dict:
    tp = sum(1 for s, l in zip(scores, labels) if l == 1 and s >= t)
    fn = sum(1 for s, l in zip(scores, labels) if l == 1 and s < t)
    fp = sum(1 for s, l in zip(scores, labels) if l == 0 and s >= t)
    tn = sum(1 for s, l in zip(scores, labels) if l == 0 and s < t)
    prec = tp / (tp + fp) if tp + fp else None
    rec = tp / (tp + fn) if tp + fn else None
    f1 = 2 * prec * rec / (prec + rec) if prec and rec else None
    return {"threshold": round(t, 4), "tp": tp, "fn": fn, "fp": fp, "tn": tn,
            "far": round(fp / (fp + tn), 4) if fp + tn else None,
            "frr": round(fn / (fn + tp), 4) if fn + tp else None,
            "precision": round(prec, 4) if prec is not None else None,
            "recall": round(rec, 4) if rec is not None else None,
            "f1": round(f1, 4) if f1 is not None else None}


def eer(scores, labels) -> tuple[float, float]:
    best = (1.0, 0.5)
    for t in sorted(set(scores)):
        r = rates(scores, labels, t)
        gap = abs((r["far"] or 0) - (r["frr"] or 0))
        if gap < best[0]:
            best = (gap, t)
    r = rates(scores, labels, best[1])
    return ((r["far"] or 0) + (r["frr"] or 0)) / 2, best[1]


def choose_thresholds(scores, labels, cfg) -> dict:
    cands = sorted(set(scores))
    match = next((t for t in cands if (rates(scores, labels, t)["far"] or 0) <= cfg["target_far"]), cands[-1])
    mismatch = max([t for t in cands if (rates(scores, labels, t)["frr"] or 0) <= cfg["target_frr"]] or [cands[0]])
    e, te = eer(scores, labels)
    if mismatch > match:
        mismatch = match = te
    # put the decision boundary half-way to the next score so ties are not on the edge
    return {"match": round(float(match), 4), "mismatch": round(float(min(mismatch, match)), 4),
            "eer_threshold": round(float(te), 4), "val_eer": round(float(e), 4)}


def three_way(scores, kinds, th) -> dict:
    out = defaultdict(Counter)
    for s, k in zip(scores, kinds):
        d = "MATCH" if s >= th["match"] else ("MISMATCH" if s <= th["mismatch"] else "REVIEW")
        out[k][d] += 1
    return {k: dict(v) for k, v in out.items()}


def summarize(scores, labels, kinds, th) -> dict:
    e, te = eer(scores, labels)
    by_kind = {}
    for neg in ("altered", "impostor"):
        idx = [i for i, k in enumerate(kinds) if k in ("positive", neg)]
        by_kind[f"positive_vs_{neg}"] = {
            "auc": _r(roc_auc([scores[i] for i in idx], [labels[i] for i in idx])),
            "far_at_match_threshold": rates([scores[i] for i in idx], [labels[i] for i in idx], th["match"])["far"],
        }
    return {
        "pairs": dict(Counter(kinds)),
        "roc_auc": _r(roc_auc(scores, labels)),
        "eer": round(e, 4), "eer_threshold": round(te, 4),
        "at_match_threshold": rates(scores, labels, th["match"]),
        "at_eer_threshold": rates(scores, labels, te),
        "by_negative_type": by_kind,
        "three_way_decisions": three_way(scores, kinds, th),
    }


def _r(x):
    return round(x, 4) if x is not None else None


SCOPE = {
    "measures": [
        "Whether a re-captured copy of the SAME image (rotation, scale, blur, noise, JPEG) scores above other signers' images.",
        "Whether script-generated stroke edits of a specimen ('edited copies') are caught by the alignment check.",
    ],
    "does_not_measure": [
        "Genuine acceptance or false rejection for real signers: the genuine queries are re-captures of the reference image, "
        "not new signings.",
        "Detection of forgeries made by another person (skilled forgery): the dataset contains none.",
        "Anything about the people in the data set beyond one specimen each; no population-level accuracy.",
    ],
    "valid_protocol": "backend/app/signature/protocol.py (manage.py eval-signatures); see docs/SIGNATURE_EVALUATION_PROTOCOL.md",
}


def evaluate_decisions(manifests: dict, split: str, model: FusionModel, cfg: dict) -> dict:
    """End-to-end MATCH/REVIEW/MISMATCH decisions, including the alteration check, for:
    recaptured genuine (simulated positive), synthetic edit of the specimen submitted as-is, re-captured
    synthetic edit, and re-captured other-signer signature. Same seeds as build_pairs.
    A synthetic edit is a script-generated change to the specimen itself ("edited copy"), not a forgery by
    another person: nothing here measures forgery detection (see signature/protocol.py)."""
    from .compare import decide
    rng = random.Random(f'{cfg["seed"]}-decisions-{split}')
    genuine = [r for r in manifests["genuine"] if r["split_norm"] == split]
    altered = defaultdict(list)
    for r in manifests["altered"]:
        if r["split_norm"] == split:
            altered[r["target_signer_id"]].append(r)
    out = defaultdict(Counter)
    for g in genuine:
        ref_img = cv2.imread(str(g["path"]), cv2.IMREAD_COLOR)
        if ref_img is None:
            continue
        ref = preprocess(ref_img)
        queries = [("genuine_recaptured", recapture(ref_img, rng)) for _ in range(cfg["positives_per_signer"])]
        for a in altered.get(g["signer_id"], []):
            img = cv2.imread(str(a["path"]), cv2.IMREAD_COLOR)
            if img is not None:
                queries += [("edited_copy_as_is", img), ("edited_copy_recaptured", recapture(img, rng))]
        others = [x for x in genuine if x["signer_id"] != g["signer_id"]]
        for o in rng.sample(others, min(cfg["impostors_per_signer"], len(others))):
            img = cv2.imread(str(o["path"]), cv2.IMREAD_COLOR)
            if img is not None:
                queries.append(("other_signer_recaptured", recapture(img, rng)))
        for kind, img in queries:
            try:
                out[kind][decide(preprocess(img), [ref], model)["decision"]] += 1
            except SignatureError:
                out[kind]["ERROR"] += 1
    res = {k: dict(v) for k, v in out.items()}
    pos = out["genuine_recaptured"]
    neg = Counter()
    for k in ("edited_copy_as_is", "edited_copy_recaptured", "other_signer_recaptured"):
        neg.update(out[k])
    total_neg = sum(neg.values())
    res["summary"] = {
        "false_accept_rate_MATCH_on_negatives": round(neg["MATCH"] / total_neg, 4) if total_neg else None,
        "edited_copy_as_is_accepted": f'{out["edited_copy_as_is"]["MATCH"]}/{sum(out["edited_copy_as_is"].values())}',
        "edited_copy_as_is_rejected": f'{out["edited_copy_as_is"]["MISMATCH"]}/{sum(out["edited_copy_as_is"].values())}',
        "genuine_recaptured_MATCH": f'{pos["MATCH"]}/{sum(pos.values())}',
        "genuine_recaptured_MISMATCH": f'{pos["MISMATCH"]}/{sum(pos.values())}',
    }
    return res


def train_and_evaluate(manifests: dict, cfg: dict | None = None, progress=None) -> tuple[FusionModel, dict]:
    cfg = {**PAIR_CONFIG, **(cfg or {})}
    pairs = {}
    for split in ("train", "val", "test"):
        if progress:
            progress(f"building {split} pairs")
        pairs[split] = build_pairs(manifests, split, cfg)
    if not pairs["train"] or not pairs["val"]:
        raise RuntimeError("Not enough pairs to train (train and validation splits are required).")

    weights, bias, mean, std = fit_fusion(pairs["train"], cfg)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    model = FusionModel(weights, bias, mean, std, {}, {})
    sc = {s: [model.score(p["features"]) for p in pairs[s]] for s in pairs}
    lb = {s: [p["label"] for p in pairs[s]] for s in pairs}
    kd = {s: [p["kind"] for p in pairs[s]] for s in pairs}
    th = choose_thresholds(sc["val"], lb["val"], cfg)
    model.thresholds = {"match": th["match"], "mismatch": th["mismatch"]}

    # per-feature baselines (raw feature value as the score), AUC on validation
    feature_auc = {}
    for k in FEATURES:
        vals = [p["features"][k] for p in pairs["val"]]
        if any(isinstance(v, float) and math.isnan(v) for v in vals):
            feature_auc[k] = None
            continue
        feature_auc[k] = _r(roc_auc(vals, lb["val"]))
        for neg in ("altered", "impostor"):
            idx = [i for i, kk in enumerate(kd["val"]) if kk in ("positive", neg)]
            feature_auc[f"{k}__vs_{neg}"] = _r(roc_auc([vals[i] for i in idx], [lb["val"][i] for i in idx]))

    from .protocol import signer_hash                    # imported here: protocol imports this module
    digest = hashlib.sha256(repr(sorted(weights.items())).encode()).hexdigest()[:8]
    meta = {
        "version": f"fusion-lr-{stamp}-{digest}",
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "config": cfg,
        "features": FEATURES,
        "task": "reference-vs-query similarity; positives are simulated re-captures (see docs)",
        # signers whose images fitted the weights (train) or chose the thresholds (val); stored as hashes so the
        # evaluation protocol can refuse to test on them without the model file naming anybody
        "fitted_signer_hashes": sorted({signer_hash(p["signer"]) for s in ("train", "val") for p in pairs[s]}),
    }
    model.meta = meta
    report = {
        "model_version": meta["version"],
        "config": cfg,
        "thresholds": th,
        "weights": {k: round(v, 4) for k, v in weights.items()},
        "per_feature_val_auc": feature_auc,
        "train": summarize(sc["train"], lb["train"], kd["train"], model.thresholds),
        "val": summarize(sc["val"], lb["val"], kd["val"], model.thresholds),
        "test": summarize(sc["test"], lb["test"], kd["test"], model.thresholds) if pairs["test"] else None,
        "signers_per_split": {s: len({p["signer"] for p in pairs[s]}) for s in pairs},
        "scope": SCOPE,
        "decisions_with_alteration_check": {},
        "leakage_check": {
            "train_val_overlap": len({p["signer"] for p in pairs["train"]} & {p["signer"] for p in pairs["val"]}),
            "train_test_overlap": len({p["signer"] for p in pairs["train"]} & {p["signer"] for p in pairs["test"]}),
            "val_test_overlap": len({p["signer"] for p in pairs["val"]} & {p["signer"] for p in pairs["test"]}),
        },
        "limitations": [
            "Only one genuine specimen per signer: positive pairs are simulated re-captures of the same image, "
            "so the model has never seen natural within-signer variation.",
            "Altered signatures are synthetic stroke additions/erasures of the reference image ('edited copies'), not "
            "forgeries by another person: no figure in this report is a forgery-detection rate.",
            "Scores are similarity scores, not calibrated probabilities.",
            "Small test set (20 signers): metrics carry wide uncertainty.",
        ],
    }
    for split in ("val", "test"):
        if progress:
            progress(f"evaluating decisions with alteration check on {split}")
        report["decisions_with_alteration_check"][split] = evaluate_decisions(manifests, split, model, cfg)
    report["limitations"].append(
        "About 10% of the synthetic edits (25/240) differ from their genuine image only by JPEG noise – the "
        "random edit landed on blank paper – so no method can distinguish them; they count against detection.")
    report["limitations"].append(
        "Genuine positives are re-captures of the same image, so they are near-identical copies and the copy "
        "check routes them to REVIEW. Real genuine signatures (a new signing) would not be pixel copies; this "
        "behaviour could not be evaluated on this dataset.")
    return model, report
