"""Passwordless sign-in with an institutional email address: validation, one-time codes, roles, limits, delivery."""
import email as emaillib
import logging
import re
import socket
import socketserver
import sqlite3
import threading

import pytest

from app.db import init_db
from app.services import email_login, mailer
from conftest import login

DOMAIN = ["saividya.ac.in"]
ALICE = "student.one@saividya.ac.in"
BOB = "faculty.two@saividya.ac.in"


# ------------------------------------------------------------------ the address rule (pure function)
@pytest.mark.parametrize("raw, expected", [
    ("student.23ds@saividya.ac.in", "student.23ds@saividya.ac.in"),            # name, dot, admission year + branch
    ("  Student.One@SAIVIDYA.AC.IN  ", "student.one@saividya.ac.in"),
    ("a@saividya.ac.in", "a@saividya.ac.in"),
    ("first_last-2@saividya.ac.in", "first_last-2@saividya.ac.in"),
    ("x" * 64 + "@saividya.ac.in", "x" * 64 + "@saividya.ac.in"),
])
def test_valid_institutional_addresses_are_accepted(raw, expected):
    assert email_login.email_problem(raw, DOMAIN) == (expected, None)


@pytest.mark.parametrize("raw", [
    "someone@gmail.com",                         # another domain
    "x@mail.saividya.ac.in",                     # sub-domain
    "x@saividya.ac.in.evil.example",             # domain as a prefix of another
    "x@evilsaividya.ac.in",                      # domain as a suffix of another
    "x@saividya.ac.in@evil.example",             # two @
    "x@saividya.ac.in.",                         # trailing dot
    "saividya.ac.in", "@saividya.ac.in", "x@", "", "   ",
    ".x@saividya.ac.in", "x.@saividya.ac.in", "x..y@saividya.ac.in", "-x@saividya.ac.in",
    "x+tag@saividya.ac.in",                      # plus addressing would create duplicate accounts
    '"x y"@saividya.ac.in', "x y@saividya.ac.in", "x,y@saividya.ac.in", "x;y@saividya.ac.in",
    "x'y@saividya.ac.in", "x<y>@saividya.ac.in",
    "x\n@saividya.ac.in", "x@saividya.ac.in\nBcc: evil@evil.example", "x\r\n@saividya.ac.in", "x\x00@saividya.ac.in",
    "xñ@saividya.ac.in", "x＠saividya.ac.in", "x@saividyа.ac.in",   # non-ASCII, full-width @, Cyrillic 'а'
    "x" * 65 + "@saividya.ac.in",                # local part too long
    "x" * 250 + "@saividya.ac.in",               # whole address too long
])
def test_other_addresses_are_refused(raw):
    email, problem = email_login.email_problem(raw, DOMAIN)
    assert email is None and problem


@pytest.mark.parametrize("raw", [None, 123, ["x@saividya.ac.in"], {"a": 1}, b"x@saividya.ac.in"])
def test_non_text_input_is_refused(raw):
    assert email_login.email_problem(raw, DOMAIN)[0] is None


def test_the_allowed_domains_are_configurable():
    assert email_login.email_problem("a@example.edu", ["example.edu", "other.edu"])[0] == "a@example.edu"
    assert email_login.email_problem("a@saividya.ac.in", ["example.edu"])[0] is None
    assert email_login.email_problem("a@saividya.ac.in", [])[0] is None


# ------------------------------------------------------------------ helpers
@pytest.fixture()
def mail(app):
    """The app with email sign-in on (messages collected in memory) and no resend gap."""
    app.config.update(EMAIL_DELIVERY="memory", EMAIL_RESEND_SECONDS=0, LOGIN_MAX_ATTEMPTS=50, LOGIN_IP_MAX_ATTEMPTS=500)
    app.extensions["cv_outbox"] = []
    return app


def ask(client, address=ALICE):
    return client.post("/api/auth/email/request", json={"email": address})


def last_code(app):
    return app.extensions["cv_outbox"][-1]["code"]


def sign_in(client, app, address=ALICE):
    assert ask(client, address).status_code == 200
    return client.post("/api/auth/email/verify", json={"email": address, "code": last_code(app)})


def bearer(resp):
    return {"Authorization": "Bearer " + resp.get_json()["data"]["token"]}


