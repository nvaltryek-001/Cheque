"""Central configuration. Every value can be overridden with an environment variable
(or a backend/.env file, loaded by run.py). Paths are resolved relative to the project root."""
from __future__ import annotations

import os
import secrets
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent


def _path(env: str, default: Path) -> Path:
    value = os.environ.get(env)
    if not value:
        return default
    p = Path(value)
    return p if p.is_absolute() else (PROJECT_ROOT / p)


def _int(env: str, default: int) -> int:
    try:
        return int(os.environ.get(env, default))
    except ValueError:
        return default


class Config:
    # --- storage -----------------------------------------------------------
    INSTANCE_DIR = _path("CV_INSTANCE_DIR", BACKEND_DIR / "instance")
    DATABASE = _path("CV_DATABASE", INSTANCE_DIR / "chequeverify.db")
    STORAGE_DIR = _path("CV_STORAGE_DIR", INSTANCE_DIR / "storage")
    MODELS_DIR = _path("CV_MODELS_DIR", BACKEND_DIR / "models_store")
    REPORTS_DIR = _path("CV_REPORTS_DIR", PROJECT_ROOT / "reports")

    # --- original datasets (read-only, never modified) ----------------------
    OCR_DATASET_DIR = _path("CV_OCR_DATASET", PROJECT_ROOT / "dataset")
    SIGNATURE_DATASET_DIR = _path("CV_SIGNATURE_DATASET", PROJECT_ROOT / "signature")

    # --- security ----------------------------------------------------------
    SECRET_KEY = os.environ.get("CV_SECRET_KEY", "")  # filled in create_app if empty
    TOKEN_MAX_AGE_SECONDS = _int("CV_TOKEN_MAX_AGE", 8 * 3600)
    CORS_ORIGINS = [o.strip() for o in os.environ.get(
        "CV_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if o.strip()]
    MAX_CONTENT_LENGTH = _int("CV_MAX_UPLOAD_MB", 10) * 1024 * 1024
    MAX_BATCH_FILES = _int("CV_MAX_BATCH_FILES", 20)
    # decoded size limit, checked from the file header before decoding (memory-exhaustion guard)
    MAX_IMAGE_PIXELS = _int("CV_MAX_IMAGE_PIXELS", 25_000_000)
    ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "bmp", "tif", "tiff", "webp"}
    LOGIN_MAX_ATTEMPTS = _int("CV_LOGIN_MAX_ATTEMPTS", 5)
    LOGIN_LOCK_SECONDS = _int("CV_LOGIN_LOCK_SECONDS", 300)       # sliding window for failed sign-ins
    LOGIN_IP_MAX_ATTEMPTS = _int("CV_LOGIN_IP_MAX_ATTEMPTS", 20)  # failures per client IP across all usernames
    MIN_PASSWORD_LENGTH = _int("CV_MIN_PASSWORD_LENGTH", 8)       # Render blueprint sets 12
    RATE_LIMIT_PER_MIN = _int("CV_RATE_LIMIT_PER_MIN", 120)       # expensive calls per user per minute (0 = off)
    # number of reverse proxies in front of the app (Render: 1). 0 = trust no X-Forwarded-* headers.
    PROXY_HOPS = _int("CV_PROXY_HOPS", 0)
    # Strict-Transport-Security max-age, sent only on HTTPS responses (0 = never send)
    HSTS_MAX_AGE = _int("CV_HSTS_MAX_AGE", 365 * 24 * 3600)

    # --- OCR ---------------------------------------------------------------
    OCR_ENGINE = os.environ.get("CV_OCR_ENGINE", "auto")  # auto | paddle | tesseract | none
    TESSERACT_CMD = os.environ.get("CV_TESSERACT_CMD", "")

    # --- frontend (production build served by Flask) -----------------------
    FRONTEND_DIST = _path("CV_FRONTEND_DIST", PROJECT_ROOT / "Frontend" / "dist")

    TESTING = False

    @classmethod
    def ensure_dirs(cls) -> None:
        for d in (cls.INSTANCE_DIR, cls.STORAGE_DIR, cls.MODELS_DIR):
            Path(d).mkdir(parents=True, exist_ok=True)

    @classmethod
    def resolve_secret(cls) -> str:
        """Use CV_SECRET_KEY if given, otherwise a persistent random key in instance/."""
        if cls.SECRET_KEY:
            return cls.SECRET_KEY
        key_file = Path(cls.INSTANCE_DIR) / "secret_key"
        if key_file.exists():
            return key_file.read_text().strip()
        key = secrets.token_hex(32)
        key_file.write_text(key)
        try:
            os.chmod(key_file, 0o600)
        except OSError:
            pass
        return key
