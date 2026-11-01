"""Browser check of the built web app against a running server (headless Chromium via Playwright).

    python tests/browser_check.py http://127.0.0.1:8000 <admin-password> [--shots DIR] [--email-log SERVER_LOG]

Needs `pip install playwright` + a Chromium build, and a built Frontend/dist served by the server.
Uses synthetic images only. It checks that

  * every page loads under the Content-Security-Policy with no securitypolicyviolation events and no
    console errors (dynamic widths/positions set through React `style` props still apply),
  * the policy really is enforced by the browser (positive controls: inline script, eval(), inline style
    attribute, foreign image and foreign fetch are all blocked),
  * sign-in through the form, every route, authenticated images (blob: URLs) and sign-out work,
  * the enrollment workflow in the browser: readiness meters, recording a signing session through the form,
    multi-file upload into the session, duplicate refusal, and the reference set turning "ready",
  * sign-in by institutional email (only when the server offers it and `--email-log` names the server log written
    with CV_EMAIL_DELIVERY=log): another domain refused, wrong code counted, a first sign-in lands on the "waiting for
    approval" page and sees no data, an administrator's approval takes effect, sign-out.

Exit code 0 only when every check passes.
"""
import argparse
import datetime
import json
import re
import sys
import time
import urllib.error
import urllib.request
import uuid

import cv2
import numpy as np

ROUTES = ["/", "/cheques", "/batch", "/enrollment", "/datasets", "/datasets/signature", "/training",
          "/analytics", "/history", "/reports", "/settings"]
results: list[tuple[str, bool]] = []


def check(name: str, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""), flush=True)


# ------------------------------------------------------------------ seeding through the HTTP API
def call(base, method, path, token=None, body=None, files=None, fields=None):
    headers, data = {}, None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
    if files or fields:
        b = uuid.uuid4().hex
        parts = [f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in (fields or {}).items()]
        for k, item in (files or {}).items():
            for name, content in (item if isinstance(item, list) else [item]):     # several files under one field
                parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"; filename="{name}"\r\n'
                             f'Content-Type: image/png\r\n\r\n'.encode() + content + b"\r\n")
        data = b"".join(parts) + f"--{b}--\r\n".encode()
        headers["Content-Type"] = f"multipart/form-data; boundary={b}"
    try:
        with urllib.request.urlopen(urllib.request.Request(base + path, data=data, headers=headers, method=method), timeout=120) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def signature(seed):
    rng = np.random.default_rng(seed)
    img = np.full((100, 320, 3), 245, np.uint8)
    pts = np.cumsum(rng.normal(0, 6, (40, 2)), axis=0)
    pts = (pts - pts.min(0)) / max(np.ptp(pts), 1) * [280, 70] + [20, 15]
    cv2.polylines(img, [pts.astype(np.int32)], False, (30, 30, 120), 2, cv2.LINE_AA)
    cv2.line(img, (25, 80), (290, 75), (30, 30, 120), 2)
    return img