def may_process(client, headers):
    """True when the account may use an operator endpoint. A POST without a file is a 400 for roles that are allowed
    to upload and a 403 for roles that are not, so no cheque image is needed."""
    status = client.post("/api/cheques", headers=headers).status_code
    assert status in (400, 403), status
    return status == 400


def set_role(client, admin, address, role):
    users = client.get("/api/users", headers=admin).get_json()["data"]
    uid = next(u["id"] for u in users if u["email"] == address)
    return client.patch(f"/api/users/{uid}", headers=admin, json={"role": role})


# ------------------------------------------------------------------ the flow
def test_config_is_public_and_describes_the_methods(client, mail):
    d = client.get("/api/auth/config").get_json()["data"]
    assert d["email_signin"] is True and d["email_domain"] == "saividya.ac.in" and d["password_signin"] is True
    assert d["code_length"] == 8 and d["code_ttl_seconds"] == 600


def test_email_sign_in_is_off_until_a_delivery_method_is_configured(client, app):
    assert client.get("/api/auth/config").get_json()["data"]["email_signin"] is False
    for path, body in (("/api/auth/email/request", {"email": ALICE}), ("/api/auth/email/verify", {"email": ALICE, "code": "12345678"})):
        r = client.post(path, json=body)
        assert r.status_code == 503 and r.get_json()["error"]["code"] == "email_signin_unavailable"


def test_the_memory_transport_is_ignored_outside_tests(mail):
    mail.config["TESTING"] = False
    with mail.app_context():
        assert mailer.mode() == ""


def test_full_flow_creates_a_pending_account_that_sees_no_data(client, mail, admin):
    r = ask(client)
    body = r.get_json()
    assert r.status_code == 200 and body["data"]["sent"] is True and body["data"]["email"] == ALICE
    code = last_code(mail)
    assert re.fullmatch(r"\d{8}", code) and code not in r.get_data(as_text=True)         # never in the response
    msg = mail.extensions["cv_outbox"][0]["message"]
    assert msg["To"] == ALICE and code[:4] + " " + code[4:] in msg.get_content() and "expires in 10 minutes" in msg.get_content()

    r = client.post("/api/auth/email/verify", json={"email": ALICE.upper(), "code": code})
    assert r.status_code == 200, r.get_json()
    user = r.get_json()["data"]["user"]
    assert user["email"] == ALICE and user["role"] == "pending" and user["username"] == ALICE
    h = bearer(r)
    assert client.get("/api/auth/me", headers=h).get_json()["data"]["role"] == "pending"
    for path in ("/api/cheques", "/api/signers", "/api/dashboard", "/api/verifications", "/api/audit-log", "/api/reports/files"):
        g = client.get(path, headers=h)
        assert g.status_code == 403 and g.get_json()["error"]["code"] == "approval_pending", path
    assert client.post("/api/auth/logout", headers=h).status_code == 200
    assert client.get("/api/auth/me", headers=h).status_code == 401                         # logout revoked the token


def test_an_administrator_approves_by_giving_a_role(client, mail, admin):
    h = bearer(sign_in(client, mail))
    assert client.get("/api/cheques", headers=h).status_code == 403
    assert set_role(client, admin, ALICE, "auditor").status_code == 200
    assert client.get("/api/cheques", headers=h).status_code == 200                         # same token, no new sign-in
    assert may_process(client, h) is False                                                  # auditors read, they do not process
    assert client.get("/api/auth/me", headers=h).get_json()["data"]["role"] == "auditor"
    assert set_role(client, admin, ALICE, "pending").status_code == 200                      # access can be withdrawn
    assert client.get("/api/cheques", headers=h).status_code == 403
    assert set_role(client, admin, ALICE, "operator").status_code == 200
    assert may_process(client, h) is True
    assert client.get("/api/users", headers=h).status_code == 403                           # never an admin


def test_the_user_list_shows_email_and_pending_state(client, mail, admin):
    sign_in(client, mail)
    rows = client.get("/api/users", headers=admin).get_json()["data"]
    row = next(u for u in rows if u["email"] == ALICE)
    assert row["role"] == "pending" and row["is_active"] is True
    assert next(u for u in rows if u["username"] == "admin")["email"] is None


