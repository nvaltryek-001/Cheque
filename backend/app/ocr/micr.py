"""E-13B MICR band reader.

General-purpose OCR engines (Tesseract 'eng', PaddleOCR) do not read the E-13B
magnetic-ink font reliably. This module segments the MICR band into glyphs and
classifies digits with a 1-nearest-neighbour template model.

Training labels: the OCR dataset has no transcriptions, but every filename stem
is the 6-digit cheque number, which is the first MICR group. Glyph templates are
therefore learned only from the TRAIN split, using the filename digits as labels
(scripts/train_micr.py). Validation/test cheques are never used for templates.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

GLYPH_H, GLYPH_W = 24, 20
NORM_WIDTH = 1024


@dataclass
class Glyph:
    x: int
    y: int
    w: int
    h: int
    img: np.ndarray  # normalised GLYPH_H x GLYPH_W float32 in [0,1]


def _normalise(mask: np.ndarray) -> np.ndarray:
    h, w = mask.shape
    scale = (GLYPH_H - 2) / max(h, 1)
    nw = max(1, min(GLYPH_W - 2, int(round(w * scale))))
    small = cv2.resize(mask.astype(np.float32), (nw, GLYPH_H - 2), interpolation=cv2.INTER_AREA)
    canvas = np.zeros((GLYPH_H, GLYPH_W), np.float32)
    x0 = (GLYPH_W - nw) // 2
    canvas[1:GLYPH_H - 1, x0:x0 + nw] = small
    return canvas


def micr_band(img: np.ndarray) -> np.ndarray:
    """Bottom band of the cheque, resized so the page is NORM_WIDTH wide."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    h, w = gray.shape
    s = NORM_WIDTH / w
    gray = cv2.resize(gray, (NORM_WIDTH, max(1, int(h * s))), interpolation=cv2.INTER_AREA)
    h = gray.shape[0]
    return gray[int(h * 0.82):int(h * 0.995), :]


