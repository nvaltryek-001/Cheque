# Security and privacy

Status: **not approved for production use with real customer cheques.** The controls below are implemented
and tested; the list of what is *not* done (end of this page) must be worked through first.

## Controls

| Concern | Control |
|---|---|
| Authentication | Signed, time-limited bearer tokens (itsdangerous, 8 h). Tokens embed a per-user version: sign-out, password change/reset and deactivation revoke all of them. Werkzeug password hashing. A token is obtained either with a password (administrator sign-in) or with a one-time code mailed to an institutional address; both give the same token. |
| Sign-in by institutional email | Any `@saividya.ac.in` address (exact domain, strict address syntax) may ask for an 8-digit code; typing an address alone signs nobody in. The code is random, stored only as a keyed hash, single-use, expires after 10 minutes, is burned after 5 wrong tries, is rate-limited per address and per client, and wrong codes feed the same persisted throttle as wrong passwords. Answers never reveal whether an account exists. Codes are never returned by the API, logged or audited (except in the development-only `log` delivery mode, which warns at start-up). New accounts are *awaiting approval*: signed in, but every data endpoint answers 403 until an administrator gives a role. Email accounts have no usable password and the server refuses to set one. Details, limits and settings: `docs/AUTHENTICATION.md`. |
| Sign-in brute force | Failed sign-ins are remembered in SQLite (survive restarts, shared by all threads). A *(client IP, username)* pair is blocked after `CV_LOGIN_MAX_ATTEMPTS` (5) failures and a client IP after `CV_LOGIN_IP_MAX_ATTEMPTS` (20) failures across all usernames (password spraying), for `CV_LOGIN_LOCK_SECONDS` (300). While blocked the password is not even checked. Unknown usernames are throttled and answered exactly like real ones. There is **no account-wide lock**: the earlier design let anyone who knew the username lock the administrator out. HTTP 429 with `Retry-After`; `python manage.py unlock-logins` clears all blocks. Change-password attempts are throttled the same way. |
| Passwords | At least `CV_MIN_PASSWORD_LENGTH` characters (default 8, the Render blueprint sets 12), letters mixed with digits/symbols, a short list of common passwords and the username itself are refused, a new password must differ from the current one. The first admin password taken from `CV_ADMIN_PASSWORD` is checked against the same rules at start-up (the server refuses to start with a weak one). |
| Authorization | Every endpoint declares allowed roles. Enrollment, specimen changes, users, settings, dataset import, training and deletion of verifications are admin-only. Auditors are read-only. An account that is awaiting approval is refused by every endpoint except `/api/auth/me` and `/api/auth/logout` (a test walks the whole route table to prove it). |
| Unauthenticated enrollment | Impossible: specimen upload requires an admin token. |
| Biometric data | Specimen and query images are served only to admin/operator through authenticated endpoints; each specimen view is written to the audit log. No public static route exposes storage. |
| Uploads | Size limit (413), extension allow-list, magic-byte check, **pixel limit read from the file header before decoding** (`CV_MAX_IMAGE_PIXELS`, default 25 million, 413 `image_too_large`; a decompression-bomb PNG that took 968 MB of memory is now refused with ≈111 MB peak), OpenCV's own decoder limit as a second line, full decode, dimension checks; stored under UUID names; the client filename is never used as a path. |
| Abuse limits | Sliding-window rate limit per signed-in user on the expensive endpoints (uploads, batch, reprocess, signature crop/preprocess, verifications, specimen upload/replace): `CV_RATE_LIMIT_PER_MIN` cost units per minute (default 120; a batch costs one unit per file; 0 = off). HTTP 429 with `Retry-After`. Counted per process: several instances each keep their own counts. |
| Path traversal | All file reads resolve inside the storage area (`storage.safe_path`); dataset images are looked up through the manifest, not by client path; report downloads accept only plain names. |
| Error leakage | Unhandled errors return a generic message; no stack traces or filesystem paths. `/api/health` returns only status and version. The `Server` header names no software or version. |
| Response headers | **Content-Security-Policy** – app: `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; font-src 'self'; connect-src 'self'; object-src 'none'; frame-src 'none'; worker-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'` (no `unsafe-inline`, no `unsafe-eval`); API: `default-src 'none'; frame-ancestors 'none'`. **HSTS** (`CV_HSTS_MAX_AGE`, default one year) on HTTPS responses only – behind a proxy it follows `X-Forwarded-Proto` only when `CV_PROXY_HOPS` says a proxy is trusted. `Permissions-Policy` (camera, microphone, geolocation, payment, USB, sensors off), `Cross-Origin-Opener-Policy` and `Cross-Origin-Resource-Policy` `same-origin`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store` on API responses. |
| Client address | `CV_PROXY_HOPS` (Render: 1) makes the audit log and the sign-in throttle use the real client address behind the platform's proxy. With 0 no `X-Forwarded-*` header is trusted, so a client cannot choose the address that is logged or throttled. If the value is wrong behind a proxy, all users share one address and one attacker can block everyone. |
| CORS | Allow-list (`CV_CORS_ORIGINS`); the Docker image sets it empty (the UI is served from the same origin). |
| Logs | The audit log stores action, user, record id and IP – never images, passwords, typed text of failed sign-ins for unknown users, or full account numbers. |
| Exports | Account numbers masked in lists, CSV exports and PDF reports; CSV cells starting with `= + - @` are escaped; reportlab markup in user text is escaped. |
| Secrets | No hard-coded secrets. Secret key from `CV_SECRET_KEY` or a random key in `instance/secret_key`. Files are created with umask 077 (database, uploads, specimens, key: mode 600). The container drops root to uid 10001 after preparing the data volume. |
| Supply chain | CI runs `pip-audit` and `npm audit` (high/critical) on every push and weekly, Dependabot opens weekly update pull requests (pip, npm, GitHub Actions, Docker), GitHub Actions are pinned to commit SHAs and the workflow token is read-only. OpenCV is pinned below 5 (golden-value tests). |
| Private data in git | `.gitignore` excludes datasets, the instance folder, databases, keys and `.env`. **`scripts/prepush_audit.py`** shows exactly what a push would publish and fails on datasets, cheque images, databases, environment files, keys, secrets and – with a private terms file – on real account numbers, names and cheque numbers (see below). CI runs it without private terms on every branch. |

## Pre-push audit

```bash
python scripts/prepush_audit.py --terms ~/private_terms.txt        # commits not yet on the upstream branch
python scripts/prepush_audit.py --base origin/main                 # everything the branch adds to main (CI)
python scripts/prepush_audit.py --staged --terms ~/private_terms.txt   # before a commit
```

It reads every commit in the range (a file added and removed again is still published with the history) and
checks paths, added lines and commit messages. The private terms file lives **outside** the repository – one
value per line: `account_number|<real number>`, `payee_name|<real name>`, `W:cheque_number|<number>` (`W:` =
warn only). Matching ignores case and spacing, digit strings also match with spaces or hyphens, and the report
prints only the label and number of a term, never its value. Exit status 1 means: do not push.
Heuristic warnings (IFSC-like codes, 12–18 digit numbers, e-mail addresses) need a human look; synthetic test
values trigger some of them by design.

## Verification done

* `backend/tests/test_security.py` – decompression-bomb refusal on every upload route (decoder never reached),
  sign-in throttle (per client, spraying, expiry, no account lock-out, audit entries without leaked text),
  per-user rate limits, password policy incl. the first admin, response headers incl. HSTS over a simulated
  proxy, CORS allow-list, `.env` parsing.
* `backend/tests/test_email_login.py` – the address rule (valid and malicious addresses), the full email flow, single use,
  expiry, attempt and request limits, no account enumeration, secrecy of codes in the database, logs and audit log,
  roles and approval, the route-table walk for unapproved accounts, SMTP delivery against a local fake SMTP server
  (STARTTLS required, unencrypted remote servers refused, unreachable server), and in-place upgrade of an older database.
* `backend/tests/test_frontend_theme.py` – the visual refresh keeps the strict policy: no inline styles, no external fonts,
  images or `data:` URLs, fonts shipped with their licence, text and control contrast, reduced-motion coverage.
* `backend/tests/test_prepush_audit.py` – the audit against throw-away repositories.
* `backend/tests/browser_check.py` – headless Chromium against a running server: no policy violations on any
  page, authenticated images and dynamic styles still work, and the policy is really enforced (inline script,
  `eval`, inline `style` attribute, foreign image and foreign `fetch` are blocked). With `--email-log` it also drives
  the email sign-in in the browser (other domain refused, wrong code counted, waiting page, approval, sign-out).
  Note: this was run against an esbuild bundle of the same sources because Vite could not be installed in the
  build environment; CI builds with Vite and the Docker job checks the served headers again.
* The Docker CI job checks headers on the real container, HSTS and the forwarded client address behind one
  proxy hop, and the password policy (runs after a push).

## Not done / known limitations

* **Tokens live in `sessionStorage`** (tab-scoped, 8 h, revocable). With the strict CSP there is no inline or
  third-party script that could read them, but an XSS bug in the app itself could. Moving to HttpOnly,
  SameSite cookies needs CSRF protection and changes for every API client – recommended together with SSO.
* **No MFA / SSO.** A bank deployment should put the app behind its identity provider. The emailed code proves control of a
  mailbox, not who is at the keyboard: whoever can read a user's mail (compromised, shared or forwarded mailbox) can sign
  in as that user, so the application is only as strong as the institution's mail security. Real mail delivery has not
  been tried from the development environment (it cannot send mail); the SMTP path is tested against a local fake server.
* **No encryption at rest** by the application: use an encrypted volume; file modes are 600.
* A distributed guessing attack from many addresses is not stopped by per-address throttling: strong passwords,
  MFA and a WAF are needed. Likewise nothing stops someone from asking many institutional mailboxes for codes (each person only receives
  unexpected mail); a WAF or CAPTCHA in front of `/api/auth/email/request` is the usual answer. The sign-in throttle is shared through SQLite; the rate limiter is per process.
* Dependency scan (GitHub Actions, 2026-11-01): `pip-audit -r backend/requirements.txt` found no known vulnerabilities. `npm audit` reports one package at high level, the build tool Vite 5
  (development-server advisories, not part of the deployed image; details and the fix in `reports/final/SECURITY_AUDIT.md` finding 2). CI blocks on the packages that ship (`npm audit --omit=dev`)
  and reports the build tools as warnings. `package-lock.json` does not exist yet (the registries are blocked in the build sandbox): the CI job `dependency-audit` uploads a generated one;
  commit it and switch the Dockerfile and CI to `npm ci`. Python dependencies are ranges, not hashes.
* Waitress + `CV_PROXY_HOPS` behind Render's proxy is covered by the Docker CI job, not by a test on Render itself.
* No data-retention automation, no independent penetration test, no formal threat model review.
* Throughput-based denial of service is limited by the rate limits only; OCR is CPU-bound.

**Do not describe this system as production-ready for banking without addressing the above.**