def test_admins_cannot_make_themselves_pending_and_invalid_roles_fail(client, mail, admin):
    me = client.get("/api/auth/me", headers=admin).get_json()["data"]["id"]
    assert client.patch(f"/api/users/{me}", headers=admin, json={"role": "pending"}).status_code == 400
    assert client.patch(f"/api/users/{me}", headers=admin, json={"role": "superuser"}).status_code == 400
    assert client.post("/api/users", headers=admin, json={"username": "pend1", "password": "Pending-123", "role": "pending"}).status_code == 400


def test_a_code_works_once(client, mail):
    ask(client)
    code = last_code(mail)
    assert client.post("/api/auth/email/verify", json={"email": ALICE, "code": code}).status_code == 200
    again = client.post("/api/auth/email/verify", json={"email": ALICE, "code": code})
    assert again.status_code == 401 and again.get_json()["error"]["code"] == "invalid_code"


def test_the_code_may_be_typed_with_a_space_or_hyphen(client, mail):
    ask(client)
    code = last_code(mail)
    r = client.post("/api/auth/email/verify", json={"email": ALICE, "code": f" {code[:4]}-{code[4:]} "})
    assert r.status_code == 200


def test_a_new_request_retires_the_previous_code(client, mail):
    ask(client)
    first = last_code(mail)
    ask(client)
    second = last_code(mail)
    assert first != second
    assert client.post("/api/auth/email/verify", json={"email": ALICE, "code": first}).status_code == 401
    assert client.post("/api/auth/email/verify", json={"email": ALICE, "code": second}).status_code == 200


def test_wrong_codes_are_counted_and_the_code_is_burned_after_the_limit(client, mail):
    ask(client)
    right = last_code(mail)
    wrong = "00000000" if right != "00000000" else "11111111"
    left = []
    for _ in range(5):
        r = client.post("/api/auth/email/verify", json={"email": ALICE, "code": wrong})
        assert r.status_code == 401 and r.get_json()["error"]["code"] == "invalid_code"
        left.append((r.get_json()["error"].get("details") or {}).get("attempts_left"))
    assert left == [4, 3, 2, 1, 0]
    burned = client.post("/api/auth/email/verify", json={"email": ALICE, "code": right})
    assert burned.status_code == 401                                                        # the right code is dead too
    ask(client)                                                                              # a fresh request starts clean
    assert client.post("/api/auth/email/verify", json={"email": ALICE, "code": last_code(mail)}).status_code == 200


def test_the_last_allowed_attempt_may_still_succeed(client, mail):
    ask(client)
    right = last_code(mail)
    wrong = "00000000" if right != "00000000" else "11111111"
    for _ in range(4):
        assert client.post("/api/auth/email/verify", json={"email": ALICE, "code": wrong}).status_code == 401
    assert client.post("/api/auth/email/verify", json={"email": ALICE, "code": right}).status_code == 200


@pytest.mark.parametrize("code", ["", "abc", "1234567", "123456789", "１２３４５６７８", "12345 678a", None, 12345678])
def test_malformed_codes_are_refused_without_a_hint(client, mail, code):
    ask(client)
    r = client.post("/api/auth/email/verify", json={"email": ALICE, "code": code})
    assert r.status_code in (400, 401)
    if r.status_code == 401:
        assert r.get_json()["error"]["code"] == "invalid_code"


def test_an_expired_code_is_refused(client, mail):
    mail.config["EMAIL_CODE_TTL_SECONDS"] = -1
    ask(client)
    r = client.post("/api/auth/email/verify", json={"email": ALICE, "code": last_code(mail)})
    assert r.status_code == 401 and r.get_json()["error"]["code"] == "invalid_code"


def test_verifying_without_asking_first_gives_the_same_answer_as_a_wrong_code(client, mail):
    never = client.post("/api/auth/email/verify", json={"email": BOB, "code": "12345678"})
    ask(client)
    wrong = client.post("/api/auth/email/verify", json={"email": ALICE, "code": "00000000" if last_code(mail) != "00000000" else "11111111"})
    assert never.status_code == wrong.status_code == 401
    assert never.get_json()["error"]["code"] == wrong.get_json()["error"]["code"] == "invalid_code"
    assert never.get_json()["error"]["message"] == wrong.get_json()["error"]["message"]


