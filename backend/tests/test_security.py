"""Security regression tests (phase 2): upload bombs, sign-in throttling, headers, rate limits."""
import io
import struct
import zlib
from pathlib import Path

import cv2
import numpy as np
import pytest

from conftest import ADMIN_PW, login, png_bytes, synthetic_signature, upload


def _png_declaring(w: int, h: int) -> bytes:
    """A tiny, structurally valid PNG whose header declares w x h pixels (one scanline of data)."""
    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    body = zlib.compress(b"\x00" + b"\xff" * w, 9)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", body) + chunk(b"IEND", b""))


# ------------------------------------------------------------------ decompression bombs
@pytest.fixture()
def no_decode(monkeypatch):
    """Any attempt to decode pixels fails the test: oversized images must be refused from the header."""
    def boom(*a, **k):
        raise AssertionError("pixels were decoded before the size check")
    monkeypatch.setattr(cv2, "imdecode", boom)


MP = "multipart/form-data"


@pytest.fixture()
def signer_id(client, admin):
    r = client.post("/api/signers", headers=admin, json={"signer_code": "SEC-001", "bank": "axis", "full_name": "T"})
    assert r.status_code == 201
    return r.get_json()["data"]["id"]


def test_declared_huge_png_is_refused_before_decode_on_every_upload_route(client, admin, no_decode, signer_id):
    bomb = _png_declaring(20000, 20000)
    for path, field in (("/api/cheques", "file"), ("/api/signature/preprocess", "file"),
                        (f"/api/signers/{signer_id}/specimens", "file"), ("/api/cheques/batch", "files")):
        r = client.post(path, headers=admin, data=upload(bomb, "bomb.png", field), content_type=MP)
        body = r.get_json()
        if path == "/api/cheques/batch":      # batch answers 201 and reports the failure per file
            assert r.status_code == 201 and body["data"]["failed"] == 1 and "too large" in str(body["data"]).lower()
        else:
            assert r.status_code == 413 and body["error"]["code"] == "image_too_large", (path, r.status_code, body)


def test_pixel_limit_is_configurable_and_applies_to_normal_images(app, client, admin, no_decode):
    app.config["MAX_IMAGE_PIXELS"] = 10_000
    r = client.post("/api/cheques", headers=admin, data=upload(_png_declaring(200, 100), "big.png"), content_type=MP)
    assert r.status_code == 413


def test_images_within_limit_still_work(client, admin):
    img = np.full((120, 400, 3), 255, np.uint8)
    cv2.putText(img, "Test", (20, 80), cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 0, 0), 3)
    r = client.post("/api/cheques", headers=admin, data=upload(png_bytes(img), "ok.png"), content_type=MP)
    assert r.status_code == 201


def test_unreadable_header_is_rejected_as_corrupt(client, admin):
    r = client.post("/api/cheques", headers=admin,
                    data=upload(b"\x89PNG\r\n\x1a\n" + b"not a real png" * 20, "x.png"), content_type=MP)
    assert r.status_code == 422 and r.get_json()["error"]["code"] == "corrupted_image"


def test_rgba_compositing_matches_reference_in_strips():
    from app.services.storage import decode_image
    rng = np.random.default_rng(1)
    rgba = rng.integers(0, 256, (1300, 37, 4), dtype=np.uint8)       # taller than one strip
    ok, buf = cv2.imencode(".png", rgba)
    out = decode_image(buf.tobytes())
    alpha = rgba[:, :, 3:4].astype(np.float32) / 255.0
    ref = (rgba[:, :, :3].astype(np.float32) * alpha + 255 * (1 - alpha)).astype(np.uint8)
    assert out.shape == ref.shape and np.abs(out.astype(int) - ref.astype(int)).max() <= 1


# ------------------------------------------------------------------ sign-in throttle
ATTACKER = {"REMOTE_ADDR": "203.0.113.9"}
OTHER = {"REMOTE_ADDR": "198.51.100.7"}


def _login(client, user="admin", pw="wrong", env=None):
    return client.post("/api/auth/login", json={"username": user, "password": pw}, environ_base=env or {})


def test_outsider_cannot_lock_the_admin_out(client):
    for _ in range(10):
        assert _login(client, env=ATTACKER).status_code in (401, 429)
    assert _login(client, env=ATTACKER).status_code == 429                   # the attacker's network is blocked …
    assert _login(client, pw=ADMIN_PW, env=ATTACKER).status_code == 429      # … even with the right password
    r = _login(client, pw=ADMIN_PW)                                          # … but the administrator is not
    assert r.status_code == 200 and r.get_json()["data"]["token"]
    assert _login(client, pw=ADMIN_PW, env=OTHER).status_code == 200


