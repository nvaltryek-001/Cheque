"""Delivery of the one-time sign-in code.

CV_EMAIL_DELIVERY selects the transport:
  smtp   real mail through CV_SMTP_HOST / CV_SMTP_PORT (STARTTLS by default; CV_SMTP_SECURITY=ssl for port 465).
         CV_SMTP_USER and the environment variable CV_SMTP_PASSWORD are used when the server asks for a login;
         the password is read here, at send time, and is never stored in the application configuration.
  log    DEVELOPMENT ONLY – the code is written to the server log instead of being mailed. Anyone who can read
         that log can sign in as any institutional user, so never use it on a server other people can reach.
  memory tests only (honoured only when the app runs with TESTING): messages are collected in
         app.extensions["cv_outbox"].
  (empty) email sign-in is switched off.
The code never appears in an API response, the audit log or any log line except in `log` mode.
"""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

from flask import current_app

log = logging.getLogger("chequeverify.mail")

LOOPBACK = {"localhost", "127.0.0.1", "::1"}
SUBJECT = "Your ChequeVerify AI sign-in code"


class DeliveryError(Exception):
    """The code could not be handed to a mail server (the reason is logged, never shown to the person)."""


def mode() -> str:
    """The active transport, or "" when email sign-in is switched off or not usable."""
    m = str(current_app.config.get("EMAIL_DELIVERY") or "").strip().lower()
    if m == "memory":
        return "memory" if current_app.config.get("TESTING") else ""
    if m == "log":
        return "log"
    if m == "smtp":
        return "smtp" if (current_app.config.get("SMTP_HOST") and sender()) else ""
    return ""


def sender() -> str:
    return str(current_app.config.get("SMTP_FROM") or current_app.config.get("SMTP_USER") or "").strip()


def format_code(code: str) -> str:
    return f"{code[:4]} {code[4:]}" if len(code) == 8 else code


def build_message(to: str, code: str, minutes: int) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = SUBJECT
    msg["From"] = sender() or "chequeverify@localhost"
    msg["To"] = to
    msg["Auto-Submitted"] = "auto-generated"
    msg.set_content(
        f"Your ChequeVerify AI sign-in code is {format_code(code)}\n\n"
        f"It works once and expires in {minutes} minutes.\n\n"
        "If you did not ask to sign in, ignore this email. Nobody can sign in with your address without this code.\n")
    return msg


def send_login_code(to: str, code: str) -> None:
    """Hand the code to the configured transport; raise DeliveryError when that fails."""
    m = mode()
    minutes = max(1, int(current_app.config["EMAIL_CODE_TTL_SECONDS"]) // 60)
    if m == "memory":
        current_app.extensions.setdefault("cv_outbox", []).append({"to": to, "code": code, "message": build_message(to, code, minutes)})
    elif m == "log":
        log.warning("DEVELOPMENT MAIL: sign-in code for %s is %s (CV_EMAIL_DELIVERY=log; never use this on a shared server)", to, code)
    elif m == "smtp":
        _smtp(build_message(to, code, minutes))
    else:
        raise DeliveryError("email delivery is not configured")


def _smtp(msg: EmailMessage) -> None:
    cfg = current_app.config
    host, port = str(cfg["SMTP_HOST"]), int(cfg["SMTP_PORT"])
    security = str(cfg.get("SMTP_SECURITY") or "starttls").lower()
    if security not in ("starttls", "ssl", "none"):
        raise DeliveryError(f"CV_SMTP_SECURITY must be starttls, ssl or none, not {security!r}")
    if security == "none" and host.lower() not in LOOPBACK:
        raise DeliveryError("CV_SMTP_SECURITY=none is only allowed for a mail server on this machine")
    password = os.environ.get("CV_SMTP_PASSWORD", "")
    try:
        context = ssl.create_default_context()
        server = smtplib.SMTP_SSL(host, port, timeout=15, context=context) if security == "ssl" else smtplib.SMTP(host, port, timeout=15)
        with server:
            server.ehlo()
            if security == "starttls":
                server.starttls(context=context)
                server.ehlo()
            if cfg.get("SMTP_USER") and password:
                server.login(str(cfg["SMTP_USER"]), password)
            server.send_message(msg)
    except (OSError, smtplib.SMTPException, ssl.SSLError) as exc:
        # only the kind of failure is logged: a server banner or an address could echo private data
        log.error("sign-in code could not be sent: %s", type(exc).__name__)
        raise DeliveryError(type(exc).__name__) from exc