def test_a_code_cannot_be_used_for_another_address(client, mail):
    ask(client, ALICE)
    ask(client, BOB)
    alice_code = mail.extensions["cv_outbox"][0]["code"]
    r = client.post("/api/auth/email/verify", json={"email": BOB, "code": alice_code})
    assert r.status_code == 401


def test_bad_addresses_get_a_clear_message_and_send_nothing(client, mail):
    r = ask(client, "someone@gmail.com")
    assert r.status_code == 400 and r.get_json()["error"]["code"] == "invalid_email"
    assert "saividya.ac.in" in r.get_json()["error"]["message"]
    assert mail.extensions["cv_outbox"] == []
    assert client.post("/api/auth/email/request", json={}).status_code == 400


# ------------------------------------------------------------------ limits
def test_a_second_code_inside_the_resend_gap_is_refused(client, mail):
    mail.config["EMAIL_RESEND_SECONDS"] = 30
    assert ask(client).status_code == 200
    r = ask(client)
    assert r.status_code == 429 and r.get_json()["error"]["code"] == "resend_too_soon"
    assert 1 <= r.get_json()["error"]["details"]["retry_after"] <= 30
    assert len(mail.extensions["cv_outbox"]) == 1


def test_codes_per_address_are_limited(client, mail):
    mail.config["EMAIL_REQUESTS_PER_ADDRESS"] = 3
    assert [ask(client).status_code for _ in range(3)] == [200, 200, 200]
    r = ask(client)
    assert r.status_code == 429 and r.get_json()["error"]["code"] == "too_many_requests"
    assert r.get_json()["error"]["details"]["retry_after"] > 0
    assert ask(client, BOB).status_code == 200                                               # other addresses are unaffected


def test_codes_per_client_are_limited_across_addresses(client, mail):
    mail.config["EMAIL_REQUESTS_PER_IP"] = 3
    assert [ask(client, f"user{i}@saividya.ac.in").status_code for i in range(3)] == [200, 200, 200]
    assert ask(client, "user9@saividya.ac.in").status_code == 429


def test_wrong_codes_feed_the_sign_in_throttle_even_when_the_code_would_have_been_right(client, mail):
    mail.config["LOGIN_MAX_ATTEMPTS"] = 3
    mail.config["EMAIL_CODE_MAX_ATTEMPTS"] = 50
    ask(client)
    right = last_code(mail)
    wrong = "00000000" if right != "00000000" else "11111111"
    assert [client.post("/api/auth/email/verify", json={"email": ALICE, "code": wrong}).status_code for _ in range(3)] == [401] * 3
    blocked = client.post("/api/auth/email/verify", json={"email": ALICE, "code": right})
    assert blocked.status_code == 429 and blocked.get_json()["error"]["code"] == "too_many_attempts"


def test_the_throttle_does_not_leak_between_addresses_or_into_password_sign_in(client, mail, admin):
    mail.config["LOGIN_MAX_ATTEMPTS"] = 2
    ask(client, ALICE)
    for _ in range(2):
        client.post("/api/auth/email/verify", json={"email": ALICE, "code": "00000000"})
    assert client.post("/api/auth/email/verify", json={"email": ALICE, "code": "00000000"}).status_code == 429
    ask(client, BOB)
    assert client.post("/api/auth/email/verify", json={"email": BOB, "code": last_code(mail)}).status_code == 200
    assert client.post("/api/auth/login", json={"username": "admin", "password": "Admin-pass-123"}).status_code == 200


# ------------------------------------------------------------------ secrecy
def test_the_code_is_stored_only_as_a_keyed_hash_and_never_logged_or_audited(client, mail, admin, caplog):
    caplog.set_level(logging.DEBUG)
    ask(client)
    code = last_code(mail)
    client.post("/api/auth/email/verify", json={"email": ALICE, "code": code})
    db = sqlite3.connect(mail.config["DATABASE"])
    try:
        dump = " ".join(str(v) for t in ("login_codes", "audit_log", "users", "login_attempts")
                        for row in db.execute(f"SELECT * FROM {t}") for v in row)
        stored = db.execute("SELECT code_hash FROM login_codes").fetchone()[0]
    finally:
        db.close()
    assert code not in dump and re.fullmatch(r"[0-9a-f]{64}", stored) and stored != code
    assert code not in caplog.text and code[:4] + " " + code[4:] not in caplog.text
    audit = client.get("/api/audit-log?per_page=100", headers=admin).get_data(as_text=True)
    assert code not in audit and ALICE not in audit.split("auth.email_code_sent")[1][:200]   # masked in the sent-code entry


