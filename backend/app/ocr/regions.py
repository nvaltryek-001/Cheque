"""Isolate the filled-in text inside a cheque field region.

The typed/written values on a cheque are larger than the pre-printed labels, and
sit on top of guilloche backgrounds, box borders and underlines. We binarise
with background normalisation, drop line-like components, keep connected
components whose height is close to the largest text, group them into rows,
and re-draw those rows on a clean white canvas for the recogniser."""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class Row:
    x0: int
    y0: int
    x1: int
    y1: int
    char_h: float
    n: int
    image: np.ndarray  # clean black-on-white rendering of the row


def binarize(region: np.ndarray) -> np.ndarray:
    """Return an ink mask (255 = ink)."""
    gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY) if region.ndim == 3 else region.copy()
    k = max(15, (min(gray.shape[:2]) // 3) | 1)
    bg = cv2.medianBlur(gray, min(k, 51))
    norm = cv2.divide(gray, np.maximum(bg, 1), scale=255)
    _, inv = cv2.threshold(norm, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    # guard against Otsu picking up the paper texture on near-empty regions
    if inv.mean() > 255 * 0.35:
        _, inv = cv2.threshold(norm, 170, 255, cv2.THRESH_BINARY_INV)
    return inv


def remove_lines(ink: np.ndarray, min_len: int) -> np.ndarray:
    vert = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, min_len)))
    horiz = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (min_len * 2, 1)))
    out = ink.copy()
    out[(vert > 0) | (horiz > 0)] = 0
    return out


def text_rows(region: np.ndarray, min_ratio: float = 0.65, line_len_factor: float = 1.15) -> list[Row]:
    if region is None or region.size == 0:
        return []
    ink = binarize(region)
    H, W = ink.shape
    n, labels, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    comps = []
    for i in range(1, n):
        x, y, w, h, a = stats[i]
        if a < 12 or h < 5:
            continue
        if h > 0.75 * H or (w > 6 * h and h < 8) or (h > 6 * w and w <= 3):
            continue  # borders, underlines, box separators
        comps.append((i, x, y, w, h, a))
    if not comps:
        return []
    ref = float(np.percentile([c[4] for c in comps], 90))
    # estimate the character height from components with letter-like proportions
    letterish = [c[4] for c in comps if 0.25 <= c[3] / c[4] <= 1.3 and c[4] >= 0.4 * ref]
    if letterish:
        ref = float(np.percentile(letterish, 85))
    # now that the char height is known, erase long straight lines that touch characters
    clean = remove_lines(ink, max(10, int(ref * line_len_factor)))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(clean, connectivity=8)
    comps = [(i, *stats[i]) for i in range(1, n) if stats[i][4] >= 10]
    big = [c for c in comps if min_ratio * ref <= c[4] <= 1.6 * ref and c[3] <= 3.5 * ref]
    if not big:
        return []
    big.sort(key=lambda c: c[2] + c[4] / 2)
    clusters: list[list] = [[big[0]]]
    for c in big[1:]:
        cy = c[2] + c[4] / 2
        mean_cy = np.mean([d[2] + d[4] / 2 for d in clusters[-1]])
        if abs(cy - mean_cy) < 0.5 * ref:
            clusters[-1].append(c)
        else:
            clusters.append([c])
    rows = []
    for cl in clusters:
        if len(cl) < 2 and cl[0][3] < ref:
            continue
        x0 = min(c[1] for c in cl); x1 = max(c[1] + c[3] for c in cl)
        y0 = min(c[2] for c in cl); y1 = max(c[2] + c[4] for c in cl)
        pad_y = int(ref * 0.35)
        ry0, ry1 = max(0, y0 - pad_y), min(H, y1 + pad_y)
        rx0, rx1 = max(0, x0 - int(ref)), min(W, x1 + int(ref))
        canvas = np.full((ry1 - ry0, rx1 - rx0), 255, np.uint8)
        for c in comps:
            i, cx, cy, cw, ch, _ = c
            if ch > 1.6 * ref:
                continue
            if cy >= ry0 and cy + ch <= ry1 and cx >= rx0 and cx + cw <= rx1:
                m = labels[cy:cy + ch, cx:cx + cw] == i
                canvas[cy - ry0:cy - ry0 + ch, cx - rx0:cx - rx0 + cw][m] = 0
        canvas = cv2.copyMakeBorder(canvas, 12, 12, 16, 16, cv2.BORDER_CONSTANT, value=255)
        rows.append(Row(int(x0), int(y0), int(x1), int(y1), ref, len(cl), canvas))
    return rows