def segment(img: np.ndarray) -> tuple[list[Glyph], list[tuple[int, int]]]:
    """Return digit-sized glyphs (left->right) and symbol blobs (x, w) along the MICR line."""
    band = micr_band(img)
    if band.size == 0:
        return [], []
    _, binary = cv2.threshold(band, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    comps = [(i, *stats[i]) for i in range(1, n) if stats[i][4] >= 12]
    # the MICR glyphs are the most common tall height in the band
    tall = [c for c in comps if 11 <= c[4] <= 30 and c[3] <= 24]
    if not tall:
        return [], []
    heights = np.array([c[4] for c in tall])
    mode_h = int(np.bincount(heights).argmax())
    centers = np.array([c[2] + c[4] / 2 for c in tall if abs(c[4] - mode_h) <= 2])
    if centers.size == 0:
        return [], []
    line_y = float(np.median(centers))
    glyphs, symbols = [], []
    for i, x, y, w, h, area in comps:
        cy = y + h / 2
        if abs(cy - line_y) > mode_h * 0.75:
            continue
        if abs(h - mode_h) <= 2 and w <= mode_h * 1.2:
            mask = (labels[y:y + h, x:x + w] == i).astype(np.uint8)
            glyphs.append(Glyph(int(x), int(y), int(w), int(h), _normalise(mask)))
        elif h < mode_h - 2 and h >= 3:
            symbols.append((int(x), int(w)))
    glyphs.sort(key=lambda g: g.x)
    symbols.sort()
    return glyphs, symbols


def group_glyphs(glyphs: list[Glyph], symbols: list[tuple[int, int]]) -> list[list[Glyph]]:
    """Split glyphs into MICR fields at symbols or wide gaps."""
    if not glyphs:
        return []
    pitch = float(np.median(np.diff([g.x for g in glyphs]))) if len(glyphs) > 1 else glyphs[0].w * 1.5
    groups, current = [], [glyphs[0]]
    for prev, g in zip(glyphs, glyphs[1:]):
        gap_has_symbol = any(prev.x + prev.w <= sx <= g.x for sx, _ in symbols)
        if gap_has_symbol or (g.x - prev.x) > pitch * 1.7:
            groups.append(current)
            current = [g]
        else:
            current.append(g)
    groups.append(current)
    return groups


class MicrModel:
    def __init__(self, samples: np.ndarray, labels: list[str], meta: dict | None = None):
        self.samples = samples.reshape(len(labels), -1).astype(np.float32)
        norms = np.linalg.norm(self.samples - self.samples.mean(1, keepdims=True), axis=1, keepdims=True)
        self._centered = (self.samples - self.samples.mean(1, keepdims=True)) / np.maximum(norms, 1e-6)
        self.labels = labels
        self.meta = meta or {}

    def classify(self, glyph: Glyph) -> tuple[str, float]:
        v = glyph.img.reshape(-1)
        v = (v - v.mean()) / max(np.linalg.norm(v - v.mean()), 1e-6)
        sims = self._centered @ v
        k = int(np.argmax(sims))
        return self.labels[k], float(sims[k])

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(path, samples=self.samples.reshape(-1, GLYPH_H, GLYPH_W),
                            labels=np.array(self.labels), meta=json.dumps(self.meta))

    @classmethod
    def load(cls, path: Path) -> "MicrModel | None":
        if not Path(path).is_file():
            return None
        data = np.load(path, allow_pickle=False)
        return cls(data["samples"], [str(x) for x in data["labels"]], json.loads(str(data["meta"])))


def read_micr(img: np.ndarray, model: MicrModel | None) -> dict:
    """Read the MICR line. Returns groups of digits plus a per-glyph confidence."""
    result = {"available": model is not None, "raw": "", "groups": [], "confidence": 0.0,
              "cheque_number": "", "micr_code": "", "short_account": "", "transaction_code": ""}
    if model is None:
        return result
    glyphs, symbols = segment(img)
    groups = group_glyphs(glyphs, symbols)
    texts, confs = [], []
    for grp in groups:
        chars = []
        for g in grp:
            label, sim = model.classify(g)
            chars.append(label)
            confs.append(sim)
        texts.append("".join(chars))
    result["groups"] = texts
    result["raw"] = " ".join(texts)
    result["confidence"] = round(float(np.mean(confs)), 3) if confs else 0.0
    result["min_glyph_confidence"] = round(float(np.min(confs)), 3) if confs else 0.0
    # CTS-2010 layout: cheque no (6) | MICR (9) | short a/c (6, optional) | txn code (2)
    six = [t for t in texts if len(t) == 6]
    nine = [t for t in texts if len(t) == 9]
    if texts and len(texts[0]) == 6:
        result["cheque_number"] = texts[0]
    elif six:
        result["cheque_number"] = six[0]
    if nine:
        result["micr_code"] = nine[0]
    if len(texts) >= 4 and len(texts[2]) == 6:
        result["short_account"] = texts[2]
    if texts and len(texts[-1]) == 2:
        result["transaction_code"] = texts[-1]
    return result


def train_from_dataset(items: list[dict]) -> tuple[MicrModel, dict]:
    """items: [{path, filename}] from the TRAIN split only."""
    samples, labels, used, skipped = [], [], 0, []
    for it in items:
        stem = Path(it["filename"]).stem
        if not (stem.isdigit() and len(stem) == 6):
            skipped.append({"file": it["filename"], "reason": "filename is not a 6-digit cheque number"})
            continue
        img = cv2.imread(str(it["path"]), cv2.IMREAD_COLOR)
        if img is None:
            skipped.append({"file": it["filename"], "reason": "unreadable"})
            continue
        groups = group_glyphs(*segment(img))
        if not groups or len(groups[0]) != 6:
            skipped.append({"file": it["filename"], "reason": f"first MICR group has {len(groups[0]) if groups else 0} glyphs"})
            continue
        for g, ch in zip(groups[0], stem):
            samples.append(g.img)
            labels.append(ch)
        used += 1
    if not samples:
        raise RuntimeError("No usable training glyphs found.")
    counts = {d: labels.count(d) for d in sorted(set(labels))}
    meta = {"train_cheques_used": used, "skipped": skipped, "glyphs": len(labels), "per_digit": counts,
            "missing_digits": [d for d in "0123456789" if d not in counts]}
    return MicrModel(np.stack(samples), labels, meta), meta