def test_the_audit_log_records_the_sign_in_without_secrets(client, mail, admin):
    sign_in(client, mail)
    rows = client.get("/api/audit-log?per_page=100", headers=admin).get_json()["data"]
    actions = {r["action"] for r in rows}
    assert {"auth.email_code_sent", "auth.login", "user.create"} <= actions
    sent = next(r for r in rows if r["action"] == "auth.email_code_sent")
    assert "st***@saividya.ac.in" in sent["detail"]
    login_row = next(r for r in rows if r["action"] == "auth.login" and r["username"] == ALICE)
    assert '"method": "email"' in login_row["detail"]
    client.post("/api/auth/email/verify", json={"email": ALICE, "code": "00000000"})
    failed = [r for r in client.get("/api/audit-log?per_page=100", headers=admin).get_json()["data"] if r["action"] == "auth.email_code_failed"]
    assert failed and "wrong_code" in failed[0]["detail"] or "no_code" in failed[0]["detail"]


def test_an_email_account_has_no_usable_password(client, mail):
    sign_in(client, mail)
    for password in ("", "x", "Student-pass-123", ALICE):
        r = client.post("/api/auth/login", json={"username": ALICE, "password": password or "-"})
        assert r.status_code == 401


def test_an_administrator_cannot_attach_a_password_to_an_email_account(client, mail, admin):
    h = bearer(sign_in(client, mail))
    uid = client.get("/api/auth/me", headers=h).get_json()["data"]["id"]
    r = client.patch(f"/api/users/{uid}", headers=admin, json={"role": "operator", "password": "Reset-pass-12345"})
    assert r.status_code == 400 and r.get_json()["error"]["code"] == "validation_error"
    assert "institutional email" in r.get_json()["error"]["message"]
    assert client.post("/api/auth/login", json={"username": ALICE, "password": "Reset-pass-12345"}).status_code == 401
    me = client.get("/api/auth/me", headers=h)
    assert me.status_code == 200 and me.get_json()["data"]["role"] == "pending"        # nothing in the refused request was applied


# ------------------------------------------------------------------ roles on first sign-in
def test_default_role_can_be_auditor_or_operator_but_never_admin(client, mail, admin):
    mail.config["EMAIL_DEFAULT_ROLE"] = "auditor"
    h = bearer(sign_in(client, mail, ALICE))
    assert client.get("/api/cheques", headers=h).status_code == 200 and client.get("/api/users", headers=h).status_code == 403
    assert may_process(client, h) is False
    mail.config["EMAIL_DEFAULT_ROLE"] = "operator"
    h2 = bearer(sign_in(client, mail, BOB))
    assert may_process(client, h2) is True and client.get("/api/users", headers=h2).status_code == 403
    mail.config["EMAIL_DEFAULT_ROLE"] = "admin"
    h3 = bearer(sign_in(client, mail, "third.person@saividya.ac.in"))
    assert client.get("/api/cheques", headers=h3).status_code == 403                         # treated as pending
    assert client.get("/api/auth/me", headers=h3).get_json()["data"]["role"] == "pending"


def test_listed_administrator_addresses_become_admins_after_proving_the_mailbox(client, mail):
    mail.config["ADMIN_EMAILS"] = [ALICE]
    h = bearer(sign_in(client, mail, ALICE))
    assert client.get("/api/users", headers=h).status_code == 200
    assert client.get("/api/auth/me", headers=h).get_json()["data"]["role"] == "admin"
    other = bearer(sign_in(client, mail, BOB))
    assert client.get("/api/users", headers=other).status_code == 403                        # not listed
    assert ask(client, ALICE).status_code == 200                                              # typing the address alone gives nothing
    assert client.post("/api/auth/email/verify", json={"email": ALICE, "code": "00000000"}).status_code == 401