def digit_canvas(row: Row) -> np.ndarray:
    """Re-space the large glyphs of a row evenly (used for boxed date digits)."""
    inv = 255 - row.image
    n, labels, stats, _ = cv2.connectedComponentsWithStats(inv, connectivity=8)
    glyphs = [(i, *stats[i]) for i in range(1, n) if stats[i][3] >= 0.6 * row.char_h]
    glyphs.sort(key=lambda g: g[1])
    if not glyphs:
        return row.image
    h = int(row.char_h * 1.6)
    gap = int(row.char_h * 0.35)
    widths = [g[3] for g in glyphs]
    canvas = np.full((h + 24, sum(widths) + gap * (len(glyphs) + 1) + 24), 255, np.uint8)
    x = 12 + gap
    for i, gx, gy, gw, gh, _ in glyphs:
        m = labels[gy:gy + gh, gx:gx + gw] == i
        top = 12 + (h - gh) // 2
        canvas[top:top + gh, x:x + gw][m] = 0
        x += gw + gap
    return canvas


def boxed_digits(region: np.ndarray) -> np.ndarray | None:
    """Digits written in a row of boxes (CTS date field).

    Finds the long top/bottom border lines of the box strip, keeps the interior
    (restricted to the strip's horizontal extent), removes separators, and returns
    the digits evenly re-spaced on a clean canvas. None if no box strip is found."""
    ink = binarize(region)
    H, W = ink.shape
    horiz = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(20, int(W * 0.12)), 1)))
    rows = np.where(horiz.sum(1) / 255 > W * 0.15)[0]
    if rows.size < 2:
        return None
    lines, start = [], rows[0]
    for a, b in zip(rows, rows[1:]):
        if b - a > 2:
            lines.append((start, a))
            start = b
    lines.append((start, rows[-1]))
    best = None
    for (t0, t1), (b0, b1) in zip(lines, lines[1:]):
        gap = b0 - t1
        if 0.12 * H <= gap <= 0.7 * H and (best is None or gap > best[3] - best[2]):
            best = (t0, b1, t1 + 1, b0)
    if best is None:
        return None
    lt, lb, y0, y1 = best
    cols = np.where((horiz[lt:lb + 1] > 0).any(0))[0]
    if cols.size == 0:
        return None
    x0, x1 = int(cols.min()), int(cols.max()) + 1
    inner = ink[y0:y1, x0:x1].copy()
    ih = inner.shape[0]
    if ih < 8:
        return None
    inner[:2] = 0
    inner[-2:] = 0
    col_cover = (inner > 0).sum(0)
    inner[:, col_cover >= 0.9 * ih] = 0          # solid separators
    n, labels, stats, _ = cv2.connectedComponentsWithStats(inner, connectivity=8)
    comps = [(i, *stats[i]) for i in range(1, n)
             if stats[i][3] >= 0.35 * ih and stats[i][2] >= 0.2 * stats[i][3] and stats[i][4] >= 20]
    if len(comps) < 6:
        return None
    comps.sort(key=lambda c: c[1])
    ch = int(max(c[4] for c in comps))
    top_ref = min(c[2] for c in comps)
    gap = max(4, ch // 3)
    canvas = np.full((ch + 30, sum(c[3] for c in comps) + gap * (len(comps) + 1) + 30), 255, np.uint8)
    x = 15 + gap
    for i, cx, cy, cw, chh, _ in comps:
        m = labels[cy:cy + chh, cx:cx + cw] == i
        top = max(0, min(15 + (cy - top_ref), canvas.shape[0] - chh))
        canvas[top:top + chh, x:x + cw][m] = 0
        x += cw + gap
    return canvas