def test_block_reports_retry_after_and_expires(client, monkeypatch):
    from app.services import throttle
    t = [1_000_000.0]
    monkeypatch.setattr(throttle, "_now", lambda: t[0])
    for _ in range(3):
        _login(client, env=ATTACKER)
    r = _login(client, pw=ADMIN_PW, env=ATTACKER)
    assert r.status_code == 429 and r.get_json()["error"]["code"] == "too_many_attempts"
    assert 0 < int(r.headers["Retry-After"]) <= 300 and r.get_json()["error"]["details"]["retry_after"] > 0
    t[0] += 301                                                              # window passes
    assert _login(client, pw=ADMIN_PW, env=ATTACKER).status_code == 200


def test_unknown_usernames_are_throttled_like_real_ones(client):
    codes = [_login(client, "nobody", env=ATTACKER).status_code for _ in range(4)]
    assert codes == [401, 401, 401, 429]       # same behaviour as an existing account: no user enumeration


def test_password_spraying_from_one_ip_is_blocked(app, client):
    app.config["LOGIN_IP_MAX_ATTEMPTS"] = 5
    for i in range(5):
        assert _login(client, f"user{i}", env=ATTACKER).status_code == 401
    assert _login(client, "brand_new_name", env=ATTACKER).status_code == 429
    assert _login(client, "brand_new_name", env=OTHER).status_code == 401


def test_success_clears_that_clients_failures(client):
    _login(client); _login(client)
    assert _login(client, pw=ADMIN_PW).status_code == 200
    _login(client); _login(client)
    assert _login(client, pw=ADMIN_PW).status_code == 200                    # would be blocked without the reset


def test_throttle_events_are_audited_without_leaking_typed_text(client, admin):
    _login(client, "Secret-Pass-123", env=ATTACKER)                          # a password typed into the username box
    for _ in range(3):                                                       # third failure for "admin" starts a block
        _login(client, env=ATTACKER)
    r = client.get("/api/audit-log?per_page=100", headers=admin)
    text = r.get_data(as_text=True)
    assert "auth.login_failed" in text and "auth.login_throttled" in text
    assert "Secret-Pass-123" not in text


def test_change_password_current_password_check_is_throttled(client, admin):
    for _ in range(3):
        r = client.post("/api/auth/change-password", headers=admin,
                        json={"current_password": "nope", "new_password": "New-pass-12345"})
        assert r.status_code == 400
    r = client.post("/api/auth/change-password", headers=admin,
                    json={"current_password": ADMIN_PW, "new_password": "New-pass-12345"})
    assert r.status_code == 429


def test_minimum_password_length_is_configurable(app, client, admin):
    app.config["MIN_PASSWORD_LENGTH"] = 14
    r = client.post("/api/users", headers=admin, json={"username": "carol", "password": "Short-pass-1", "role": "operator"})
    assert r.status_code == 400 and "14" in r.get_json()["error"]["message"]
    r = client.post("/api/users", headers=admin, json={"username": "carol", "password": "Long-enough-pass-1", "role": "operator"})
    assert r.status_code == 201


def test_unlock_logins_clears_the_throttle(app, client):
    for _ in range(3):
        _login(client, env=ATTACKER)
    assert _login(client, pw=ADMIN_PW, env=ATTACKER).status_code == 429
    with app.app_context():
        from app.services import throttle
        assert throttle.clear_all() == 3
    assert _login(client, pw=ADMIN_PW, env=ATTACKER).status_code == 200


# ------------------------------------------------------------------ rate limit on expensive calls
def _sig_upload():
    return upload(png_bytes(synthetic_signature(3)), "s.png")


def test_expensive_calls_are_limited_per_user(app, client, admin, operator):
    app.config["RATE_LIMIT_PER_MIN"] = 3
    ok_codes = [client.post("/api/signature/preprocess", headers=operator, data=_sig_upload(), content_type=MP).status_code
                for _ in range(3)]
    assert ok_codes == [200, 200, 200]
    r = client.post("/api/signature/preprocess", headers=operator, data=_sig_upload(), content_type=MP)
    assert r.status_code == 429 and r.get_json()["error"]["code"] == "rate_limited" and int(r.headers["Retry-After"]) >= 1
    # another user has their own budget
    assert client.post("/api/signature/preprocess", headers=admin, data=_sig_upload(), content_type=MP).status_code == 200
    # cheap reads are not limited
    assert client.get("/api/cheques", headers=operator).status_code == 200


