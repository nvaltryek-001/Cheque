"""Loads trained artefacts (MICR templates, signature fusion model) with caching."""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from flask import current_app

from ..ocr.engine import get_engine
from ..ocr.micr import MicrModel
from ..signature.compare import FusionModel

MICR_FILE = "micr_e13b.npz"
FUSION_FILE = "signature_fusion.json"

log = logging.getLogger("chequeverify.models")
_lock = threading.Lock()
_cache: dict = {}
_damaged: dict = {}          # path -> error text, for model files that exist but cannot be read


def _path(name: str) -> Path:
    return Path(current_app.config["MODELS_DIR"]) / name


def _load(name: str, loader):
    """Load a model file once per version of that file.

    A file that exists but cannot be read (a disk fault, a copy that was cut short, a file edited by hand) must not turn
    every cheque and every verification into a server error. It is logged once, reported by `status()` and treated as
    "no trained model": cheques fall back to the general OCR engine and verification to the untrained baseline, and the
    result says so (the model version is recorded with each decision). Retraining or restoring a backup fixes it."""
    p = _path(name)
    mtime = p.stat().st_mtime if p.is_file() else None
    with _lock:
        hit = _cache.get(str(p))
        if hit and hit[0] == mtime:
            return hit[1]
        model = None
        _damaged.pop(str(p), None)
        if mtime:
            try:
                model = loader(p)
            except Exception as exc:                       # noqa: BLE001 - any failure to parse means "unusable"
                _damaged[str(p)] = f"{type(exc).__name__}: the file could not be read"
                log.error("model file %s exists but could not be loaded (%s); running without it", p.name, type(exc).__name__)
        _cache[str(p)] = (mtime, model)
        return model


def _problem(name: str) -> str | None:
    return _damaged.get(str(_path(name)))


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
        "micr_model": {"available": micr is not None, "error": _problem(MICR_FILE),
                       "trained_cheques": (micr.meta or {}).get("train_cheques_used") if micr else None,
                       "glyphs": (micr.meta or {}).get("glyphs") if micr else None},
        "signature_model": {"available": fusion is not None, "error": _problem(FUSION_FILE),
                            "version": fusion.version if fusion else "baseline-mean-v1",
                            "thresholds": fusion.thresholds if fusion else None,
                            "trained_at": fusion.meta.get("trained_at") if fusion else None},
    }
