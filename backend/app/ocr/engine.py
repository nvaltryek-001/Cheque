"""Pluggable text-recognition engines.

* PaddleOCR 3.x  – used when `paddleocr` is importable (your existing Windows env).
* Tesseract 5    – used through its command-line binary (no Python wrapper needed).
* none           – no engine available: extraction returns empty fields marked for review.

Select with CV_OCR_ENGINE=auto|paddle|tesseract|none (default auto = paddle, then tesseract).
"""
from __future__ import annotations

import csv
import io
import logging
import os
import shutil
import subprocess
import threading
from dataclasses import dataclass, field

import cv2
import numpy as np

log = logging.getLogger("chequeverify.ocr")


@dataclass
class Line:
    text: str
    conf: float      # 0..1
    x: int
    y: int
    w: int
    h: int
    words: list = field(default_factory=list)  # [(text, conf, x, y, w, h)] when the engine provides them


class BaseEngine:
    name = "none"
    available = False

    def read(self, img: np.ndarray, mode: str = "block") -> list[Line]:
        """mode: block (multi-line) | line (single line) | digits (single line of digits)."""
        return []


class TesseractEngine(BaseEngine):
    name = "tesseract"

    def __init__(self, cmd: str = ""):
        self.cmd = cmd or shutil.which("tesseract") or ""
        if not self.cmd and os.name == "nt":
            default = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
            self.cmd = default if os.path.isfile(default) else ""
        self.available = bool(self.cmd)
        self.version = ""
        if self.available:
            try:
                out = subprocess.run([self.cmd, "--version"], capture_output=True, text=True, timeout=10)
                self.version = (out.stdout or out.stderr).splitlines()[0].replace("tesseract", "").strip()
            except (OSError, subprocess.SubprocessError):
                self.available = False

    def read(self, img: np.ndarray, mode: str = "block") -> list[Line]:
        if not self.available or img is None or img.size == 0:
            return []
        psm = {"block": "6", "line": "7", "digits": "7", "page": "3", "sparse": "11"}.get(mode, "6")
        args = [self.cmd, "stdin", "stdout", "-l", "eng", "--psm", psm, "-c", "preserve_interword_spaces=1"]
        if mode == "digits":
            args += ["-c", "tessedit_char_whitelist=0123456789/-"]
        args.append("tsv")
        ok, buf = cv2.imencode(".png", img)
        if not ok:
            return []
        try:
            proc = subprocess.run(args, input=buf.tobytes(), capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("tesseract failed: %s", exc)
            return []
        return _tsv_to_lines(proc.stdout.decode("utf-8", "replace"))


def _tsv_to_lines(tsv: str) -> list[Line]:
    words: dict[tuple, list] = {}
    reader = csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE)
    for row in reader:
        try:
            if row.get("level") != "5":
                continue
            text = (row.get("text") or "").strip()
            conf = float(row.get("conf") or -1)
            if not text or conf < 0:
                continue
            key = (row["block_num"], row["par_num"], row["line_num"])
            words.setdefault(key, []).append(
                (int(row["left"]), int(row["top"]), int(row["width"]), int(row["height"]), text, conf))
        except (KeyError, ValueError):
            continue
    lines = []
    for ws in words.values():
        ws.sort(key=lambda t: t[0])
        x0 = min(w[0] for w in ws); y0 = min(w[1] for w in ws)
        x1 = max(w[0] + w[2] for w in ws); y1 = max(w[1] + w[3] for w in ws)
        lines.append(Line(" ".join(w[4] for w in ws), float(np.mean([w[5] for w in ws])) / 100.0,
                          x0, y0, x1 - x0, y1 - y0,
                          [(w[4], w[5] / 100.0, w[0], w[1], w[2], w[3]) for w in ws]))
    lines.sort(key=lambda l: (l.y, l.x))
    return lines


class PaddleEngine(BaseEngine):
    """Adapter for PaddleOCR 3.x (`PaddleOCR(...).predict()` API).

    Not exercised in the cloud build environment (PaddleOCR was not installable there);
    it follows the documented 3.x result format: rec_texts / rec_scores / rec_polys."""
    name = "paddleocr"

    def __init__(self):
        self._ocr = None
        self._lock = threading.Lock()
        try:
            import paddleocr  # noqa: F401
            self.available = True
            self.version = getattr(paddleocr, "__version__", "")
        except Exception:  # pragma: no cover - depends on local install
            self.available = False
            self.version = ""

    def _get(self):
        if self._ocr is None:
            from paddleocr import PaddleOCR
            self._ocr = PaddleOCR(lang="en", use_doc_orientation_classify=False,
                                  use_doc_unwarping=False, use_textline_orientation=False)
        return self._ocr

    def read(self, img: np.ndarray, mode: str = "block") -> list[Line]:  # pragma: no cover
        if not self.available or img is None or img.size == 0:
            return []
        if img.ndim == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        with self._lock:
            results = self._get().predict(img)
        lines = []
        for res in results or []:
            data = res.json.get("res", res.json) if hasattr(res, "json") else res
            texts = data.get("rec_texts", []) or []
            scores = data.get("rec_scores", []) or []
            polys = data.get("rec_polys", data.get("dt_polys", [])) or []
            for text, score, poly in zip(texts, scores, polys):
                pts = np.array(poly).reshape(-1, 2)
                x0, y0 = pts.min(0); x1, y1 = pts.max(0)
                if mode == "digits":
                    text = "".join(c for c in text if c.isdigit() or c in "/-")
                lines.append(Line(str(text), float(score), int(x0), int(y0), int(x1 - x0), int(y1 - y0)))
        lines.sort(key=lambda l: (l.y, l.x))
        return lines


_ENGINE: BaseEngine | None = None
_ENGINE_LOCK = threading.Lock()


def get_engine(preference: str = "auto", tesseract_cmd: str = "") -> BaseEngine:
    global _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is not None and (preference == "auto" or _ENGINE.name.startswith(preference[:5])):
            return _ENGINE
        engine: BaseEngine = BaseEngine()
        if preference in ("auto", "paddle"):
            p = PaddleEngine()
            if p.available:
                engine = p
        if not engine.available and preference in ("auto", "tesseract"):
            t = TesseractEngine(tesseract_cmd)
            if t.available:
                engine = t
        _ENGINE = engine
        log.info("OCR engine selected: %s", engine.name)
        return engine


def reset_engine() -> None:
    global _ENGINE
    _ENGINE = None
