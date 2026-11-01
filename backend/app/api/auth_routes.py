"""/api/auth and /api/users"""
from __future__ import annotations

from flask import Blueprint, current_app, g

from ..db import execute, now_iso, query
from ..services import audit, email_login
from ..services.auth import (ASSIGNABLE_ROLES, ROLES, authenticate, hash_password, issue_token, login_required,
                             public_user, validate_password)
from .common import ApiError, json_body, ok, require_fields

bp = Blueprint("auth", __name__)


@bp.get("/api/auth/config")
def auth_config():
    """Public: which sign-in methods this server offers (the login screen needs it before anyone is signed in)."""
    return ok(email_login.public_config())


@bp.post("/api/auth/login")
def login():
    """Username + password ("administrator sign-in"). Switched off with CV_PASSWORD_LOGIN=0."""
    if not current_app.config.get("PASSWORD_LOGIN", True):
        raise ApiError(403, "password_signin_disabled",
                       "Username and password sign-in is switched off on this server. Sign in with your institutional email.")
    data = json_body()
    require_fields(data, "username", "password")
    user = authenticate(str(data["username"]).strip(), str(data["password"]))
    g.user = user
    audit.record("auth.login", "user", user["id"], {"method": "password"})
    return ok({"token": issue_token(user), "user": public_user(user)})


@bp.post("/api/auth/email/request")
def email_request():
    """Step 1 of the passwordless sign-in: mail a one-time code to an institutional address."""
    data = json_body()
    require_fields(data, "email")
    return ok(email_login.request_code(data["email"]))


@bp.post("/api/auth/email/verify")
def email_verify():
    """Step 2: the code proves the mailbox; the account is created on first use."""
    data = json_body()
    require_fields(data, "email", "code")
    user = email_login.verify_code(data["email"], data["code"])
    g.user = user
    audit.record("auth.login", "user", user["id"], {"method": "email"})
    return ok({"token": issue_token(user), "user": public_user(user)})


@bp.post("/api/auth/logout")
@login_required(allow_pending=True)
def logout():
    # bumping the token version revokes every token issued to this user
    execute("UPDATE users SET token_version = token_version + 1 WHERE id=?", (g.user["id"],))
    audit.record("auth.logout", "user", g.user["id"])
    return ok({"logged_out": True})


@bp.get("/api/auth/me")
@login_required(allow_pending=True)
def me():
    return ok(public_user(g.user))


@bp.post("/api/auth/change-password")
@login_required()
def change_password():
    data = json_body()
    require_fields(data, "current_password", "new_password")
    from werkzeug.security import check_password_hash

    from ..services import throttle
    key = f"pw:{g.user['id']}"            # separate namespace: usernames cannot contain ':'
    throttle.check_login(key)
    if not check_password_hash(g.user["password_hash"], str(data["current_password"])):
        throttle.record_failure(key)
        raise ApiError(400, "invalid_password", "Current password is incorrect.")
    throttle.record_success(key)
    if str(data["new_password"]) == str(data["current_password"]):
        raise ApiError(400, "weak_password", "The new password must differ from the current one.")
    validate_password(str(data["new_password"]), g.user["username"])
    execute("UPDATE users SET password_hash=?, token_version=token_version+1 WHERE id=?",
            (hash_password(str(data["new_password"])), g.user["id"]))
    audit.record("auth.change_password", "user", g.user["id"])
    user = query("SELECT * FROM users WHERE id=?", (g.user["id"],), one=True)
    return ok({"token": issue_token(user)})


# ------------------------------------------------------------------ users CRUD
@bp.get("/api/users")
@login_required("admin")
def list_users():
    rows = query("SELECT * FROM users ORDER BY id")
    return ok([public_user(r) for r in rows])


@bp.post("/api/users")
@login_required("admin")
def create_user():
    data = json_body()
    require_fields(data, "username", "password", "role")
    username = str(data["username"]).strip()
    if not username.replace("_", "").replace(".", "").isalnum() or not 3 <= len(username) <= 40:
        raise ApiError(400, "validation_error", "Username must be 3–40 letters, digits, '.' or '_'.")
    if data["role"] not in ROLES:
        raise ApiError(400, "validation_error", f"Role must be one of {', '.join(ROLES)}.")
    validate_password(str(data["password"]), username)
    if query("SELECT 1 FROM users WHERE username=?", (username,), one=True):
        raise ApiError(409, "duplicate", "That username already exists.")
    uid = execute("INSERT INTO users(username, full_name, password_hash, role, created_at) VALUES(?,?,?,?,?)",
                  (username, str(data.get("full_name", ""))[:120], hash_password(str(data["password"])),
                   data["role"], now_iso()))
    audit.record("user.create", "user", uid, {"username": username, "role": data["role"]})
    return ok(public_user(query("SELECT * FROM users WHERE id=?", (uid,), one=True)), 201)


@bp.patch("/api/users/<int:uid>")
@login_required("admin")
def update_user(uid: int):
    user = query("SELECT * FROM users WHERE id=?", (uid,), one=True)
    if user is None:
        raise ApiError(404, "not_found", "User not found.")
    data = json_body()
    sets, params, changes = [], [], {}
    if "full_name" in data:
        sets.append("full_name=?"); params.append(str(data["full_name"])[:120]); changes["full_name"] = True
    if "role" in data:
        if data["role"] not in ASSIGNABLE_ROLES:
            raise ApiError(400, "validation_error", "Invalid role.")
        if uid == g.user["id"] and data["role"] != "admin":
            raise ApiError(400, "validation_error", "You cannot remove your own admin role.")
        if data["role"] == "pending":      # withdraw access without deleting the account; the stored role is kept
            sets.append("pending_approval=1")
        else:                               # giving a role approves the account
            sets.append("role=?"); params.append(data["role"]); sets.append("pending_approval=0")
        changes["role"] = data["role"]
    if "is_active" in data:
        if uid == g.user["id"] and not data["is_active"]:
            raise ApiError(400, "validation_error", "You cannot deactivate your own account.")
        sets.append("is_active=?"); params.append(1 if data["is_active"] else 0)
        sets.append("token_version=token_version+1"); changes["is_active"] = bool(data["is_active"])
    if data.get("password"):
        if user["email"]:                   # signs in with a mailed code; a password would be a second, weaker way in
            raise ApiError(400, "validation_error",
                           "This account signs in with its institutional email and has no password to reset.")
        validate_password(str(data["password"]), user["username"])
        sets.append("password_hash=?"); params.append(hash_password(str(data["password"])))
        sets.append("token_version=token_version+1"); changes["password_reset"] = True
    if not sets:
        raise ApiError(400, "validation_error", "Nothing to update.")
    execute(f"UPDATE users SET {', '.join(sets)} WHERE id=?", (*params, uid))
    audit.record("user.update", "user", uid, changes)
    return ok(public_user(query("SELECT * FROM users WHERE id=?", (uid,), one=True)))


@bp.delete("/api/users/<int:uid>")
@login_required("admin")
def delete_user(uid: int):
    if uid == g.user["id"]:
        raise ApiError(400, "validation_error", "You cannot delete your own account.")
    if query("SELECT 1 FROM users WHERE id=?", (uid,), one=True) is None:
        raise ApiError(404, "not_found", "User not found.")
    execute("DELETE FROM users WHERE id=?", (uid,))
    audit.record("user.delete", "user", uid)
    return ok({"deleted": uid})
