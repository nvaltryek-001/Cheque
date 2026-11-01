"""Deterministic synthetic inputs shared by the golden-value generator and the OpenCV
compatibility test. No dataset images are used, so this runs in CI."""
from pathlib import Path

import cv2
import numpy as np

from app.ocr.micr import GLYPH_H, MicrModel

MODELS = Path(__file__).resolve().parent.parent / "models_store"


def signature(seed: int, w: int = 320, h: int = 100) -> np.ndarray:
    rng = np.random.default_rng(seed)
    img = np.full((h, w, 3), 245, np.uint8)
    pts = np.cumsum(rng.normal(0, 6, (40, 2)), axis=0)
    pts = pts - pts.min(0)
    pts = pts / max(pts.max(), 1) * [w - 40, h - 30] + [20, 15]
    cv2.polylines(img, [pts.astype(np.int32)], False, (30, 30, 120), 2, cv2.LINE_AA)
    cv2.line(img, (25, h - 20), (w - 30, h - 25), (30, 30, 120), 2)
    return img


def signature_pairs():
    a, b = signature(1), signature(2)
    shifted = cv2.warpAffine(a, np.float32([[1, 0, 4], [0, 1, 2]]), (a.shape[1], a.shape[0]),
                             borderValue=(245, 245, 245))
    blurred = cv2.GaussianBlur(a, (0, 0), 0.8)
    return {"identical": (a, a), "shifted": (shifted, a), "blurred": (blurred, a), "different": (b, a)}


def micr_cheque(text: str = "123456 999888777 135790 31") -> np.ndarray:
    """Render a cheque-sized white page whose MICR band is drawn from the stored glyph templates."""
    model = MicrModel.load(MODELS / "micr_e13b.npz")
    page = np.full((476, 1024, 3), 250, np.uint8)
    glyph = {}
    for img, lab in zip(model.samples.reshape(-1, GLYPH_H, model.samples.shape[1] // GLYPH_H), model.labels):
        glyph.setdefault(lab, img)
    x, y = 260, 425
    for ch in text:
        if ch == " ":
            x += 34
            continue
        g = (glyph[ch] > 0.5).astype(np.uint8)
        ys, xs = np.nonzero(g)
        g = g[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
        g = cv2.resize(g, (max(1, int(g.shape[1] * 16 / g.shape[0])), 16), interpolation=cv2.INTER_NEAREST)
        page[y:y + g.shape[0], x:x + g.shape[1]][g > 0] = (20, 20, 20)
        x += 15
    return page