def test_adding_an_address_to_the_admin_list_promotes_an_account_that_is_still_pending(client, mail):
    sign_in(client, mail, ALICE)
    mail.config["ADMIN_EMAILS"] = [ALICE]
    h = bearer(sign_in(client, mail, ALICE))
    assert client.get("/api/users", headers=h).status_code == 200


def test_the_admin_list_does_not_override_a_deliberate_demotion(client, mail, admin):
    mail.config["ADMIN_EMAILS"] = [ALICE]
    sign_in(client, mail, ALICE)
    assert set_role(client, admin, ALICE, "auditor").status_code == 200
    h = bearer(sign_in(client, mail, ALICE))
    assert client.get("/api/auth/me", headers=h).get_json()["data"]["role"] == "auditor"


def test_a_disabled_account_cannot_sign_in_by_email_and_loses_its_token(client, mail, admin):
    h = bearer(sign_in(client, mail))
    uid = client.get("/api/auth/me", headers=h).get_json()["data"]["id"]
    assert client.patch(f"/api/users/{uid}", headers=admin, json={"is_active": False}).status_code == 200
    assert client.get("/api/auth/me", headers=h).status_code == 401
    ask(client)
    r = client.post("/api/auth/email/verify", json={"email": ALICE, "code": last_code(mail)})
    assert r.status_code == 403 and r.get_json()["error"]["code"] == "account_disabled"


def test_a_deleted_account_starts_again_as_pending(client, mail, admin):
    h = bearer(sign_in(client, mail))
    uid = client.get("/api/auth/me", headers=h).get_json()["data"]["id"]
    assert client.delete(f"/api/users/{uid}", headers=admin).status_code == 200
    h2 = bearer(sign_in(client, mail))
    assert client.get("/api/auth/me", headers=h2).get_json()["data"]["role"] == "pending"


def test_one_account_per_address_whatever_the_letter_case(client, mail, admin):
    sign_in(client, mail, ALICE)
    sign_in(client, mail, ALICE.upper())
    assert [u["email"] for u in client.get("/api/users", headers=admin).get_json()["data"]].count(ALICE) == 1


def test_an_unapproved_account_is_blocked_on_every_protected_endpoint(app, client, mail):
    """Walks the whole route table: whatever answers 401 to a stranger must answer 403 approval_pending to a
    signed-in but unapproved account, except who-am-I and sign-out. Catches an endpoint added without the gate."""
    pending = bearer(sign_in(client, mail))
    allowed = {"/api/auth/me", "/api/auth/logout"}
    protected = 0
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static" or not rule.rule.startswith("/api/"):
            continue
        if rule.rule == "/api/auth/logout":
            continue                  # allowed for pending accounts (see the flow test); calling it here would revoke the token
        url = rule.build({a: 1 for a in rule.arguments})[1]
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            anonymous = client.open(url, method=method)
            if anonymous.status_code != 401:
                continue
            protected += 1
            r = client.open(url, method=method, headers=pending)
            if rule.rule in allowed:
                assert r.status_code != 403, (method, rule.rule)
            else:
                assert r.status_code == 403 and r.get_json()["error"]["code"] == "approval_pending", (method, rule.rule, r.status_code)
    assert protected > 40


# ------------------------------------------------------------------ password sign-in stays, can be switched off
def test_password_sign_in_still_works_and_reports_its_method(client, mail, admin):
    assert login(client)["Authorization"].startswith("Bearer ")
    rows = client.get("/api/audit-log?per_page=50", headers=admin).get_json()["data"]
    assert any(r["action"] == "auth.login" and '"method": "password"' in r["detail"] for r in rows)


def test_password_sign_in_can_be_switched_off(client, mail):
    mail.config["PASSWORD_LOGIN"] = False
    assert client.get("/api/auth/config").get_json()["data"]["password_signin"] is False
    r = client.post("/api/auth/login", json={"username": "admin", "password": "Admin-pass-123"})
    assert r.status_code == 403 and r.get_json()["error"]["code"] == "password_signin_disabled"
    assert sign_in(client, mail).status_code == 200                                           # email sign-in is unaffected


