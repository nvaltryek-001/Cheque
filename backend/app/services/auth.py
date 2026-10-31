"""Token authentication + role-based authorization.

Roles
  admin    – everything, including user management and signature enrollment
  operator – process cheques, run verifications, review results
  auditor  – read-only access to records, reports and audit log
Tokens are signed (itsdangerous) and bound to the user's token_version, so
logout / password change / deactivation invalidates every issued token.
"""
from __future__ import annotations

import functools
import secrets
import string
from datetime import datetime, timedelta, timezone

from flask import current_app, g, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash

from ..api.common import ApiError
from ..db import execute, now_iso, query

ROLES = ("admin", "operator", "auditor")
MIN_PASSWORD_LENGTH = 8


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="cv-auth")


def validate_password(password: str) -> None:
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise ApiError(400, "weak_password", f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if password.isdigit() or password.isalpha():
        raise ApiError(400, "weak_password", "Password must mix letters and numbers/symbols.")


def hash_password(password: str) -> str:
    return generate_password_hash(password)


def random_password(length: int = 14) -> str:
    alphabet = string.ascii_letters + string.digits
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(length))
        if any(c.isdigit() for c in pw) and any(c.isalpha() for c in pw):
            return pw


def issue_token(user) -> str:
    return _serializer().dumps({"uid": user["id"], "v": user["token_version"]})


def public_user(row) -> dict:
    return {
        "id": row["id"], "username": row["username"], "full_name": row["full_name"],
        "role": row["role"], "is_active": bool(row["is_active"]),
        "created_at": row["created_at"], "last_login_at": row["last_login_at"],
    }


def authenticate(username: str, password: str):
    user = query("SELECT * FROM users WHERE username = ?", (username,), one=True)
    if user is None:
        check_password_hash(generate_password_hash("x"), password or "")  # equalise timing
        raise ApiError(401, "invalid_credentials", "Invalid username or password.")
    if user["locked_until"]:
        until = datetime.fromisoformat(user["locked_until"])
        if until > datetime.now(timezone.utc):
            raise ApiError(429, "account_locked", "Too many failed attempts. Try again later.")
    if not check_password_hash(user["password_hash"], password or ""):
        failed = user["failed_logins"] + 1
        locked = None
        if failed >= current_app.config["LOGIN_MAX_ATTEMPTS"]:
            locked = (datetime.now(timezone.utc) +
                      timedelta(seconds=current_app.config["LOGIN_LOCK_SECONDS"])).replace(microsecond=0).isoformat()
            failed = 0
        execute("UPDATE users SET failed_logins=?, locked_until=? WHERE id=?", (failed, locked, user["id"]))
        raise ApiError(401, "invalid_credentials", "Invalid username or password.")
    if not user["is_active"]:
        raise ApiError(403, "account_disabled", "This account is disabled.")
    execute("UPDATE users SET failed_logins=0, locked_until=NULL, last_login_at=? WHERE id=?",
            (now_iso(), user["id"]))
    return query("SELECT * FROM users WHERE id=?", (user["id"],), one=True)


def load_user_from_request():
    header = request.headers.get("Authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else ""
    if not token:
        raise ApiError(401, "auth_required", "Authentication required.")
    try:
        payload = _serializer().loads(token, max_age=current_app.config["TOKEN_MAX_AGE_SECONDS"])
    except SignatureExpired as exc:
        raise ApiError(401, "token_expired", "Session expired. Please sign in again.") from exc
    except BadSignature as exc:
        raise ApiError(401, "invalid_token", "Invalid session token.") from exc
    user = query("SELECT * FROM users WHERE id=?", (payload.get("uid"),), one=True)
    if user is None or not user["is_active"] or user["token_version"] != payload.get("v"):
        raise ApiError(401, "invalid_token", "Session is no longer valid.")
    return user


def login_required(*roles: str):
    """Decorator. With no roles: any authenticated user. Otherwise one of the roles."""
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            user = load_user_from_request()
            if roles and user["role"] not in roles:
                raise ApiError(403, "forbidden", "Your role is not allowed to perform this action.")
            g.user = user
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def current_user_id() -> int | None:
    user = g.get("user")
    return user["id"] if user is not None else None


def bootstrap_admin(app) -> str | None:
    """Create the first admin if the users table is empty. Returns the generated
    password (only when it was generated) so it can be printed once to the console."""
    import os
    from ..db import connect
    conn = connect(str(app.config["DATABASE"]))
    try:
        if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0:
            return None
        password = os.environ.get("CV_ADMIN_PASSWORD") or random_password()
        conn.execute(
            "INSERT INTO users(username, full_name, password_hash, role, created_at) VALUES(?,?,?,?,?)",
            ("admin", "Administrator", hash_password(password), "admin", now_iso()))
        conn.commit()
        return None if os.environ.get("CV_ADMIN_PASSWORD") else password
    finally:
        conn.close()