def seed(base, password):
    png = lambda im: cv2.imencode(".png", im)[1].tobytes()  # noqa: E731
    s, b = call(base, "POST", "/api/auth/login", body={"username": "admin", "password": password})
    assert s == 200, b
    token = b["data"]["token"]
    sig = signature(uuid.uuid4().int % 1_000_000)   # fresh drawing each run: duplicate specimens are rejected
    cheque = np.full((470, 1024, 3), 248, np.uint8)
    cv2.putText(cheque, "CANARA BANK  IFSC: CNRB0002854", (40, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (60, 60, 60), 2)
    cv2.putText(cheque, "Pay  Browser Check", (40, 120), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (20, 20, 20), 2)
    cheque[290:390, 690:1010] = sig
    code = "BROWSER-" + uuid.uuid4().hex[:6]
    s, b = call(base, "POST", "/api/signers", token, {"signer_code": code, "bank": "canara", "full_name": "Browser Check"})
    assert s == 201, b
    sid = b["data"]["id"]
    s, b = call(base, "POST", f"/api/signers/{sid}/specimens", token, files={"file": ("s.png", png(sig))})
    assert s == 201, b
    s, b = call(base, "POST", "/api/cheques", token, files={"file": ("c.png", png(cheque))})
    assert s == 201, b
    cid = b["data"]["id"]
    s, b = call(base, "POST", "/api/verifications", token, fields={"signer_id": sid, "cheque_id": cid})
    assert s == 201, b
    return sid, cid, token


def fresh_signature_png():
    return cv2.imencode(".png", signature(uuid.uuid4().int % 1_000_000))[1].tobytes()


# ------------------------------------------------------------------ the browser part
INIT = """
window.__csp = [];
document.addEventListener('securitypolicyviolation', e => window.__csp.push(e.violatedDirective + ' | ' + (e.blockedURI || 'inline')));
"""

CONTROLS = {
    "inline script": ("() => { const s = document.createElement('script'); s.textContent = 'window.__ran = 1';"
                      " document.head.appendChild(s); return window.__ran === 1; }", False, "script-src"),
    # eval() called from inside a DevTools evaluation is exempt from CSP, so the control uses a string timer,
    # which the page itself compiles after the evaluation has returned (the same check as eval).
    "eval (string timer)": ("() => new Promise(res => { window.__evalRan = false; setTimeout('window.__evalRan = true', 0);"
                            " setTimeout(() => res(window.__evalRan), 150); })", False, "script-src"),
    "inline style attribute": ("() => { const d = document.createElement('div'); d.setAttribute('style', 'width: 123px');"
                               " document.body.appendChild(d); const w = getComputedStyle(d).width; d.remove(); return w === '123px'; }",
                               False, "style-src"),
    "style through the CSSOM (what React uses)": ("() => { const d = document.createElement('div'); d.style.width = '123px';"
                                                  " document.body.appendChild(d); const w = getComputedStyle(d).width; d.remove(); return w === '123px'; }",
                                                  True, None),
    "foreign image": ("() => { new Image().src = 'https://example.invalid/x.png'; return true; }", True, "img-src"),
    "foreign fetch": ("() => fetch('https://example.invalid/x').then(() => true, () => false)", False, "connect-src"),
}


def blob_image_shown(page, timeout_ms: int = 8000) -> bool:
    """True when an <img> with a blob: URL has been decoded (authenticated images are fetched with the token).
    Polls with page.evaluate: Playwright's wait_for_function compiles its predicate with eval() inside the page,
    which the policy (rightly) blocks."""
    waited = 0
    while True:
        if page.evaluate("[...document.images].some(i => i.currentSrc.startsWith('blob:') && i.naturalWidth > 0)"):
            return True
        if waited >= timeout_ms:
            return False
        page.wait_for_timeout(200)
        waited += 200


def enrollment_flow(page, base, sid, token):
    """Readiness meters, a signing session recorded through the form, multi-file upload, duplicate refusal."""
    day = (datetime.date.today() - datetime.timedelta(days=5)).isoformat()
    page.goto(f"{base}/#/enrollment/{sid}")
    page.wait_for_selector("[role=meter]")
    body = page.inner_text("body").lower()          # pills are shown capitalised by CSS; inner_text applies it
    check("readiness panel shows the two meters", page.locator("[role=meter]").count() == 2)
    check("a thin reference set is announced as not ready", "reference not ready" in body and "no match will be issued" in body, body[:200])
    check("an unsessioned specimen is flagged", "no session" in body)

    page.click("button:has-text('Record signing session')")
    page.fill("input[type=date]", day)
    page.fill("input[placeholder^='Reference of the signed consent']", "BROWSER-CONSENT")
    page.click("button:has-text('Record session')")
    page.wait_for_selector(f"td:has-text('{day}')")
    check("signing session recorded through the form", True)

    page.locator("button:has-text('Add signatures')").first.click()
    files = [{"name": f"browser{i}.png", "mimeType": "image/png", "buffer": fresh_signature_png()} for i in range(2)]
    page.set_input_files("input[type=file]", files)
    page.click("button:has-text('Enroll 2 signatures')")
    page.wait_for_selector(".upload-list .pill-good")
    modal = page.inner_text(".modal")
    check("multi-file upload enrolls both signatures", "2 enrolled" in modal and "0 rejected" in modal, modal[:200])
    page.click(".modal button:has-text('Close')")
    page.wait_for_timeout(600)
    now = page.get_attribute("[role=meter] >> nth=0", "aria-valuenow")
    check("specimen meter counts the two independent signatures", now == "2", now)
    width = page.evaluate("document.querySelector('.meter .bar-fill').getBoundingClientRect().width")
    check("meter width set through the CSSOM applies under the policy", width and width > 0, width)

    # the same image again is refused, and the refusal is explained
    page.locator("button:has-text('Add signatures')").first.click()
    page.set_input_files("input[type=file]", [files[0]])
    page.click("button:has-text('Enroll 1 signature')")
    page.wait_for_selector(".upload-list .warn-text")
    check("a repeated image is refused with a reason", "duplicate" in page.inner_text(".upload-list").lower(), page.inner_text(".upload-list"))
    page.click(".modal button:has-text('Close')")

    # a second signing day (through the API) completes the reference set
    day2 = (datetime.date.today() - datetime.timedelta(days=40)).isoformat()
    s, b = call(base, "POST", f"/api/signers/{sid}/sessions", token, {"session_date": day2, "consent_ref": "BROWSER-CONSENT"})
    assert s == 201, b
    s, b = call(base, "POST", f"/api/signers/{sid}/specimens/batch", token, fields={"session_id": b["data"]["id"]},
                files={"files": [(f"old{i}.png", fresh_signature_png()) for i in range(3)]})
    assert s == 201 and b["data"]["enrolled"] == 3, b
    page.reload()                      # the page is already on this route: a same-URL goto() would keep the stale data
    page.wait_for_selector("text=so a MATCH can be issued")
    check("five independent specimens on two days make the reference set ready", "reference ready" in page.inner_text("body").lower())
    page.goto(f"{base}/#/enrollment")
    page.wait_for_selector("text=signers have a ready reference set")
    check("the signer list shows the reference status", "reference ready" in page.inner_text("body").lower())
    check("enrollment workflow: no policy violations", page.evaluate("window.__csp") == [], page.evaluate("window.__csp"))


def sign_in_with_password(page, password):
    """The administrator form. When the server also offers email sign-in it sits behind the "Administrator sign-in" button."""
    if page.locator("input[autocomplete=username]").count() == 0:
        page.click("button:has-text('Administrator sign-in')")
    page.fill("input[autocomplete=username]", "admin")
    page.fill("input[autocomplete=current-password]", password)
    page.click("button[type=submit]")


def code_from_log(path, email, timeout_s=8.0):
    """The one-time code the server wrote to its log (CV_EMAIL_DELIVERY=log, development only)."""
    pattern = re.compile(r"sign-in code for " + re.escape(email) + r" is (\d{8})")
    end = time.time() + timeout_s
    while time.time() < end:
        found = pattern.findall(open(path, encoding="utf-8", errors="replace").read())
        if found:
            return found[-1]
        time.sleep(0.2)
    return None


def email_flow(browser, base, password, log_path):
    """Passwordless sign-in as a person would do it: address, code, waiting page, approval, sign-out."""
    config = call(base, "GET", "/api/auth/config")[1]["data"]
    if not (config["email_signin"] and log_path):
        print("SKIP email sign-in flow (needs a server with email delivery and --email-log)", flush=True)
        return
    domain = config["email_domain"]
    local = "browser." + uuid.uuid4().hex[:5] + "9"          # a digit in the part after the dot: shown name is "Browser"
    address = f"{local}@{domain}"
    page = browser.new_context(viewport={"width": 1400, "height": 900}).new_page()
    page.add_init_script(INIT)
    errors: list[str] = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append("pageerror: " + str(e)))

    page.goto(base + "/", wait_until="networkidle")
    page.wait_for_selector(".auth-card")
    check("email is the first way in when the server offers it",
          page.locator("input[type=email]").count() == 1 and page.locator("input[type=password]").count() == 0)
    check("the administrator sign-in stays one click away", page.locator("button:has-text('Administrator sign-in')").count() == 1)

    page.fill("input[type=email]", "someone@gmail.com")
    page.click("button:has-text('Send sign-in code')")
    page.wait_for_selector(".form-error")
    check("an address from another domain is refused and the allowed domain is named", domain in page.inner_text(".form-error"), page.inner_text(".form-error"))
    check("no code screen appears for a refused address", page.locator(".code-input").count() == 0)

    page.fill("input[type=email]", local)                      # only the part before the @
    page.click("button:has-text('Send sign-in code')")
    page.wait_for_selector(".code-input")
    check("typing only the part before the @ completes the institutional address", address in page.inner_text(".auth-card"), page.inner_text(".auth-card"))
    page.fill(".code-input", "12ab3456")
    check("the code box keeps digits only and groups them", page.input_value(".code-input") == "1234 56", page.input_value(".code-input"))

    page.fill(".code-input", "00000000")
    page.click("button:has-text('Verify and sign in')")
    page.wait_for_selector(".form-error")
    check("a wrong code is refused and the remaining tries are shown", "tries left" in page.inner_text(".form-error"), page.inner_text(".form-error"))
    code = code_from_log(log_path, address)
    check("the server issued a code for the address", bool(code))
    page.fill(".code-input", code or "00000000")
    page.click("button:has-text('Verify and sign in')")
    page.wait_for_selector("text=Waiting for approval")
    check("a first sign-in waits for approval and shows no application", page.locator("nav[aria-label=Main]").count() == 0)
    token = page.evaluate("sessionStorage.getItem('cv_token')")
    s, b = call(base, "GET", "/api/cheques", token)
    check("the unapproved account is refused data by the server too", s == 403 and b["error"]["code"] == "approval_pending", (s, b))

    s, b = call(base, "POST", "/api/auth/login", body={"username": "admin", "password": password})
    admin = b["data"]["token"]
    users = call(base, "GET", "/api/users", admin)[1]["data"]
    uid = next(u["id"] for u in users if u["email"] == address)
    check("the administrator sees the person as awaiting approval", next(u for u in users if u["id"] == uid)["role"] == "pending")
    s, b = call(base, "PATCH", f"/api/users/{uid}", admin, {"role": "operator"})
    check("an administrator approves by giving a role", s == 200, b)
    page.click("button:has-text('Check again')")
    page.wait_for_selector("nav[aria-label=Main]")
    check("approval takes effect without signing in again", True)
    check("the name shown is derived from the address", page.inner_text(".who-name") == "Browser", page.inner_text(".who-name"))
    check("the role is shown in plain words", page.inner_text(".who-role") == "Operator", page.inner_text(".who-role"))
    page.goto(f"{base}/#/settings")
    page.wait_for_selector("text=Your sign-in")
    check("no password form for an account that signs in by email", page.locator("text=Current password").count() == 0)
    page.goto(f"{base}/#/")
    page.click("button[aria-label='Sign out']")
    page.wait_for_selector(".auth-card")
    check("sign-out returns to the email form", page.locator("input[type=email]").count() == 1 and not page.evaluate("sessionStorage.getItem('cv_token')"))
    check("email sign-in: no policy violations", page.evaluate("window.__csp") == [], page.evaluate("window.__csp"))
    statuses = sorted(re.findall(r"status of (\d{3})", " ".join(errors)))
    check("email sign-in: the only console errors are the two refusals provoked on purpose (other domain, wrong code)",
          len(errors) == 2 and statuses == ["400", "401"], errors)
    call(base, "DELETE", f"/api/users/{uid}", admin)            # leave the installation as it was
    page.context.close()


