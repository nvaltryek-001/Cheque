"""Token authentication + role-based authorization.

Roles
  admin    – everything, including user management and signature enrollment
  operator – process cheques, run verifications, review results
  auditor  – read-only access to records, reports and audit log
  pending  – (shown role, stored as users.pending_approval) signed in by email but not yet approved: may only see
             who they are and sign out; every other endpoint answers 403 until an administrator gives a role
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
from . import audit, throttle

ROLES = ("admin", "operator", "auditor")
ASSIGNABLE_ROLES = ROLES + ("pending",)   # what an administrator may set on an existing account
MIN_PASSWORD_LENGTH = 8   # default; the effective value is current_app.config["MIN_PASSWORD_LENGTH"]


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="cv-auth")


# Passwords that are rejected whatever their length (product name, defaults people try first).
COMMON_PASSWORDS = {"password", "passw0rd", "password1", "password123", "admin123", "admin@123", "administrator",
                    "welcome1", "welcome123", "changeme", "change-me-123", "letmein", "qwerty123", "chequeverify",
                    "chequeverify1", "chequeverify123", "iloveyou1", "abc12345", "12345678a"}


def password_problem(password: str, minimum: int, username: str = "") -> str | None:
    """Why `password` is not acceptable (None = acceptable). Pure function: no request context needed."""
    password = password or ""
    if len(password) < minimum:
        return f"Password must be at least {minimum} characters."
    if password.isdigit() or password.isalpha():
        return "Password must mix letters and numbers/symbols."
    if password.lower() in COMMON_PASSWORDS or (username and password.lower() == username.strip().lower()):
        return "Password is too common or equals the username."
    return None


def minimum_password_length(config) -> int:
    return max(MIN_PASSWORD_LENGTH, int(config.get("MIN_PASSWORD_LENGTH") or 0))


def validate_password(password: str, username: str = "") -> None:
    problem = password_problem(password, minimum_password_length(current_app.config), username)
    if problem:
        raise ApiError(400, "weak_password", problem)


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
        "id": row["id"], "username": row["username"], "email": row["email"], "full_name": row["full_name"],
        "role": "pending" if row["pending_approval"] else row["role"], "is_active": bool(row["is_active"]),
        "created_at": row["created_at"], "last_login_at": row["last_login_at"],
    }


def authenticate(username: str, password: str):
    """Check credentials behind the per-client throttle (services/throttle.py). A blocked client is refused
    before the password is looked at; failures are recorded per client IP and username, never per account,
    so outsiders cannot lock a real user out."""
    throttle.check_login(username)
    user = query("SELECT * FROM users WHERE username = ?", (username,), one=True)
    if user is None:
        check_password_hash(generate_password_hash("x"), password or "")  # equalise timing
        _failed(username, None)
        raise ApiError(401, "invalid_credentials", "Invalid username or password.")
    if not check_password_hash(user["password_hash"], password or ""):
        _failed(username, user)
        raise ApiError(401, "invalid_credentials", "Invalid username or password.")
    if not user["is_active"]:
        raise ApiError(403, "account_disabled", "This account is disabled.")
    throttle.record_success(username)
    execute("UPDATE users SET last_login_at=? WHERE id=?", (now_iso(), user["id"]))
    return query("SELECT * FROM users WHERE id=?", (user["id"],), one=True)


def _failed(username: str, user) -> None:
    """Record a failed sign-in and audit it. Only names of existing accounts are written to the audit log:
    people sometimes type their password into the username box."""
    blocked_now = throttle.record_failure(username)
    who = user["username"] if user else "(unknown)"
    audit.record("auth.login_failed", "user", user["id"] if user else None, {"username": who})
    if blocked_now:
        audit.record("auth.login_throttled", "user", user["id"] if user else None,
                     {"username": who, "window_seconds": int(current_app.config["LOGIN_LOCK_SECONDS"])})


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


def login_required(*roles: str, allow_pending: bool = False):
    """Decorator. With no roles: any approved, authenticated user. Otherwise one of the roles. An account that
    signed in by email but has not been approved yet is refused everywhere except where allow_pending is set
    (who-am-I and sign-out)."""
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            user = load_user_from_request()
            if user["pending_approval"] and not allow_pending:
                raise ApiError(403, "approval_pending", "Your account is waiting for an administrator to approve it.")
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
        if os.environ.get("CV_ADMIN_PASSWORD"):   # an operator-chosen first password obeys the same policy
            problem = password_problem(password, minimum_password_length(app.config), "admin")
            if problem:
                raise RuntimeError("CV_ADMIN_PASSWORD is not acceptable: " + problem +
                                   " Choose a stronger first-admin password (or unset it to get a random one).")
        conn.execute(
            "INSERT INTO users(username, full_name, password_hash, role, created_at) VALUES(?,?,?,?,?)",
            ("admin", "Administrator", hash_password(password), "admin", now_iso()))
        conn.commit()
        return None if os.environ.get("CV_ADMIN_PASSWORD") else password
    finally:
        conn.close()
