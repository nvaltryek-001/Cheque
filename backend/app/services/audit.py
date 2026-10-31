"""Append-only audit trail of sensitive actions. Never logs image content,
passwords or full account numbers."""
from __future__ import annotations

import json
import logging

from flask import g, has_request_context, request

from ..db import execute, now_iso

log = logging.getLogger("chequeverify.audit")


def mask_account(value: str | None) -> str:
    v = (value or "").strip()
    if len(v) <= 4:
        return "*" * len(v)
    return "*" * (len(v) - 4) + v[-4:]


def record(action: str, entity: str | None = None, entity_id=None, detail=None) -> None:
    user = g.get("user") if has_request_context() else None
    ip = request.remote_addr if has_request_context() else None
    text = json.dumps(detail, default=str) if isinstance(detail, (dict, list)) else (detail or "")
    execute(
        "INSERT INTO audit_log(user_id, username, action, entity, entity_id, detail, ip, created_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (user["id"] if user else None, user["username"] if user else None, action, entity,
         str(entity_id) if entity_id is not None else None, text[:2000], ip, now_iso()),
    )
    log.info("audit action=%s entity=%s id=%s user=%s", action, entity, entity_id,
             user["username"] if user else "-")
