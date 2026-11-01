"""Locate the signature on a CTS-2010 cheque.

The drawer's signature sits in the lower-right area above "Please sign above".
We search that area, merge nearby ink into clusters, and keep the cluster with
the most ink (printed captions are small, thin clusters). The caller can always
override the crop manually in the UI."""
from __future__ import annotations

import cv2
import numpy as np

SEARCH_REGION = (0.58, 0.50, 0.975, 0.86)  # x0, y0, x1, y1 as page fractions


def find_signature(cheque: np.ndarray, region=SEARCH_REGION) -> tuple[np.ndarray | None, dict]:
    h, w = cheque.shape[:2]
    x0, y0, x1, y1 = int(region[0] * w), int(region[1] * h), int(region[2] * w), int(region[3] * h)
    area = cheque[y0:y1, x0:x1]
    if area.size == 0:
        return None, {"found": False, "reason": "empty search region"}
    gray = cv2.cvtColor(area, cv2.COLOR_BGR2GRAY)
    bg = cv2.medianBlur(gray, 31)
    norm = cv2.divide(gray, np.maximum(bg, 1), scale=255)
    _, ink = cv2.threshold(norm, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    if ink.mean() > 255 * 0.3:
        _, ink = cv2.threshold(norm, 160, 255, cv2.THRESH_BINARY_INV)
    ink = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    ah, aw = ink.shape
    k = max(5, int(aw * 0.03))
    merged = cv2.dilate(ink, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k * 2, max(3, k // 2))))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(merged)
    best, best_score = None, 0.0
    for i in range(1, n):
        x, y, cw, ch, _ = stats[i]
        if cw < aw * 0.08 or ch < ah * 0.08:
            continue
        if ch < 0.12 * ah and cw > 0.5 * aw:   # long thin printed line
            continue
        ink_px = int((ink[y:y + ch, x:x + cw] > 0).sum())
        # signatures are tall relative to printed captions and carry more ink
        score = ink_px * (ch / ah)
        if score > best_score:
            best, best_score = (x, y, cw, ch), score
    if best is None:
        return None, {"found": False, "reason": "no signature-like ink cluster in the expected area"}
    x, y, cw, ch = best
    # drop small printed glyphs (caption / account-holder name) inside the cluster
    sub = ink[y:y + ch, x:x + cw]
    m, lab, st, _ = cv2.connectedComponentsWithStats(sub)
    tall = max((st[i][3] for i in range(1, m)), default=0)
    keep = np.zeros_like(sub)
    for i in range(1, m):
        if st[i][3] >= 0.3 * tall or st[i][4] >= 0.02 * sub.size:
            keep[lab == i] = 255
    ys, xs = np.nonzero(keep)
    if xs.size:
        x, y, cw, ch = x + int(xs.min()), y + int(ys.min()), int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)
    pad = int(0.04 * aw)
    sx0, sy0 = max(0, x - pad), max(0, y - pad)
    sx1, sy1 = min(aw, x + cw + pad), min(ah, y + ch + pad)
    crop = area[sy0:sy1, sx0:sx1].copy()
    box = [x0 + sx0, y0 + sy0, sx1 - sx0, sy1 - sy0]
    return crop, {"found": True, "box": box, "box_fraction": [round(box[0] / w, 4), round(box[1] / h, 4),
                                                              round(box[2] / w, 4), round(box[3] / h, 4)]}


def crop_fraction(cheque: np.ndarray, frac: list[float]) -> np.ndarray:
    """Manual crop: frac = [x, y, w, h] as page fractions (validated)."""
    h, w = cheque.shape[:2]
    x, y, fw, fh = [float(v) for v in frac]
    if not (0 <= x < 1 and 0 <= y < 1 and 0 < fw <= 1 and 0 < fh <= 1):
        raise ValueError("crop fractions must be within [0, 1]")
    x0, y0 = int(x * w), int(y * h)
    x1, y1 = min(w, int((x + fw) * w)), min(h, int((y + fh) * h))
    if x1 - x0 < 20 or y1 - y0 < 10:
        raise ValueError("crop is too small")
    return cheque[y0:y1, x0:x1].copy()