def test_batch_costs_one_unit_per_file(app, client, operator):
    app.config["RATE_LIMIT_PER_MIN"] = 3
    files = {"files": [(io.BytesIO(png_bytes(synthetic_signature(i))), f"f{i}.png") for i in range(4)]}
    r = client.post("/api/cheques/batch", headers=operator, data=files, content_type=MP)
    assert r.status_code == 429                        # 4 files exceed a budget of 3 per minute


def test_rate_limit_can_be_switched_off(app, client, operator):
    app.config["RATE_LIMIT_PER_MIN"] = 0
    for _ in range(5):
        assert client.post("/api/signature/preprocess", headers=operator, data=_sig_upload(), content_type=MP).status_code == 200


def test_limiter_window_slides(monkeypatch):
    from app.services import throttle
    t = [100.0]
    monkeypatch.setattr(throttle, "_now", lambda: t[0])
    lim = throttle.RateLimiter()
    assert [lim.hit(("b", 1), 1, 2) for _ in range(3)] == [0, 0, 60]
    t[0] += 61
    assert lim.hit(("b", 1), 1, 2) == 0


# ---------------------------------------------------------------- response headers
def _spa_dist(app, tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>x</title><div id=root></div>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    app.config["FRONTEND_DIST"] = dist


def test_api_responses_carry_the_hardening_headers(client):
    h = client.get("/api/health").headers
    assert h["Content-Security-Policy"].startswith("default-src 'none'")
    assert "frame-ancestors 'none'" in h["Content-Security-Policy"]
    assert h["X-Content-Type-Options"] == "nosniff" and h["X-Frame-Options"] == "DENY"
    assert h["Referrer-Policy"] == "no-referrer" and h["Cache-Control"] == "no-store"
    assert h["Cross-Origin-Opener-Policy"] == "same-origin" and h["Cross-Origin-Resource-Policy"] == "same-origin"
    assert "camera=()" in h["Permissions-Policy"] and "microphone=()" in h["Permissions-Policy"]


def test_error_responses_carry_the_headers_too(client):
    for path in ("/api/nope", "/api/cheques"):          # 404 and 401
        h = client.get(path).headers
        assert "Content-Security-Policy" in h and h["X-Content-Type-Options"] == "nosniff"


def test_spa_policy_blocks_inline_script_and_foreign_origins(app, client, tmp_path):
    _spa_dist(app, tmp_path)
    for path in ("/", "/#/cheques", "/assets/app.js", "/some/client/route"):
        csp = client.get(path.split("#")[0]).headers["Content-Security-Policy"]
        directives = {d.split()[0]: d.split()[1:] for d in csp.split(";") if d.strip()}
        assert directives["script-src"] == ["'self'"]
        assert directives["default-src"] == ["'self'"]
        assert directives["object-src"] == ["'none'"] and directives["frame-ancestors"] == ["'none'"]
        assert directives["base-uri"] == ["'none'"] and directives["connect-src"] == ["'self'"]
        assert "'unsafe-inline'" not in csp and "'unsafe-eval'" not in csp
        assert "http:" not in csp and "https:" not in csp and "*" not in csp
        assert set(directives["img-src"]) == {"'self'", "blob:"}


def test_hsts_only_over_https(app, client):
    assert "Strict-Transport-Security" not in client.get("/api/health").headers
    h = client.get("/api/health", base_url="https://localhost").headers
    assert h["Strict-Transport-Security"] == "max-age=31536000"
    app.config["HSTS_MAX_AGE"] = 0
    assert "Strict-Transport-Security" not in client.get("/api/health", base_url="https://localhost").headers


def test_hsts_follows_the_proxy_scheme_only_when_a_proxy_is_trusted(tmp_path, monkeypatch):
    from app import create_app
    from conftest import ADMIN_PW
    monkeypatch.setenv("CV_ADMIN_PASSWORD", ADMIN_PW)
    base = {"TESTING": True, "INSTANCE_DIR": tmp_path, "DATABASE": tmp_path / "t.db", "STORAGE_DIR": tmp_path / "s",
            "MODELS_DIR": tmp_path / "m", "SECRET_KEY": "k", "REPORTS_DIR": tmp_path / "r"}
    trusted = create_app({**base, "PROXY_HOPS": 1}).test_client()
    assert "Strict-Transport-Security" in trusted.get("/api/health", headers={"X-Forwarded-Proto": "https"}).headers
    untrusted = create_app({**base, "DATABASE": tmp_path / "u.db", "PROXY_HOPS": 0}).test_client()
    assert "Strict-Transport-Security" not in untrusted.get("/api/health", headers={"X-Forwarded-Proto": "https"}).headers


def test_cors_allows_only_listed_origins(client):
    ok = client.get("/api/health", headers={"Origin": "http://localhost:5173"}).headers
    assert ok["Access-Control-Allow-Origin"] == "http://localhost:5173"
    bad = client.get("/api/health", headers={"Origin": "https://evil.example"}).headers
    assert "Access-Control-Allow-Origin" not in bad
    pre = client.open("/api/auth/login", method="OPTIONS", headers={"Origin": "https://evil.example"})
    assert "Access-Control-Allow-Origin" not in pre.headers


# ---------------------------------------------------------------- password policy
def test_password_policy_rules():
    from app.services.auth import password_problem
    assert password_problem("Admin-pass-123", 8) is None
    assert "at least 12" in password_problem("Short-1", 12)
    assert "mix" in password_problem("onlyletterslong", 8) and "mix" in password_problem("1234567890", 8)
    assert "common" in password_problem("Password123", 8, "someone")
    assert "common" in password_problem("Alice.Smith1", 8, "alice.smith1")      # equals the username


def test_users_cannot_be_created_with_common_or_username_passwords(client, admin):
    r = client.post("/api/users", headers=admin, json={"username": "bob_ops", "password": "Bob_ops", "role": "operator"})
    assert r.status_code == 400
    r = client.post("/api/users", headers=admin, json={"username": "carol1", "password": "Password123", "role": "operator"})
    assert r.status_code == 400 and r.json["error"]["code"] == "weak_password"
    r = client.post("/api/users", headers=admin, json={"username": "carol1", "password": "Carol-ops-2026", "role": "operator"})
    assert r.status_code == 201


def test_new_password_must_differ_from_the_current_one(client, admin):
    from conftest import ADMIN_PW
    r = client.post("/api/auth/change-password", headers=admin,
                    json={"current_password": ADMIN_PW, "new_password": ADMIN_PW})
    assert r.status_code == 400 and "differ" in r.json["error"]["message"]


def test_first_admin_password_from_the_environment_must_satisfy_the_policy(tmp_path, monkeypatch):
    from app import create_app
    base = {"TESTING": True, "INSTANCE_DIR": tmp_path, "DATABASE": tmp_path / "x.db", "STORAGE_DIR": tmp_path / "s",
            "MODELS_DIR": tmp_path / "m", "SECRET_KEY": "k", "REPORTS_DIR": tmp_path / "r", "MIN_PASSWORD_LENGTH": 12}
    monkeypatch.setenv("CV_ADMIN_PASSWORD", "admin123")
    with pytest.raises(RuntimeError, match="CV_ADMIN_PASSWORD is not acceptable"):
        create_app(base)
    monkeypatch.setenv("CV_ADMIN_PASSWORD", "Str0ng-first-admin")
    assert create_app({**base, "DATABASE": tmp_path / "y.db"}) is not None


# ---------------------------------------------------------------- .env loader (run.py)
def test_dotenv_loader_handles_quotes_and_inline_comments(tmp_path, monkeypatch):
    import run
    f = tmp_path / ".env"
    f.write_text('CV_T1=auto            # auto | paddle | tesseract\nCV_T2="two words # kept"\nCV_T3=\'single\'\n'
                 '# comment\nCV_T4=pass#word\nCV_T5=   padded   \n', encoding="utf-8")
    for k in ("CV_T1", "CV_T2", "CV_T3", "CV_T4", "CV_T5"):
        monkeypatch.delenv(k, raising=False)
    run.load_dotenv(f)
    import os
    assert os.environ["CV_T1"] == "auto" and os.environ["CV_T2"] == "two words # kept"
    assert os.environ["CV_T3"] == "single" and os.environ["CV_T4"] == "pass#word" and os.environ["CV_T5"] == "padded"
    for k in ("CV_T1", "CV_T2", "CV_T3", "CV_T4", "CV_T5"):
        os.environ.pop(k, None)


def test_server_header_does_not_reveal_versions(client):
    assert client.get("/api/health").headers["Server"] == "ChequeVerify"
    assert client.get("/nothing-here").headers["Server"] == "ChequeVerify"


def test_production_image_has_no_cors_origins_by_default():
    text = (Path(__file__).resolve().parents[2] / "Dockerfile").read_text()
    assert 'CV_CORS_ORIGINS=""' in text