def run(base, password, shots, email_log=""):
    from playwright.sync_api import sync_playwright
    sid, cid, token = seed(base, password)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_context(viewport={"width": 1400, "height": 900}).new_page()
        page.add_init_script(INIT)
        console_errors: list[str] = []
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: console_errors.append("pageerror: " + str(e)))
        headers_seen: dict[str, dict] = {}
        page.on("response", lambda r: headers_seen.setdefault(r.url.replace(base, ""), dict(r.headers)))

        page.goto(base + "/", wait_until="networkidle")
        page.wait_for_selector(".auth-card")
        csp = headers_seen.get("/", {}).get("content-security-policy", "")
        check("the page is served with a Content-Security-Policy", "script-src 'self'" in csp, csp)
        check("the script and the stylesheet are served with it too",
              all("content-security-policy" in h for u, h in headers_seen.items() if u.startswith("/assets/")) and
              any(u.startswith("/assets/") for u in headers_seen))
        check("login screen: no policy violations", page.evaluate("window.__csp") == [], page.evaluate("window.__csp"))

        # --- sign in through the form
        sign_in_with_password(page, password)
        page.wait_for_selector("nav[aria-label=Main]")
        check("sign-in through the form works", page.evaluate("!!sessionStorage.getItem('cv_token')"))

        # --- every route renders under the policy
        for route in ROUTES + [f"/cheques/{cid}", f"/enrollment/{sid}", f"/verify?cheque={cid}&signer={sid}"]:
            page.goto(f"{base}/#{route}")
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(500)
            text = page.inner_text("body")
            ok = "Page not found" not in text and len(text.strip()) > 80
            check(f"page {route} renders", ok, text[:120].replace("\n", " "))
            if shots:
                page.screenshot(path=f"{shots}/{route.strip('/').replace('/', '_').replace('?', '_').replace('=', '-').replace('&', '_') or 'dashboard'}.png")
        check("no policy violations while visiting every page", page.evaluate("window.__csp") == [], page.evaluate("window.__csp"))
        enrollment_flow(page, base, sid, token)

        # --- authenticated images arrive as blob: URLs and are displayed
        page.goto(f"{base}/#/cheques/{cid}")
        check("cheque image shown (blob:)", blob_image_shown(page))
        box = page.evaluate("(() => { const b = document.querySelector('.crop-box'); return b ? b.getBoundingClientRect().width : null; })()")
        check("dynamic style set through React still applies (signature box)", box is None or box > 0, box)
        page.goto(f"{base}/#/enrollment/{sid}")
        check("specimen image shown (blob:)", blob_image_shown(page))
        check("no console errors", not console_errors, console_errors[:3])

        # --- the browser really enforces the policy (positive controls)
        for name, (js, allowed, directive) in CONTROLS.items():
            before = len(page.evaluate("window.__csp"))
            got = page.evaluate(js)
            page.wait_for_timeout(150)
            new = page.evaluate("window.__csp")[before:]
            if directive:
                check(f"control: {name} is {'reported' if allowed else 'blocked'} by {directive}",
                      any(directive in v for v in new) and (got is True if allowed else got is False), (got, new))
            else:
                check(f"control: {name} is allowed", got is True and not new, (got, new))

        # --- sign out
        page.goto(f"{base}/#/")
        page.click("button[aria-label='Sign out']")
        page.wait_for_selector(".auth-card")
        check("sign-out clears the token", page.evaluate("!sessionStorage.getItem('cv_token')"))
        email_flow(browser, base, password, email_log)
        browser.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("password")
    ap.add_argument("--shots", default="")
    ap.add_argument("--email-log", default="", help="server log of a server started with CV_EMAIL_DELIVERY=log (development only)")
    a = ap.parse_args()
    run(a.base.rstrip("/"), a.password, a.shots, a.email_log)
    print(json.dumps({"passed": sum(r[1] for r in results), "failed": [r[0] for r in results if not r[1]]}))
    sys.exit(0 if all(r[1] for r in results) else 1)
