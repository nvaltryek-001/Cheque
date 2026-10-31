"""Private file storage for uploaded cheques and signature specimens.

Files are saved under random UUID names inside STORAGE_DIR/<area>/ – the client
filename is never used as a path. Every read goes through safe_path(), which
rejects anything that resolves outside the area directory."""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import cv2
import numpy as np
from flask import current_app
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from ..api.common import ApiError

AREAS = ("cheques", "crops", "specimens", "specimens_processed", "queries", "reports")
# image magic numbers – the extension alone is not trusted
_MAGIC = {
    b"\x89PNG\r\n\x1a\n": "png",
    b"\xff\xd8\xff": "jpeg",
    b"BM": "bmp",
    b"II*\x00": "tiff",
    b"MM\x00*": "tiff",
}


def area_dir(area: str) -> Path:
    if area not in AREAS:
        raise ValueError(f"unknown storage area {area}")
    d = Path(current_app.config["STORAGE_DIR"]) / area
    d.mkdir(parents=True, exist_ok=True)
    return d


def safe_path(area: str, name: str) -> Path:
    base = area_dir(area).resolve()
    candidate = (base / name).resolve()
    if base not in candidate.parents or not candidate.is_file():
        raise ApiError(404, "not_found", "File not found.")
    return candidate


def _sniff(data: bytes) -> str | None:
    for magic, kind in _MAGIC.items():
        if data.startswith(magic):
            return kind
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def read_upload(file: FileStorage | None, field: str = "file") -> tuple[bytes, np.ndarray, str]:
    """Validate an uploaded image. Returns (raw bytes, decoded BGR image, clean filename)."""
    if file is None or not file.filename:
        raise ApiError(400, "file_required", f"An image file is required in field '{field}'.")
    name = secure_filename(file.filename) or "upload"
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in current_app.config["ALLOWED_EXTENSIONS"]:
        raise ApiError(415, "unsupported_type",
                       "Unsupported file type. Allowed: " + ", ".join(sorted(current_app.config["ALLOWED_EXTENSIONS"])))
    data = file.read()
    if not data:
        raise ApiError(400, "empty_file", "The uploaded file is empty.")
    if len(data) > current_app.config["MAX_CONTENT_LENGTH"]:
        raise ApiError(413, "file_too_large", "The uploaded file exceeds the size limit.")
    if _sniff(data) is None:
        raise ApiError(415, "unsupported_type", "File content is not a supported image format.")
    img = decode_image(data)
    if img is None:
        raise ApiError(422, "corrupted_image", "The image could not be decoded (corrupted or truncated).")
    h, w = img.shape[:2]
    if h < 20 or w < 20:
        raise ApiError(422, "image_too_small", "The image is too small to process.")
    if h * w > 40_000_000:
        raise ApiError(413, "image_too_large", "Image resolution is too large.")
    return data, img, name


def decode_image(data: bytes) -> np.ndarray | None:
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_UNCHANGED)
    if img is None:
        return None
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    elif img.shape[2] == 4:  # composite RGBA onto white
        alpha = img[:, :, 3:4].astype(np.float32) / 255.0
        img = (img[:, :, :3].astype(np.float32) * alpha + 255 * (1 - alpha)).astype(np.uint8)
    if img.dtype != np.uint8:
        img = cv2.normalize(img, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    return img


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save_bytes(area: str, data: bytes, ext: str = "png") -> str:
    name = f"{uuid.uuid4().hex}.{ext}"
    (area_dir(area) / name).write_bytes(data)
    return name


def save_image(area: str, img: np.ndarray) -> str:
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise ApiError(500, "storage_error", "Could not encode image.")
    return save_bytes(area, buf.tobytes(), "png")


def load_image(area: str, name: str) -> np.ndarray:
    img = cv2.imread(str(safe_path(area, name)), cv2.IMREAD_COLOR)
    if img is None:
        raise ApiError(500, "storage_error", "Stored image could not be read.")
    return img


def delete(area: str, name: str | None) -> None:
    if not name:
        return
    try:
        safe_path(area, name).unlink()
    except (ApiError, OSError):
        pass
