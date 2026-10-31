"""Image-quality checks for cheque and signature images."""
from __future__ import annotations

import cv2
import numpy as np


def assess_cheque(img: np.ndarray) -> dict:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    scale = 1024 / w
    g = cv2.resize(gray, (1024, max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
    sharpness = float(cv2.Laplacian(g, cv2.CV_64F).var())
    brightness = float(g.mean())
    contrast = float(g.std())
    aspect = w / h
    issues = []
    if w < 800:
        issues.append({"code": "low_resolution", "message": f"Width {w}px is low; 1000px+ recommended."})
    if sharpness < 60:
        issues.append({"code": "blurry", "message": "Image appears blurry."})
    if brightness < 90:
        issues.append({"code": "too_dark", "message": "Image is too dark."})
    if brightness > 245:
        issues.append({"code": "too_bright", "message": "Image is washed out."})
    if contrast < 25:
        issues.append({"code": "low_contrast", "message": "Image has low contrast."})
    if not 1.9 <= aspect <= 2.7:
        issues.append({"code": "unusual_aspect", "message": f"Aspect ratio {aspect:.2f} is unusual for a CTS-2010 cheque (~2.2)."})
    score = 100
    score -= 25 * sum(i["code"] in ("blurry", "low_resolution") for i in issues)
    score -= 15 * sum(i["code"] in ("too_dark", "too_bright", "low_contrast") for i in issues)
    score -= 20 * sum(i["code"] == "unusual_aspect" for i in issues)
    return {
        "width": w, "height": h, "sharpness": round(sharpness, 1), "brightness": round(brightness, 1),
        "contrast": round(contrast, 1), "aspect_ratio": round(aspect, 3),
        "score": max(0, score), "acceptable": max(0, score) >= 50, "issues": issues,
    }
