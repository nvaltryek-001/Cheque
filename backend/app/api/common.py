"""Shared API helpers: consistent JSON envelope, errors, pagination, validation."""
from __future__ import annotations

from typing import Any

from flask import jsonify, request


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Any = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details


def ok(data: Any = None, status: int = 200, **meta: Any):
    body: dict[str, Any] = {"ok": True, "data": data}
    if meta:
        body["meta"] = meta
    return jsonify(body), status


def error_response(status: int, code: str, message: str, details: Any = None):
    body: dict[str, Any] = {"ok": False, "error": {"code": code, "message": message}}
    if details is not None:
        body["error"]["details"] = details
    return jsonify(body), status


def json_body() -> dict:
    data = request.get_json(silent=True)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ApiError(400, "invalid_json", "Request body must be a JSON object.")
    return data


def require_fields(data: dict, *names: str) -> None:
    missing = [n for n in names if not str(data.get(n, "")).strip()]
    if missing:
        raise ApiError(400, "validation_error", f"Missing required field(s): {', '.join(missing)}",
                       {"missing": missing})


def pagination() -> tuple[int, int]:
    try:
        page = max(1, int(request.args.get("page", 1)))
        per_page = min(100, max(1, int(request.args.get("per_page", 20))))
    except ValueError as exc:
        raise ApiError(400, "validation_error", "page and per_page must be integers.") from exc
    return page, per_page


def int_arg(name: str, default: int | None = None) -> int | None:
    raw = request.args.get(name)
    if raw in (None, ""):
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ApiError(400, "validation_error", f"{name} must be an integer.") from exc
