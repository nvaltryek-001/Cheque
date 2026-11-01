"""Brute-force and abuse throttling.

Sign-in throttle (persisted in SQLite, so it survives restarts and is shared by all worker threads)
  Failed sign-ins are remembered for LOGIN_LOCK_SECONDS. A (client IP, username) pair is blocked after
  LOGIN_MAX_ATTEMPTS failures and a client IP is blocked after LOGIN_IP_MAX_ATTEMPTS failures against any
  usernames (password spraying). While blocked the password is not even checked, so a block cannot be used
  to test guesses. The block is per client: someone typing wrong passwords for "admin" from another network
  can no longer lock the real administrator out (the earlier account-wide lockout could be triggered by
  anyone who knew the username). Unknown usernames are throttled exactly like real ones, so the response
  does not reveal which accounts exist.

  Limits of this design: it cannot stop a distributed guessing attack that uses many IPs – that needs strong
  passwords (CV_MIN_PASSWORD_LENGTH), MFA/SSO and a WAF. Behind a reverse proxy, CV_PROXY_HOPS must be set
  correctly, otherwise every user shares the proxy's IP and one attacker blocks everyone.

Rate limiter for expensive endpoints (in memory, per user)
  A sliding window of CV_RATE_LIMIT_PER_MIN cost units per user per bucket. It is per process: with several
  server instances each keeps its own counts.
"""
from __future__ import annotations

import math
import threading
import time
from collections import defaultdict, deque

from flask import current_app, request

from ..api.common import ApiError
from ..db import execute, query


def _now() -> float:
    return time.time()


def client_ip() -> str:
    return (request.remote_addr or "unknown")[:64]


def _key(username: str) -> str:
    return (username or "").strip().lower()[:64]


def _window() -> int:
    return max(1, int(current_app.config["LOGIN_LOCK_SECONDS"]))


def _blocked_for(rows: list, limit: int, window: int, now: float) -> int:
    """Seconds until the count of failures in the window drops below `limit` (0 = not blocked).
    `rows` are failure timestamps, newest first, all inside the window."""
    if limit <= 0 or len(rows) < limit:
        return 0
    return max(1, math.ceil(rows[limit - 1] + window - now))


def retry_after(ip: str, key: str) -> int:
    now, window = _now(), _window()
    since = now - window
    pair = [r["at"] for r in query("SELECT at FROM login_attempts WHERE ip=? AND username=? AND at>? ORDER BY at DESC",
                                   (ip, key, since))]
    by_ip = [r["at"] for r in query("SELECT at FROM login_attempts WHERE ip=? AND at>? ORDER BY at DESC", (ip, since))]
    return max(_blocked_for(pair, int(current_app.config["LOGIN_MAX_ATTEMPTS"]), window, now),
               _blocked_for(by_ip, int(current_app.config["LOGIN_IP_MAX_ATTEMPTS"]), window, now))


def check_login(username: str) -> None:
    """Raise 429 when this client is blocked. Call before looking at the password."""
    wait = retry_after(client_ip(), _key(username))
    if wait:
        raise ApiError(429, "too_many_attempts",
                       f"Too many failed attempts from this network. Try again in {wait} seconds.",
                       {"retry_after": wait})


def record_failure(username: str) -> bool:
    """Remember a failed attempt. Returns True when this failure starts a block."""
    ip, key, now = client_ip(), _key(username), _now()
    execute("DELETE FROM login_attempts WHERE at<?", (now - _window(),))
    execute("INSERT INTO login_attempts(ip, username, at) VALUES(?,?,?)", (ip, key, now))
    return retry_after(ip, key) > 0


def record_success(username: str) -> None:
    """A successful sign-in clears this client's failures for that username (other clients' are kept)."""
    execute("DELETE FROM login_attempts WHERE ip=? AND username=?", (client_ip(), _key(username)))


def clear_all() -> int:
    """Emergency unlock (manage.py unlock-logins)."""
    n = query("SELECT COUNT(*) c FROM login_attempts", one=True)["c"]
    execute("DELETE FROM login_attempts")
    return n


# ------------------------------------------------------------------ rate limit for heavy endpoints
class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[tuple, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: tuple, cost: int, limit: int, window: float = 60.0) -> int:
        """Register `cost` units; returns 0 when allowed or the seconds to wait when over the limit."""
        if limit <= 0:
            return 0
        now = _now()
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= now - window:
                q.popleft()
            if len(q) + cost > limit and q:
                return max(1, math.ceil(q[0] + window - now))
            if cost > limit:                       # a single request larger than the whole budget
                return int(window)
            q.extend([now] * cost)
            return 0

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


def limiter() -> RateLimiter:
    """The current app's limiter (one per application object, so tests and multiple apps never share counts)."""
    ext = current_app.extensions
    if "cv_limiter" not in ext:
        ext["cv_limiter"] = RateLimiter()
    return ext["cv_limiter"]


def rate_limited(bucket: str = "heavy", cost=lambda: 1):
    """Decorator for routes behind login_required: limits expensive calls per signed-in user."""
    import functools

    from flask import g

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            limit = int(current_app.config.get("RATE_LIMIT_PER_MIN", 0))
            user = g.get("user")
            wait = limiter().hit((bucket, user["id"] if user else client_ip()), int(cost()), limit)
            if wait:
                raise ApiError(429, "rate_limited", f"Too many requests. Try again in {wait} seconds.",
                               {"retry_after": wait})
            return fn(*args, **kwargs)
        return wrapper
    return decorator
