"""Signature comparison: hand-crafted similarity features + a small learned fusion.

Features (each in [0, 1], higher = more similar)
  orb      ORB keypoint matches passing Lowe's ratio test / min keypoints
  sift     same with SIFT (skipped gracefully if SIFT is unavailable)
  ssim     structural similarity of the normalised grey canvases
  hog      cosine similarity of HOG descriptors
  profile  correlation of horizontal + vertical ink projection profiles
  chamfer  symmetric distance-transform agreement of the ink masks
  density  ratio of ink fractions (min/max)
  aspect   ratio of signature bounding-box aspect ratios (min/max)

Fusion: logistic regression trained on TRAIN-split pairs only
(scripts/train_signature.py); thresholds are chosen on the VALIDATION split.
The fused score is a similarity score, NOT a calibrated probability of
authenticity.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import cv2
import numpy as np

from .preprocess import CANVAS_H, CANVAS_W, Processed

FEATURES = ["orb", "sift", "ssim", "hog", "profile", "chamfer", "density", "aspect"]

_ORB = cv2.ORB_create(nfeatures=500, scaleFactor=1.2, edgeThreshold=8, patchSize=15)
try:
    _SIFT = cv2.SIFT_create(nfeatures=400)
except AttributeError:  # pragma: no cover - very old OpenCV
    _SIFT = None
try:
    _HOG = cv2.HOGDescriptor((CANVAS_W, CANVAS_H), (32, 32), (16, 16), (16, 16), 9)
except AttributeError:  # OpenCV builds without objdetect HOG (e.g. some 5.x wheels)
    _HOG = None


def _ratio_matches(d1, d2, norm, ratio=0.78) -> int:
    """Lowe ratio test that tolerates empty descriptors and short kNN lists."""
    if d1 is None or d2 is None or len(d1) == 0 or len(d2) == 0:
        return 0
    if norm == cv2.NORM_L2:
        d1, d2 = d1.astype(np.float32), d2.astype(np.float32)
    matcher = cv2.BFMatcher(norm)
    k = 2 if len(d2) >= 2 else 1
    good = 0
    for pair in matcher.knnMatch(d1, d2, k=k):
        if not pair:
            continue
        if len(pair) == 1:
            # single neighbour: accept only very close matches
            limit = 40 if norm == cv2.NORM_HAMMING else 150
            good += pair[0].distance < limit
        elif pair[0].distance < ratio * pair[1].distance:
            good += 1
    return good


def _kp_score(detector, a: Processed, b: Processed, norm) -> tuple[float, dict]:
    if detector is None:
        return float("nan"), {"available": False}
    k1, d1 = detector.detectAndCompute(a.gray, a.mask)
    k2, d2 = detector.detectAndCompute(b.gray, b.mask)
    n1, n2 = len(k1 or []), len(k2 or [])
    if n1 < 3 or n2 < 3:
        return 0.0, {"keypoints": [n1, n2], "matches": 0, "note": "too few keypoints"}
    good = min(_ratio_matches(d1, d2, norm), _ratio_matches(d2, d1, norm))
    return min(1.0, good / min(n1, n2)), {"keypoints": [n1, n2], "matches": good}


def _ssim(a: np.ndarray, b: np.ndarray) -> float:
    a = a.astype(np.float64)
    b = b.astype(np.float64)
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    blur = lambda x: cv2.GaussianBlur(x, (11, 11), 1.5)
    mu_a, mu_b = blur(a), blur(b)
    sa, sb = blur(a * a) - mu_a ** 2, blur(b * b) - mu_b ** 2
    sab = blur(a * b) - mu_a * mu_b
    m = ((2 * mu_a * mu_b + c1) * (2 * sab + c2)) / ((mu_a ** 2 + mu_b ** 2 + c1) * (sa + sb + c2))
    return float(np.clip(m.mean(), 0, 1))


def _hog(a: np.ndarray, b: np.ndarray) -> float:
    if _HOG is None:
        return float("nan")  # treated as "no evidence" by the fusion model
    ha = _HOG.compute(255 - a).ravel()
    hb = _HOG.compute(255 - b).ravel()
    denom = np.linalg.norm(ha) * np.linalg.norm(hb)
    return float(np.clip(ha @ hb / denom, 0, 1)) if denom > 0 else 0.0


def _profile(a: np.ndarray, b: np.ndarray) -> float:
    def corr(x, y):
        x = cv2.GaussianBlur(x.reshape(1, -1).astype(np.float32), (1, 9), 0).ravel()
        y = cv2.GaussianBlur(y.reshape(1, -1).astype(np.float32), (1, 9), 0).ravel()
        if x.std() < 1e-6 or y.std() < 1e-6:
            return 0.0
        return float(np.corrcoef(x, y)[0, 1])
    ma, mb = (a > 0).astype(np.float32), (b > 0).astype(np.float32)
    c = 0.5 * (corr(ma.sum(0), mb.sum(0)) + corr(ma.sum(1), mb.sum(1)))
    return float(np.clip((c + 1) / 2, 0, 1))


def _chamfer(a: np.ndarray, b: np.ndarray) -> float:
    if not a.any() or not b.any():
        return 0.0
    da = cv2.distanceTransform(255 - a, cv2.DIST_L2, 3)
    db = cv2.distanceTransform(255 - b, cv2.DIST_L2, 3)
    d = 0.5 * (db[a > 0].mean() + da[b > 0].mean())
    return float(math.exp(-d / 6.0))


def _ratio(x: float, y: float) -> float:
    return float(min(x, y) / max(x, y)) if max(x, y) > 0 else 0.0


def features(query: Processed, ref: Processed) -> tuple[dict, dict]:
    orb, orb_d = _kp_score(_ORB, query, ref, cv2.NORM_HAMMING)
    sift, sift_d = _kp_score(_SIFT, query, ref, cv2.NORM_L2)
    qa = query.bbox[2] / max(query.bbox[3], 1)
    ra = ref.bbox[2] / max(ref.bbox[3], 1)
    f = {
        "orb": orb,
        "sift": sift,
        "ssim": _ssim(query.gray, ref.gray),
        "hog": _hog(query.gray, ref.gray),
        "profile": _profile(query.mask, ref.mask),
        "chamfer": _chamfer(query.mask, ref.mask),
        "density": _ratio(query.mask.mean(), ref.mask.mean()),
        "aspect": _ratio(qa, ra),
    }
    return f, {"orb": orb_d, "sift": sift_d}


class FusionModel:
    """Logistic-regression weights over FEATURES (+ bias), with decision thresholds."""

    def __init__(self, weights: dict, bias: float, mean: dict, std: dict, thresholds: dict, meta: dict):
        self.weights, self.bias, self.mean, self.std = weights, bias, mean, std
        self.thresholds = thresholds
        self.meta = meta

    @property
    def version(self) -> str:
        return self.meta.get("version", "fusion-unknown")

    def score(self, f: dict) -> float:
        z = self.bias
        for k, w in self.weights.items():
            v = f.get(k)
            if v is None or (isinstance(v, float) and math.isnan(v)):
                v = self.mean.get(k, 0.0)
            z += w * (v - self.mean.get(k, 0.0)) / (self.std.get(k, 1.0) or 1.0)
        return 1 / (1 + math.exp(-z))

    def to_dict(self) -> dict:
        return {"weights": self.weights, "bias": self.bias, "mean": self.mean, "std": self.std,
                "thresholds": self.thresholds, "meta": self.meta}

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2))

    @classmethod
    def load(cls, path: Path) -> "FusionModel | None":
        p = Path(path)
        if not p.is_file():
            return None
        d = json.loads(p.read_text())
        return cls(d["weights"], d["bias"], d["mean"], d["std"], d["thresholds"], d.get("meta", {}))


def baseline_score(f: dict) -> float:
    """Unweighted mean of available features – used only when no trained model exists."""
    vals = [v for v in f.values() if v is not None and not (isinstance(v, float) and math.isnan(v))]
    return float(np.mean(vals)) if vals else 0.0


BASELINE_THRESHOLDS = {"match": 0.80, "mismatch": 0.55}


def decide(query: Processed, refs: list[Processed], model: FusionModel | None,
           thresholds: dict | None = None) -> dict:
    """Compare a query against all active reference specimens and return MATCH / REVIEW / MISMATCH."""
    reasons: list[str] = []
    if not refs:
        return {"decision": "REVIEW", "score": None, "reasons": ["No active reference specimens are enrolled."],
                "per_specimen": [], "model_version": None}
    if model is not None:
        th = {**model.thresholds, **(thresholds or {})}
        scorer, version = model.score, model.version
    else:
        th = {**BASELINE_THRESHOLDS, **(thresholds or {})}
        scorer, version = baseline_score, "baseline-mean-v1"
        reasons.append("Trained fusion model unavailable – using the unweighted baseline.")
    per = []
    for i, ref in enumerate(refs):
        f, dbg = features(query, ref)
        per.append({"index": i, "score": round(scorer(f), 4),
                    "features": {k: (None if isinstance(v, float) and math.isnan(v) else round(v, 4)) for k, v in f.items()},
                    "keypoints": dbg})
    best = max(per, key=lambda p: p["score"])
    score = best["score"]
    q_ok = query.quality.get("acceptable", True)
    if score >= th["match"]:
        decision = "MATCH"
        reasons.append(f"Best similarity {score:.2f} ≥ match threshold {th['match']:.2f}.")
    elif score <= th["mismatch"]:
        decision = "MISMATCH"
        reasons.append(f"Best similarity {score:.2f} ≤ mismatch threshold {th['mismatch']:.2f}.")
    else:
        decision = "REVIEW"
        reasons.append(f"Similarity {score:.2f} is between thresholds "
                       f"({th['mismatch']:.2f}–{th['match']:.2f}); manual review required.")
    if not q_ok:
        reasons.append("Query image quality is poor: " + "; ".join(i["message"] for i in query.quality.get("issues", [])))
        if decision == "MATCH":
            decision = "REVIEW"
    if len(refs) < 2:
        reasons.append("Only one reference specimen is enrolled; enroll more specimens for a more reliable comparison.")
    strongest = sorted(best["features"].items(), key=lambda kv: -(kv[1] or 0))[:3]
    weakest = sorted(best["features"].items(), key=lambda kv: (kv[1] if kv[1] is not None else 1))[:3]
    return {
        "decision": decision, "score": score, "thresholds": th, "model_version": version,
        "reasons": reasons, "per_specimen": per, "best_index": best["index"],
        "explanation": {"strongest_agreement": strongest, "weakest_agreement": weakest},
        "disclaimer": "Decision-support only. A similarity score is not proof of authenticity.",
    }
