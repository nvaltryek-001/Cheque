"""Alteration check: is the questioned signature an edited copy of an enrolled specimen?

Global similarity features cannot see a few strokes added to, or erased from, an otherwise
identical image – the classic "copy and alter" forgery (and exactly what the dataset's
synthetic forgeries are). This check aligns the query onto the specimen with ORB keypoints +
RANSAC; if the two are near-identical after alignment, it measures ink that exists in only one
of them, grouped into connected regions. Natural re-signing never aligns this tightly, so the
check only fires for copies. Thresholds are chosen on TRAIN pairs (scripts in training.py)."""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .preprocess import Processed

DEFAULTS = {"min_inliers": 20, "copy_agreement": 0.6, "region_px_frac": 0.0025, "altered_frac": 0.01,
            "exact_copy_agreement": 0.98,
            "level": 45, "window": 5, "work_height": 160}
_ORB = cv2.ORB_create(nfeatures=1500, edgeThreshold=10, patchSize=21)


@dataclass
class AlterationResult:
    aligned: bool
    inliers: int
    agreement: float        # share of ink that matches after alignment (min of both directions)
    added_frac: float       # ink present only in the query, in significant regions, / reference ink
    removed_frac: float     # ink present only in the reference, in significant regions, / reference ink
    regions: int
    near_copy: bool
    altered: bool

    def as_dict(self) -> dict:
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


def _sig_regions(diff: np.ndarray, min_px: int) -> tuple[int, int]:
    n, _, st, _ = cv2.connectedComponentsWithStats(diff, connectivity=8)
    big = [st[i][4] for i in range(1, n) if st[i][4] >= min_px]
    return int(sum(big)), len(big)


def check(query: Processed, ref: Processed, cfg: dict | None = None) -> AlterationResult | None:
    c = {**DEFAULTS, **(cfg or {})}
    if query.full_gray is None or ref.full_gray is None:
        return None
    def scaled(img, interp):
        f = c["work_height"] / img.shape[0]
        return cv2.resize(img, (max(1, int(round(img.shape[1] * f))), c["work_height"]), interpolation=interp)
    # specimens are often only 30–60 px tall: work at a fixed height so small edits span several pixels
    qg, rg = scaled(query.full_gray, cv2.INTER_CUBIC), scaled(ref.full_gray, cv2.INTER_CUBIC)
    q_mask, r_mask = scaled(query.full_mask, cv2.INTER_NEAREST), scaled(ref.full_mask, cv2.INTER_NEAREST)
    k1, d1 = _ORB.detectAndCompute(qg, None)
    k2, d2 = _ORB.detectAndCompute(rg, None)
    if d1 is None or d2 is None or len(k1) < c["min_inliers"] or len(k2) < c["min_inliers"]:
        return AlterationResult(False, 0, 0.0, 0.0, 0.0, 0, False, False)
    pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(d1, d2, k=2)
    good = [p[0] for p in pairs if len(p) == 2 and p[0].distance < 0.75 * p[1].distance]
    if len(good) < c["min_inliers"]:
        return AlterationResult(False, len(good), 0.0, 0.0, 0.0, 0, False, False)
    src = np.float32([k1[m.queryIdx].pt for m in good])
    dst = np.float32([k2[m.trainIdx].pt for m in good])
    M, inl = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=3.0)
    n_in = int(inl.sum()) if inl is not None else 0
    if M is None or n_in < c["min_inliers"]:
        return AlterationResult(False, n_in, 0.0, 0.0, 0.0, 0, False, False)
    h, w = rg.shape
    qm = cv2.warpAffine(q_mask, M, (w, h), flags=cv2.INTER_NEAREST, borderValue=0)
    rm = r_mask
    k3 = np.ones((3, 3), np.uint8)
    q_d, r_d = cv2.dilate(qm, k3, iterations=2), cv2.dilate(rm, k3, iterations=2)
    q_ink, r_ink = max(int((qm > 0).sum()), 1), max(int((rm > 0).sum()), 1)
    agreement = min(((qm > 0) & (r_d > 0)).sum() / q_ink, ((rm > 0) & (q_d > 0)).sum() / r_ink)
    # grey-level comparison (faint added strokes / weakened areas do not cross the ink threshold):
    # a pixel counts as "added" only if the query is darker than every reference pixel near it,
    # and "removed" if the reference is darker than every query pixel near it. This tolerates the
    # small misalignment and blur of a re-capture.
    qw = cv2.warpAffine(qg, M, (w, h), flags=cv2.INTER_LINEAR, borderValue=255)
    valid = cv2.warpAffine(np.full_like(qg, 255), M, (w, h), flags=cv2.INTER_NEAREST, borderValue=0) > 0
    valid = cv2.erode(valid.astype(np.uint8), k3, iterations=3) > 0
    rb = cv2.GaussianBlur(rg, (3, 3), 0).astype(np.int16)
    qb = cv2.GaussianBlur(qw, (3, 3), 0).astype(np.int16)
    win = np.ones((c["window"], c["window"]), np.uint8)
    r_min = cv2.erode(rb.astype(np.uint8), win).astype(np.int16)
    q_min = cv2.erode(qb.astype(np.uint8), win).astype(np.int16)
    added = (((r_min - qb) > c["level"]) & valid).astype(np.uint8)
    removed = (((q_min - rb) > c["level"]) & valid).astype(np.uint8)
    min_px = max(6, int(c["region_px_frac"] * h * w))
    a_px, a_n = _sig_regions(added, min_px)
    r_px, r_n = _sig_regions(removed, min_px)
    near_copy = agreement >= c["copy_agreement"]
    added_frac, removed_frac = a_px / r_ink, r_px / r_ink
    altered = near_copy and max(added_frac, removed_frac) >= c["altered_frac"]
    return AlterationResult(True, n_in, float(agreement), float(added_frac), float(removed_frac),
                            a_n + r_n, bool(near_copy), bool(altered))
