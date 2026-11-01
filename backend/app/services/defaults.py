"""Default runtime settings stored in the settings table (editable by admins)."""
from __future__ import annotations

import json

from ..db import connect, now_iso

DEFAULT_SETTINGS = {
    # null = use the thresholds chosen on the validation split by the trained model
    "signature.match_threshold": None,
    "signature.mismatch_threshold": None,
    "signature.max_specimens_per_signer": 20,
    # Reference-set policy: a MATCH is only trusted when the signer's specimens are independent samples
    # (different recorded signing days, not cut from the same page). See services/enrollment.readiness.
    "enrollment.min_specimens": 5,
    "enrollment.min_sessions": 2,
    "enrollment.cap_match_when_not_ready": True,
    "signature.duplicate_hamming": 6,
    "ocr.auto_signature_crop": True,
    "retention.days": 0,  # 0 = keep until deleted manually
}


def ensure_default_settings(app) -> None:
    conn = connect(str(app.config["DATABASE"]))
    try:
        for k, v in DEFAULT_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO settings(key, value, updated_at) VALUES(?,?,?)",
                         (k, json.dumps(v), now_iso()))
        conn.commit()
    finally:
        conn.close()