# ------------------------------------------------------------------ delivery
def test_a_failed_delivery_is_reported_without_details_and_leaves_no_live_code(client, mail, admin, monkeypatch):
    def boom(to, code):
        raise mailer.DeliveryError("SMTPAuthenticationError")
    monkeypatch.setattr(mailer, "send_login_code", boom)
    r = ask(client)
    assert r.status_code == 503 and r.get_json()["error"]["code"] == "email_delivery_failed"
    assert "SMTP" not in r.get_data(as_text=True)
    db = sqlite3.connect(mail.config["DATABASE"])
    try:
        assert db.execute("SELECT COUNT(*) FROM login_codes WHERE consumed_at IS NULL").fetchone()[0] == 0
    finally:
        db.close()
    assert "delivery" in client.get("/api/audit-log?per_page=20", headers=admin).get_data(as_text=True)


def test_log_delivery_writes_the_code_to_the_log_and_warns_at_start_up(client, mail, caplog):
    mail.config["EMAIL_DELIVERY"] = "log"
    caplog.set_level(logging.WARNING)
    assert ask(client).status_code == 200
    assert "DEVELOPMENT MAIL" in caplog.text and re.search(r"is \d{8}", caplog.text)
    assert any("CV_EMAIL_DELIVERY=log" in n for n in email_login.startup_notes(mail.config))


class FakeSmtp(socketserver.ThreadingTCPServer):
    """A mail server that accepts anything: no STARTTLS, no AUTH, keeps every message it receives."""
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _SmtpHandler)
        self.messages: list[str] = []
        threading.Thread(target=self.serve_forever, daemon=True).start()

    @property
    def port(self) -> int:
        return self.server_address[1]

    def stop(self):
        self.shutdown()
        self.server_close()


class _SmtpHandler(socketserver.StreamRequestHandler):
    def handle(self):
        def say(text):
            self.wfile.write(text.encode() + b"\r\n")
            self.wfile.flush()
        say("220 fake ESMTP")
        body, in_data = [], False
        for raw in self.rfile:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if in_data:
                if line == ".":
                    in_data = False
                    self.server.messages.append("\n".join(body))
                    body = []
                    say("250 queued")
                else:
                    body.append(line[1:] if line.startswith("..") else line)
                continue
            word = line.upper()
            if word.startswith(("EHLO", "HELO")):
                say("250 fake")
            elif word.startswith(("MAIL FROM", "RCPT TO")):
                say("250 ok")
            elif word == "DATA":
                say("354 go ahead")
                in_data = True
            elif word == "QUIT":
                say("221 bye")
                return
            else:
                say("500 unknown")


@pytest.fixture()
def smtp(mail):
    server = FakeSmtp()
    mail.config.update(EMAIL_DELIVERY="smtp", SMTP_HOST="127.0.0.1", SMTP_PORT=server.port, SMTP_SECURITY="none",
                       SMTP_FROM="no-reply@saividya.ac.in", SMTP_USER="")
    yield server
    server.stop()


def test_a_real_smtp_server_receives_the_code_and_it_signs_the_person_in(client, mail, smtp):
    assert ask(client).status_code == 200
    assert len(smtp.messages) == 1
    msg = emaillib.message_from_string(smtp.messages[0])
    assert msg["To"] == ALICE and msg["From"] == "no-reply@saividya.ac.in" and msg["Subject"] == "Your ChequeVerify AI sign-in code"
    assert msg["Auto-Submitted"] == "auto-generated" and "Bcc" not in msg
    code = re.search(r"code is (\d{4}) (\d{4})", msg.get_payload(decode=True).decode()).groups()
    assert client.post("/api/auth/email/verify", json={"email": ALICE, "code": "".join(code)}).status_code == 200


def test_starttls_is_required_and_never_silently_dropped(client, mail, smtp):
    mail.config["SMTP_SECURITY"] = "starttls"                      # the fake server does not offer it
    r = ask(client)
    assert r.status_code == 503 and r.get_json()["error"]["code"] == "email_delivery_failed"
    assert smtp.messages == []                                     # nothing was sent in clear text


