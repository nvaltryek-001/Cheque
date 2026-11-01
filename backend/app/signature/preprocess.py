"""Signature preprocessing.

validate -> grayscale -> background normalisation -> denoise -> contrast (CLAHE)
-> threshold (Otsu, adaptive fallback for uneven light) -> speck removal
-> foreground detection & crop -> aspect-preserving resize + padding onto a
fixed canvas -> quality report.

Strokes are kept as grey levels (not only a binary mask) so pen pressure and
stroke width information survive normalisation."""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

CANVAS_H, CANVAS_W = 128, 384

DEFAULT_CONFIG = {
    "canvas": [CANVAS_H, CANVAS_W],
    "background_kernel": 31,
    "denoise": "median3",
    "clahe_clip": 2.0,
    "min_component_area_frac": 0.00015,
    "pad_frac": 0.06,
    "min_ink_frac": 0.004,
    "max_ink_frac": 0.45,
}


@dataclass
class Processed:
    gray: np.ndarray          # CANVAS_H x CANVAS_W uint8, ink dark on white
    mask: np.ndarray          # CANVAS_H x CANVAS_W uint8, 255 = ink
    bbox: tuple[int, int, int, int]  # crop box in the source image (x, y, w, h)
    quality: dict = field(default_factory=dict)
    # full background-normalised image + ink mask before cropping/resizing; used for
    # alignment-based alteration checks (absent when Processed is built by hand)
    full_gray: np.ndarray | None = None
    full_mask: np.ndarray | None = None


class SignatureError(ValueError):
    """Raised when an image cannot be used as a signature."""


def _to_gray(img: np.ndarray) -> np.ndarray:
    if img is None or img.size == 0:
        raise SignatureError("Empty image.")
    if img.ndim == 2:
        return img
    if img.shape[2] == 4:
        alpha = img[:, :, 3:4].astype(np.float32) / 255.0
        img = (img[:, :, :3] * alpha + 255 * (1 - alpha)).astype(np.uint8)
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def _ink_mask(gray: np.ndarray, cfg: dict) -> tuple[np.ndarray, np.ndarray, str]:
    h, w = gray.shape
    k = min(cfg["background_kernel"], (min(h, w) // 2) * 2 + 1)
    k = max(3, k | 1)
    bg = cv2.medianBlur(gray, k) if k >= 3 else gray
    norm = cv2.divide(gray, np.maximum(bg, 1), scale=255)
    if cfg["denoise"] == "median3":
        norm = cv2.medianBlur(norm, 3)
    clahe = cv2.createCLAHE(clipLimit=cfg["clahe_clip"], tileGridSize=(4, 4))
    enhanced = clahe.apply(norm)
    _, otsu = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    method = "otsu"
    ink_frac = otsu.mean() / 255
    if ink_frac > cfg["max_ink_frac"]:  # uneven illumination -> adaptive
        block = max(15, (min(h, w) // 4) | 1)
        otsu = cv2.adaptiveThreshold(enhanced, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                     cv2.THRESH_BINARY_INV, block, 12)
        method = "adaptive"
    # remove specks
    n, labels, stats, _ = cv2.connectedComponentsWithStats(otsu, connectivity=8)
    min_area = max(4, int(cfg["min_component_area_frac"] * h * w))
    keep = np.zeros(n, bool)
    keep[1:] = stats[1:, cv2.CC_STAT_AREA] >= min_area
    mask = (keep[labels] * 255).astype(np.uint8)
    return mask, norm, method


def preprocess(img: np.ndarray, config: dict | None = None) -> Processed:
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    gray = _to_gray(img)
    h, w = gray.shape
    if h < 15 or w < 30:
        raise SignatureError("Image is too small to contain a signature.")
    contrast = float(gray.std())
    mask, norm, method = _ink_mask(gray, cfg)
    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        raise SignatureError("No ink detected – the image appears to be empty.")
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    crop_mask = mask[y0:y1, x0:x1]
    crop_gray = norm[y0:y1, x0:x1]
    # keep stroke grey levels, force background to white
    stroke = np.where(cv2.dilate(crop_mask, np.ones((3, 3), np.uint8)) > 0, crop_gray, 255).astype(np.uint8)

    ch, cw = crop_mask.shape
    inner_h = int(CANVAS_H * (1 - 2 * cfg["pad_frac"]))
    inner_w = int(CANVAS_W * (1 - 2 * cfg["pad_frac"]))
    scale = min(inner_h / ch, inner_w / cw)
    nh, nw = max(1, int(round(ch * scale))), max(1, int(round(cw * scale)))
    interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
    r_gray = cv2.resize(stroke, (nw, nh), interpolation=interp)
    r_mask = cv2.resize(crop_mask, (nw, nh), interpolation=cv2.INTER_NEAREST if scale >= 1 else cv2.INTER_AREA)
    r_mask = ((r_mask > 96) * 255).astype(np.uint8)
    canvas_g = np.full((CANVAS_H, CANVAS_W), 255, np.uint8)
    canvas_m = np.zeros((CANVAS_H, CANVAS_W), np.uint8)
    oy, ox = (CANVAS_H - nh) // 2, (CANVAS_W - nw) // 2
    canvas_g[oy:oy + nh, ox:ox + nw] = r_gray
    canvas_m[oy:oy + nh, ox:ox + nw] = r_mask

    ink_frac_src = float(crop_mask.mean() / 255)
    n_comp = cv2.connectedComponents(crop_mask)[0] - 1
    sharp = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    issues = []
    if mask.mean() / 255 < cfg["min_ink_frac"]:
        issues.append({"code": "too_little_ink", "message": "Very little ink detected – signature may be faint or missing."})
    if contrast < 18:
        issues.append({"code": "low_contrast", "message": "Low contrast between ink and paper."})
    if cw < 40 or ch < 12:
        issues.append({"code": "small_signature", "message": "Signature region is very small; use a higher-resolution scan."})
    if n_comp > 60:
        issues.append({"code": "noisy", "message": "Many separate fragments – image may be noisy or include printed text."})
    if sharp < 30:
        issues.append({"code": "blurry", "message": "Image appears blurry."})
    if ink_frac_src > 0.5:
        issues.append({"code": "too_much_ink", "message": "Crop is mostly ink – likely not a clean signature image."})
    score = max(0, 100 - 20 * len(issues))
    quality = {
        "score": score, "acceptable": score >= 60 and not any(i["code"] == "too_little_ink" for i in issues),
        "issues": issues, "threshold_method": method, "source_size": [w, h],
        "crop_size": [cw, ch], "ink_fraction": round(ink_frac_src, 4), "components": int(n_comp),
        "contrast": round(contrast, 1), "sharpness": round(sharp, 1),
    }
    return Processed(canvas_g, canvas_m, (x0, y0, cw, ch), quality, norm, mask)


def perceptual_hash(processed: Processed) -> str:
    small = cv2.resize(processed.gray, (17, 8), interpolation=cv2.INTER_AREA)
    bits = (small[:, 1:] > small[:, :-1]).flatten()
    return "".join("1" if b else "0" for b in bits)
