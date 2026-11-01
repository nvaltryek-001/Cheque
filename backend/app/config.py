"""Central configuration. Every value can be overridden with an environment variable
(or a backend/.env file, loaded by run.py). Paths are resolved relative to the project root."""
from __future__ import annotations

import logging
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
    # Set by docker_entrypoint.py when /data is not a mounted disk or volume (Render Free plan): everything stored is lost
    # when the container restarts, and the sign-in screen and the application say so. CV_STORAGE_TEMPORARY=0/1 overrides.
    STORAGE_TEMPORARY = os.environ.get("CV_STORAGE_TEMPORARY", "").strip().lower() in ("1", "true", "yes", "on")

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
    # images processed at the same moment (memory ceiling = this x memory per image; see app/services/limits.py)
    MAX_CONCURRENT_IMAGES = _int("CV_MAX_CONCURRENT_IMAGES", 2)
    IMAGE_QUEUE_SECONDS = _int("CV_IMAGE_QUEUE_SECONDS", 30)         # how long a request waits for a slot before "503 busy"
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

    # --- sign-in by institutional email (passwordless; see docs/AUTHENTICATION.md) ------------
    # Anyone with a mailbox in one of these domains may ask for a code; the emailed one-time code proves they
    # control the mailbox. Matching is exact: sub-domains are not included. Comma separated.
    EMAIL_DOMAINS = [d.strip().lower() for d in os.environ.get("CV_EMAIL_DOMAINS", "saividya.ac.in").split(",") if d.strip()]
    # How the code reaches the person. "smtp" = real mail (CV_SMTP_*). "log" = DEVELOPMENT ONLY: the code is
    # written to the server log, so anyone who can read the log can sign in. "" = email sign-in is switched off.
    EMAIL_DELIVERY = os.environ.get("CV_EMAIL_DELIVERY", "").strip().lower()
    SMTP_HOST = os.environ.get("CV_SMTP_HOST", "").strip()
    SMTP_PORT = _int("CV_SMTP_PORT", 587)
    SMTP_SECURITY = os.environ.get("CV_SMTP_SECURITY", "starttls").strip().lower()   # starttls | ssl | none (loopback only)
    SMTP_USER = os.environ.get("CV_SMTP_USER", "").strip()                           # the password is read from CV_SMTP_PASSWORD when sending
    SMTP_FROM = os.environ.get("CV_SMTP_FROM", "").strip()
    EMAIL_CODE_TTL_SECONDS = _int("CV_EMAIL_CODE_TTL", 600)
    EMAIL_CODE_MAX_ATTEMPTS = _int("CV_EMAIL_CODE_ATTEMPTS", 5)          # wrong guesses before a code is burned
    EMAIL_REQUEST_WINDOW_SECONDS = _int("CV_EMAIL_REQUEST_WINDOW", 900)
    EMAIL_REQUESTS_PER_ADDRESS = _int("CV_EMAIL_REQUESTS_PER_ADDRESS", 5)   # codes per address per window
    EMAIL_REQUESTS_PER_IP = _int("CV_EMAIL_REQUESTS_PER_IP", 20)            # codes per client IP per window
    EMAIL_RESEND_SECONDS = _int("CV_EMAIL_RESEND_SECONDS", 30)               # minimum gap between two codes for one address
    # What a first-time email sign-in becomes: "pending" (signed in, sees nothing until an administrator
    # approves), or "auditor" / "operator" (immediately active with that role). Never "admin".
    EMAIL_DEFAULT_ROLE = os.environ.get("CV_EMAIL_DEFAULT_ROLE", "pending").strip().lower()
    # Addresses that become administrators when they sign in (after proving the mailbox). Not a list of who may sign in.
    ADMIN_EMAILS = [e.strip().lower() for e in os.environ.get("CV_ADMIN_EMAILS", "").split(",") if e.strip()]
    # Username + password sign-in ("Administrator sign-in"). Switch off once email administrators exist.
    PASSWORD_LOGIN = os.environ.get("CV_PASSWORD_LOGIN", "1").strip().lower() not in ("0", "false", "no", "off")

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
        from .services import atomic
        key_file = Path(cls.INSTANCE_DIR) / "secret_key"
        if key_file.exists():
            existing = key_file.read_text().strip()
            if len(existing) >= 32:
                return existing
            # Empty or cut short (a crash while the file was first written): a short key would make session tokens
            # easy to forge, so it is replaced. Sessions signed with the old value stop working; nothing else is lost.
            logging.getLogger("chequeverify").warning("secret_key in %s was empty or too short and was replaced", cls.INSTANCE_DIR)
        key = secrets.token_hex(32)
        atomic.write_text(key_file, key)
        return key
