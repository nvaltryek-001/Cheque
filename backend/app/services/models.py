"""Loads trained artefacts (MICR templates, signature fusion model) with caching."""
from __future__ import annotations

import threading
from pathlib import Path

from flask import current_app

from ..ocr.engine import get_engine
from ..ocr.micr import MicrModel
from ..signature.compare import FusionModel

MICR_FILE = "micr_e13b.npz"
FUSION_FILE = "signature_fusion.json"

_lock = threading.Lock()
_cache: dict = {}


def _path(name: str) -> Path:
    return Path(current_app.config["MODELS_DIR"]) / name


def _load(name: str, loader):
    p = _path(name)
    mtime = p.stat().st_mtime if p.is_file() else None
    with _lock:
        hit = _cache.get(str(p))
        if hit and hit[0] == mtime:
            return hit[1]
        model = loader(p) if mtime else None
        _cache[str(p)] = (mtime, model)
        return model


def micr_model() -> MicrModel | None:
    return _load(MICR_FILE, MicrModel.load)


def fusion_model() -> FusionModel | None:
    return _load(FUSION_FILE, FusionModel.load)


def ocr_engine():
    return get_engine(current_app.config["OCR_ENGINE"], current_app.config["TESSERACT_CMD"])


def status() -> dict:
    eng = ocr_engine()
    micr = micr_model()
    fusion = fusion_model()
    return {
        "ocr_engine": {"name": eng.name, "available": eng.available, "version": getattr(eng, "version", "")},
        "micr_model": {"available": micr is not None,
                       "trained_cheques": (micr.meta or {}).get("train_cheques_used") if micr else None,
                       "glyphs": (micr.meta or {}).get("glyphs") if micr else None},
        "signature_model": {"available": fusion is not None,
                            "version": fusion.version if fusion else "baseline-mean-v1",
                            "thresholds": fusion.thresholds if fusion else None,
                            "trained_at": fusion.meta.get("trained_at") if fusion else None},
    }
