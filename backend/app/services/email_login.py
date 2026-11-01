"""Passwordless sign-in with an institutional email address.

    POST /api/auth/email/request  {email}        -> a one-time code is emailed to that address
    POST /api/auth/email/verify   {email, code}  -> the same {token, user} answer as the password sign-in

Who may ask for a code: any address in an allowed domain (CV_EMAIL_DOMAINS, default saividya.ac.in) – there is no
list of people. Typing an address is not proof that it is yours, so nobody is signed in until they enter the code that
was mailed to it. A person signing in for the first time gets an account automatically; what that account may do is
decided by CV_EMAIL_DEFAULT_ROLE (default "pending": signed in, but no data until an administrator approves) and by
CV_ADMIN_EMAILS (addresses that become administrators once they have proved the mailbox).

The code: 8 digits from the system random generator; only a keyed hash (HMAC-SHA-256 with the server secret) is
stored; it works once, for CV_EMAIL_CODE_TTL seconds (default 600), and is burned after CV_EMAIL_CODE_ATTEMPTS wrong
tries (default 5). Asking for a new code retires the old one. Requests are limited per address and per client IP,
wrong codes feed the same persisted sign-in throttle as wrong passwords, and the answers never reveal whether an
account exists.

Limits: this proves control of a mailbox, not who is sitting at the keyboard. Anyone who can read a user's mail (or a
shared / forwarding mailbox) can sign in as that user, and a lost session token is as good as the mailbox until it
expires (CV_TOKEN_MAX_AGE). docs/AUTHENTICATION.md lists what this does and does not protect against.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import math
import re
import secrets
import sqlite3
import time

from flask import current_app

from ..api.common import ApiError
from ..db import execute, get_db, now_iso, query
from . import audit, mailer, throttle
from .auth import hash_password

log = logging.getLogger("chequeverify.email_login")

CODE_DIGITS = 8
INITIAL_ROLES = ("operator", "auditor")          # roles a first sign-in may receive without approval
_LOCAL = re.compile(r"[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?")


def email_problem(raw, domains) -> tuple[str | None, str | None]:
    """(normalised address, None) when `raw` is an acceptable address in one of `domains`, else (None, reason).
    Pure function: ASCII only, exactly one '@', the domain must equal an allowed domain (so neither
    'x.saividya.ac.in' nor 'saividya.ac.in.evil.example' pass), local part of letters, digits, '.', '_' and '-'."""
    if not isinstance(raw, str):
        return None, "Enter your institutional email address."
    value = raw.strip().lower()
    if not value:
        return None, "Enter your institutional email address."
    if len(value) > 254 or not value.isascii() or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value) \
            or value.count("@") != 1:
        return None, "Enter a valid email address."
    local, domain = value.split("@")
    if domain not in domains:
        shown = " or ".join(f"name@{d}" for d in domains[:2]) or "an institutional address"
        return None, f"Use your institutional email address ({shown})."
    if ".." in local or not _LOCAL.fullmatch(local):
        return None, "Enter a valid email address."
    return value, None


def normalize(raw) -> str:
    email, problem = email_problem(raw, list(current_app.config["EMAIL_DOMAINS"]))
    if problem:
        raise ApiError(400, "invalid_email", problem)
    return email  # type: ignore[return-value]


def mask(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[:2]}***@{domain}"


def enabled() -> bool:
    return bool(current_app.config["EMAIL_DOMAINS"]) and mailer.mode() != ""


def public_config() -> dict:
    cfg = current_app.config
    domains = list(cfg["EMAIL_DOMAINS"])
    return {"email_signin": enabled(), "email_domain": domains[0] if domains else None, "email_domains": domains,
            "password_signin": bool(cfg["PASSWORD_LOGIN"]), "code_length": CODE_DIGITS,
            "code_ttl_seconds": int(cfg["EMAIL_CODE_TTL_SECONDS"]), "resend_seconds": int(cfg["EMAIL_RESEND_SECONDS"]),
            "storage_temporary": bool(cfg.get("STORAGE_TEMPORARY", False))}


def _digest(email: str, code: str) -> str:
    key = str(current_app.config["SECRET_KEY"]).encode()
    return hmac.new(key, f"cv-email-code\x00{email}\x00{code}".encode(), hashlib.sha256).hexdigest()


def _wait(first_expiry: float, now: float) -> int:
    return max(1, math.ceil(first_expiry - now))


# ----------------------------------------------------------------------------- step 1: ask for a code
def request_code(raw_email) -> dict:
    if not enabled():
        raise ApiError(503, "email_signin_unavailable",
                       "Email sign-in is not set up on this server. Ask an administrator or use the administrator sign-in.")
    email = normalize(raw_email)
    cfg = current_app.config
    ip, now = throttle.client_ip(), time.time()
    window = int(cfg["EMAIL_REQUEST_WINDOW_SECONDS"])
    execute("DELETE FROM login_codes WHERE created_at < ?", (now - max(window, 86400),))
    mine = [r["created_at"] for r in query("SELECT created_at FROM login_codes WHERE email=? AND created_at>? "
                                           "ORDER BY created_at DESC", (email, now - window))]
    from_ip = [r["created_at"] for r in query("SELECT created_at FROM login_codes WHERE ip=? AND created_at>? "
                                              "ORDER BY created_at DESC", (ip, now - window))]
    gap = int(cfg["EMAIL_RESEND_SECONDS"])
    if mine and now - mine[0] < gap:
        wait = _wait(mine[0] + gap, now)
        raise ApiError(429, "resend_too_soon", f"A code was sent a moment ago. You can ask for another in {wait} seconds.",
                       {"retry_after": wait})
    for times, limit in ((mine, int(cfg["EMAIL_REQUESTS_PER_ADDRESS"])), (from_ip, int(cfg["EMAIL_REQUESTS_PER_IP"]))):
        if limit > 0 and len(times) >= limit:
            wait = _wait(times[limit - 1] + window, now)
            raise ApiError(429, "too_many_requests", f"Too many codes requested. Try again in {wait} seconds.",
                           {"retry_after": wait})
    code = f"{secrets.randbelow(10 ** CODE_DIGITS):0{CODE_DIGITS}d}"
    ttl = int(cfg["EMAIL_CODE_TTL_SECONDS"])
    code_id = execute("INSERT INTO login_codes(email, code_hash, ip, created_at, expires_at) VALUES(?,?,?,?,?)",
                      (email, _digest(email, code), ip, now, now + ttl))
    # One live code per address: every older code is retired after the new one exists, so the newest wins even when
    # two requests arrive together and an older code can never come back to life.
    execute("UPDATE login_codes SET consumed_at=? WHERE email=? AND consumed_at IS NULL AND id<?", (now, email, code_id))
    try:
        mailer.send_login_code(email, code)
    except mailer.DeliveryError:
        execute("UPDATE login_codes SET consumed_at=? WHERE id=?", (now, code_id))
        audit.record("auth.email_code_failed", "user", None, {"email": mask(email), "reason": "delivery"})
        raise ApiError(503, "email_delivery_failed",
                       "We could not send the code. Try again in a few minutes or contact an administrator.") from None
    audit.record("auth.email_code_sent", "user", None, {"email": mask(email)})
    return {"sent": True, "email": email, "expires_in": ttl, "resend_after": gap, "code_length": CODE_DIGITS}


# ----------------------------------------------------------------------------- step 2: prove the mailbox
def _reject(email: str, key: str, reason: str, attempts_left: int | None = None):
    throttle.record_failure(key)
    audit.record("auth.email_code_failed", "user", None, {"email": mask(email), "reason": reason})
    details = {"attempts_left": attempts_left} if attempts_left is not None else None
    raise ApiError(401, "invalid_code", "That code is wrong or has expired. Check the latest email or request a new code.",
                   details)


def verify_code(raw_email, raw_code):
    """Check the code and return the user row (created on first use). Raises ApiError otherwise."""
    if not enabled():
        raise ApiError(503, "email_signin_unavailable", "Email sign-in is not set up on this server.")
    email = normalize(raw_email)
    key = "email:" + email                      # separate namespace: usernames cannot contain ':'
    throttle.check_login(key)
    given = re.sub(r"[\s-]", "", str(raw_code or ""))
    now = time.time()
    row = query("SELECT * FROM login_codes WHERE email=? AND consumed_at IS NULL ORDER BY created_at DESC LIMIT 1",
                (email,), one=True)
    if row is None:
        _reject(email, key, "no_code")
    if row["expires_at"] < now:
        execute("UPDATE login_codes SET consumed_at=? WHERE id=?", (now, row["id"]))
        _reject(email, key, "expired")
    max_attempts = int(current_app.config["EMAIL_CODE_MAX_ATTEMPTS"])
    db = get_db()
    # Reserve one guess before looking at the code. SQLite applies a conditional UPDATE atomically, so however many
    # requests arrive at the same moment, at most `max_attempts` of them are ever allowed to compare a code.
    reserved = db.execute("UPDATE login_codes SET attempts = attempts + 1 "
                          "WHERE id=? AND consumed_at IS NULL AND attempts < ?", (row["id"], max_attempts)).rowcount
    db.commit()
    if reserved != 1:                           # every guess is used (or the code was just consumed by another request)
        execute("UPDATE login_codes SET consumed_at=? WHERE id=? AND consumed_at IS NULL", (now, row["id"]))
        _reject(email, key, "too_many_attempts")
    used = query("SELECT attempts FROM login_codes WHERE id=?", (row["id"],), one=True)["attempts"]
    well_formed = len(given) == CODE_DIGITS and given.isdigit()
    if not (well_formed and hmac.compare_digest(row["code_hash"], _digest(email, given))):
        if used >= max_attempts:
            execute("UPDATE login_codes SET consumed_at=? WHERE id=? AND consumed_at IS NULL", (now, row["id"]))
        _reject(email, key, "wrong_code", max(0, max_attempts - used))
    cur = db.execute("UPDATE login_codes SET consumed_at=? WHERE id=? AND consumed_at IS NULL", (now, row["id"]))
    db.commit()
    if cur.rowcount != 1:                       # another request used the code first
        _reject(email, key, "already_used")
    throttle.record_success(key)
    return provision(email)


# ----------------------------------------------------------------------------- the account behind an address
def _initial_role(email: str) -> tuple[str, int]:
    """(role stored, pending_approval) for an account that does not exist yet."""
    cfg = current_app.config
    if email in set(cfg["ADMIN_EMAILS"]):
        return "admin", 0
    default = str(cfg["EMAIL_DEFAULT_ROLE"] or "pending")
    if default in INITIAL_ROLES:
        return default, 0
    return "auditor", 1                         # pending: the least-privileged role, locked behind the approval flag


def provision(email: str):
    """The user for a verified address; created on first sign-in. Raises 403 for a disabled account."""
    user = query("SELECT * FROM users WHERE email=? COLLATE NOCASE", (email,), one=True)
    if user is None:
        role, pending = _initial_role(email)
        try:
            uid = execute("INSERT INTO users(username, email, full_name, password_hash, role, pending_approval, created_at) "
                          "VALUES(?,?,?,?,?,?,?)",
                          (email, email, "", hash_password(secrets.token_urlsafe(32)), role, pending, now_iso()))
        except sqlite3.IntegrityError:
            user = query("SELECT * FROM users WHERE email=? COLLATE NOCASE", (email,), one=True)
            if user is None:                    # the username is taken by an account that has no email
                raise ApiError(409, "account_conflict",
                               "An account with this name already exists. Ask an administrator to link it to your email.") from None
        else:
            audit.record("user.create", "user", uid, {"email": email, "role": "pending" if pending else role, "via": "email"})
            user = query("SELECT * FROM users WHERE id=?", (uid,), one=True)
    elif user["pending_approval"] and email in set(current_app.config["ADMIN_EMAILS"]):
        execute("UPDATE users SET role='admin', pending_approval=0 WHERE id=?", (user["id"],))
        audit.record("user.update", "user", user["id"], {"role": "admin", "via": "CV_ADMIN_EMAILS"})
        user = query("SELECT * FROM users WHERE id=?", (user["id"],), one=True)
    if not user["is_active"]:
        raise ApiError(403, "account_disabled", "This account is disabled. Contact an administrator.")
    execute("UPDATE users SET last_login_at=? WHERE id=?", (now_iso(), user["id"]))
    return query("SELECT * FROM users WHERE id=?", (user["id"],), one=True)


# ----------------------------------------------------------------------------- start-up self-check
def startup_notes(config) -> list[str]:
    """Warnings worth printing when the server starts (also used by the tests)."""
    notes = []
    mode = str(config.get("EMAIL_DELIVERY") or "").strip().lower()
    if mode == "log":
        notes.append("CV_EMAIL_DELIVERY=log: sign-in codes are written to this log. Anyone who can read it can sign in as any "
                     "institutional user. Use it for local development only.")
    elif mode == "smtp" and not (config.get("SMTP_HOST") and (config.get("SMTP_FROM") or config.get("SMTP_USER"))):
        notes.append("CV_EMAIL_DELIVERY=smtp needs CV_SMTP_HOST and CV_SMTP_FROM (or CV_SMTP_USER): email sign-in is OFF.")
    elif mode == "memory" and not config.get("TESTING"):
        notes.append("CV_EMAIL_DELIVERY=memory is for tests only and is ignored: email sign-in is OFF.")
    elif mode not in ("", "log", "smtp", "memory"):
        notes.append(f"CV_EMAIL_DELIVERY={mode!r} is not recognised (use smtp or log): email sign-in is OFF.")
    default = str(config.get("EMAIL_DEFAULT_ROLE") or "pending")
    if default not in ("pending",) + INITIAL_ROLES:
        notes.append(f"CV_EMAIL_DEFAULT_ROLE={default!r} is not allowed (pending, auditor or operator): using 'pending'.")
    if default == "operator":
        notes.append("CV_EMAIL_DEFAULT_ROLE=operator: everyone with an institutional mailbox can process cheques and see "
                     "signature specimens as soon as they sign in.")
    if not config.get("PASSWORD_LOGIN", True) and not config.get("ADMIN_EMAILS"):
        notes.append("CV_PASSWORD_LOGIN is off and CV_ADMIN_EMAILS is empty: unless an email administrator already "
                     "exists, nobody can administer this server.")
    return notes
