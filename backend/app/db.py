"""SQLite persistence (stdlib sqlite3, no ORM). One connection per request."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable

from flask import current_app, g

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    full_name TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin','operator','auditor')),
    is_active INTEGER NOT NULL DEFAULT 1,
    token_version INTEGER NOT NULL DEFAULT 0,
    failed_logins INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT,
    created_at TEXT NOT NULL,
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS cheques (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_filename TEXT NOT NULL,
    stored_name TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    width INTEGER, height INTEGER,
    status TEXT NOT NULL DEFAULT 'processed'
        CHECK (status IN ('processed','needs_review','reviewed','failed')),
    ocr_engine TEXT,
    quality_json TEXT,
    fields_json TEXT,          -- machine extraction (never overwritten by edits)
    corrected_json TEXT,       -- human corrections (field -> value)
    validation_json TEXT,
    raw_text TEXT,
    signature_crop_name TEXT,
    batch_id TEXT,
    notes TEXT NOT NULL DEFAULT '',
    processing_ms INTEGER,
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    reviewed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    reviewed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_cheques_created ON cheques(created_at);

CREATE TABLE IF NOT EXISTS signers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signer_code TEXT NOT NULL UNIQUE,
    full_name TEXT NOT NULL DEFAULT '',
    bank TEXT NOT NULL,
    account_number TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','inactive')),
    source TEXT NOT NULL DEFAULT 'manual',
    notes TEXT NOT NULL DEFAULT '',
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_signers_account ON signers(account_number);

CREATE TABLE IF NOT EXISTS specimens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signer_id INTEGER NOT NULL REFERENCES signers(id) ON DELETE CASCADE,
    original_filename TEXT NOT NULL,
    stored_name TEXT NOT NULL,
    processed_name TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    phash TEXT NOT NULL,
    quality_json TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','inactive')),
    replaced_by INTEGER REFERENCES specimens(id) ON DELETE SET NULL,
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_specimens_signer ON specimens(signer_id);

CREATE TABLE IF NOT EXISTS verifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cheque_id INTEGER REFERENCES cheques(id) ON DELETE SET NULL,
    signer_id INTEGER REFERENCES signers(id) ON DELETE SET NULL,
    query_name TEXT,
    decision TEXT NOT NULL CHECK (decision IN ('MATCH','REVIEW','MISMATCH')),
    score REAL,
    details_json TEXT,
    model_version TEXT,
    review_decision TEXT CHECK (review_decision IN ('APPROVED','REJECTED') OR review_decision IS NULL),
    review_note TEXT NOT NULL DEFAULT '',
    reviewed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    reviewed_at TEXT,
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,           -- signature_eval | micr_train | ocr_eval
    status TEXT NOT NULL,         -- running | completed | failed
    config_json TEXT,
    metrics_json TEXT,
    model_version TEXT,
    message TEXT NOT NULL DEFAULT '',
    started_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    username TEXT,
    action TEXT NOT NULL,
    entity TEXT,
    entity_id TEXT,
    detail TEXT,
    ip TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at);

-- failed sign-ins inside the throttling window (services/throttle.py). users.failed_logins / locked_until
-- are legacy columns from the earlier account-wide lockout and are no longer used.
CREATE TABLE IF NOT EXISTS login_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip TEXT NOT NULL,
    username TEXT NOT NULL,
    at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_login_attempts ON login_attempts(ip, username, at);
"""


def _json_default(o):
    import numpy as np
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    return str(o)


def dumps(obj) -> str:
    """json.dumps that also accepts numpy scalars/arrays."""
    return json.dumps(obj, default=_json_default)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, detect_types=0, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect(str(current_app.config["DATABASE"]))
    return g.db


def close_db(_exc: Any = None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(path: str) -> None:
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def query(sql: str, params: Iterable[Any] = (), one: bool = False):
    cur = get_db().execute(sql, tuple(params))
    rows = cur.fetchall()
    cur.close()
    if one:
        return rows[0] if rows else None
    return rows


def execute(sql: str, params: Iterable[Any] = ()) -> int:
    db = get_db()
    cur = db.execute(sql, tuple(params))
    db.commit()
    return cur.lastrowid


def row_to_dict(row: sqlite3.Row | None, json_fields: Iterable[str] = ()) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for f in json_fields:
        key = f
        if key in d:
            raw = d.pop(key)
            name = key[:-5] if key.endswith("_json") else key
            try:
                d[name] = json.loads(raw) if raw else None
            except (TypeError, ValueError):
                d[name] = None
    return d


def get_setting(key: str, default: Any = None) -> Any:
    row = query("SELECT value FROM settings WHERE key = ?", (key,), one=True)
    if row is None:
        return default
    try:
        return json.loads(row["value"])
    except ValueError:
        return default


def set_setting(key: str, value: Any) -> None:
    execute(
        "INSERT INTO settings(key, value, updated_at) VALUES(?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, json.dumps(value), now_iso()),
    )