def test_unencrypted_smtp_is_refused_for_servers_on_other_machines(client, mail):
    mail.config.update(EMAIL_DELIVERY="smtp", SMTP_HOST="mail.example.invalid", SMTP_SECURITY="none", SMTP_FROM="x@saividya.ac.in")
    r = ask(client)
    assert r.status_code == 503 and r.get_json()["error"]["code"] == "email_delivery_failed"


def test_an_unreachable_mail_server_is_a_clean_503(client, mail):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    mail.config.update(EMAIL_DELIVERY="smtp", SMTP_HOST="127.0.0.1", SMTP_PORT=port, SMTP_SECURITY="none", SMTP_FROM="x@saividya.ac.in")
    r = ask(client)
    assert r.status_code == 503 and r.get_json()["error"]["code"] == "email_delivery_failed"


def test_smtp_needs_a_host_and_a_sender_before_email_sign_in_is_offered(client, mail):
    mail.config.update(EMAIL_DELIVERY="smtp", SMTP_HOST="", SMTP_FROM="", SMTP_USER="")
    assert client.get("/api/auth/config").get_json()["data"]["email_signin"] is False
    mail.config.update(SMTP_HOST="mail.example.invalid", SMTP_FROM="x@saividya.ac.in")
    assert client.get("/api/auth/config").get_json()["data"]["email_signin"] is True


def test_start_up_notes_point_out_unsafe_or_incomplete_settings():
    ok = {"EMAIL_DELIVERY": "smtp", "SMTP_HOST": "h", "SMTP_FROM": "f", "EMAIL_DEFAULT_ROLE": "pending", "PASSWORD_LOGIN": True}
    assert email_login.startup_notes(ok) == []
    assert email_login.startup_notes({"EMAIL_DELIVERY": ""}) == []
    assert any("OFF" in n for n in email_login.startup_notes({"EMAIL_DELIVERY": "smtp"}))
    assert any("not recognised" in n for n in email_login.startup_notes({"EMAIL_DELIVERY": "carrier-pigeon"}))
    assert any("tests only" in n for n in email_login.startup_notes({"EMAIL_DELIVERY": "memory"}))
    assert any("not allowed" in n for n in email_login.startup_notes({**ok, "EMAIL_DEFAULT_ROLE": "admin"}))
    assert any("process cheques" in n for n in email_login.startup_notes({**ok, "EMAIL_DEFAULT_ROLE": "operator"}))
    assert any("nobody can administer" in n for n in email_login.startup_notes({**ok, "PASSWORD_LOGIN": False, "ADMIN_EMAILS": []}))
    assert not any("administer" in n for n in email_login.startup_notes({**ok, "PASSWORD_LOGIN": False, "ADMIN_EMAILS": [ALICE]}))


# ------------------------------------------------------------------ upgrade of an existing database
def test_an_older_database_is_upgraded_in_place(tmp_path):
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            full_name TEXT NOT NULL DEFAULT '', password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('admin','operator','auditor')), is_active INTEGER NOT NULL DEFAULT 1,
            token_version INTEGER NOT NULL DEFAULT 0, failed_logins INTEGER NOT NULL DEFAULT 0, locked_until TEXT,
            created_at TEXT NOT NULL, last_login_at TEXT);
        INSERT INTO users(username, full_name, password_hash, role, created_at) VALUES ('legacy', 'Old User', 'x', 'operator', '2026-01-01');
    """)
    db.commit()
    db.close()
    init_db(str(path))
    init_db(str(path))                                               # idempotent
    db = sqlite3.connect(path)
    try:
        cols = {r[1] for r in db.execute("PRAGMA table_info(users)")}
        assert {"email", "pending_approval"} <= cols
        assert db.execute("SELECT username, role, email, pending_approval FROM users").fetchall() == [("legacy", "operator", None, 0)]
        assert db.execute("SELECT name FROM sqlite_master WHERE name='login_codes'").fetchone()
        db.execute("INSERT INTO users(username, email, password_hash, role, created_at) VALUES ('a@saividya.ac.in','a@saividya.ac.in','x','auditor','t')")
        with pytest.raises(sqlite3.IntegrityError):                  # one account per address, whatever the case
            db.execute("INSERT INTO users(username, email, password_hash, role, created_at) VALUES ('b','A@SAIVIDYA.AC.IN','x','auditor','t')")
    finally:
        db.close()
