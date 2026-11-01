# Security audit

Last updated 2026-11-01. The first table below is the original review of the rebuilt application; the section after it is
the current, severity-classified status and supersedes the old "residual risks" paragraph (which said there was no general rate
limiting; there has been since the phase 2 hardening).

Review of the rebuilt application against the directive's checklist. Controls are described in
`docs/SECURITY.md`; the "Evidence" column names the automated test that exercises each one.

| Risk | Status | Evidence |
|---|---|---|
| Unauthenticated enrollment | Fixed by design – admin token required | `test_enrollment_duplicates_and_authorization` (operator → 403) |
| Unrestricted specimen retrieval | Admin/operator only, audited per view | same test (auditor → 403) |
| Insecure upload | Size, extension, magic bytes, decode, dimension checks; UUID storage names | `test_invalid_file_type`, `test_extension_spoofing_rejected`, `test_large_file_rejected`, `test_corrupted_image` |
| Path traversal | `safe_path` containment; manifest lookup for dataset images; name allow-list for reports | `test_dataset_image_path_traversal` |
| Hard-coded secrets | None; random secret key and admin password on first run | code review |
| Excessive CORS | Allow-list only, default localhost:5173 | code review (`app/__init__.py`) |
| Sensitive data in logs | Audit log has no images/passwords/full account numbers; app log has action names only | code review (`services/audit.py`) |
| Unsafe file storage | Private directory, never served statically | code review |
| Missing authorization | Every route has `login_required(...)` | `test_auth_required_everywhere`, role tests |
| Unrestricted reports | Reports require login; audit-log export admin/auditor only; accounts masked | `test_audit_log_access`, CSV checks |
| Brute force | Lockout after 5 failures / 5 min | `test_bad_credentials_and_lockout` |
| Session revocation | Token version bump on logout/password/deactivation | `test_login_logout_and_token_revocation`, `test_user_crud_and_roles` |
| Error information leakage | Generic 500 body, JSON errors for unknown API routes | `test_unknown_api_route_json` |
| CSV formula injection | Leading `= + - @` escaped | code review (`services/reports.py`) |

## Status on 2026-11-01 (severity-classified)

Severity scale: **Critical** (exploitable now, severe), **High** (serious, needs action before real data), **Medium**, **Low**,
**Informational**. No value of any secret, password, one-time code or private cheque datum appears in this file.

| # | Severity | Finding | Status |
|---|---|---|---|
| 1 | **High** | A real account number, cheque numbers and MICR codes from the private list are in the original code: `backend/utils/test_field_extraction_pipeline_patched.py` on `main`, and its copy under `legacy/` in the first commit of the public branch (public since 2026-10-31). Not introduced by recent work; the image does not contain `legacy/` (`.dockerignore`). | **OPEN, owner decision.** Removing it from history needs a force-push of `main` and the branch (and the numbers should be treated as disclosed). Not done: it would modify `main` and rewrite history, which need explicit approval. |
| 2 | **High** (by the audit's own threshold) | `npm audit --audit-level=high` reports one package at high level: the build tool **Vite <= 6.4.3** (a direct dev dependency; this project uses 5.4.x). The advisories are about Vite's **development server**: path traversal in optimized-dependency `.map` handling, cross-origin script execution through custom middleware, files outside `server.fs.allow` served when project paths match system paths, a `server.fs.deny` bypass on Windows alternate paths, and several `launch-editor` issues on Windows (the helper behind "open in editor"). npm's suggested fix is Vite 8.3.5, a major version. No other package is reported at high or critical level, including `react` and `react-dom`, the only packages inside the page. Vite is not in the Docker image: the build stage compiles the page and only the compiled files are copied on. | **OPEN.** Exposure: a developer's machine while `npm run dev` is running; not the deployed application. Fix, which needs a machine that can run npm: upgrade Vite and `@vitejs/plugin-react` to a patched version, run `npm run build`, commit `package-lock.json`. Not done here because a major upgrade of the build tool could not be tested offline. CI now gates the packages that ship (`npm audit --omit=dev`) and reports the build-tool advisories as warnings on every run. |
| 3 | Medium | Waitress 3 removed `X-Forwarded-For` / `-Proto` before the application saw them. Behind Render's proxy every visitor would have shared one address (one sign-in throttle for all users, wrong audit-log addresses) and HSTS would not be sent. | **FIXED** (`clear_untrusted_proxy_headers=False`; `CV_PROXY_HOPS` stays the only trust setting). Pinned by a test; verified in a real container on GitHub: the step "Behind one reverse proxy" passed on commits `93c2f0c`, `1215ebc` and `23df57e` (`DEPLOYMENT_VERIFICATION.md`). |
| 4 | Medium | The SIGTERM handler could fail to start its watchdog (a signal during `print` raised "reentrant call"): a stuck request could keep the process alive until the platform killed it (availability, and a risk of cutting a write short). | **FIXED**; new test fails on the old code; 60 of 60 repeated runs under CPU load exit in about 1 s (about 1 in 3 hung before). |
| 5 | Medium | Real account numbers and payee names were in test files of a local, unpushed commit. | **FIXED before publishing**: the pushed history has that commit squashed with the commit that replaced them by synthetic values. Pre-push audit with the private-terms list: 0 errors in the commits added by this work; the only error it reports anywhere on the branch is finding 1, in the original first commit; the low-sensitivity warnings that remain are finding 8. |
| 6 | Medium | Memory exhaustion: four simultaneous 25-megapixel uploads peaked at 0.7-1.1 GB, enough for an out-of-memory kill on a 512 MB instance. | **FIXED**: header-based pixel limit (12 MP in the Docker image and on Render), two images at a time, `503 busy` for the rest, glibc malloc settings. Eight simultaneous maximum-size uploads in a 512 MB container: no out-of-memory kill (GitHub container run); peak 250-395 MB in local rehearsals. |
| 7 | Low | Frontend builds are not reproducible: no `package-lock.json`, and the Dockerfile runs `npm install`, so each build resolves the newest versions inside the ranges. | OPEN (same fix as #2). |
| 8 | Low | A bank routing code (`micr_code`, low sensitivity in the owner's own classification) appears in three OpenCV golden-test fixtures (commit `6a18ab8`), and one amount in words in `backend/tests/test_ocr_scoring.py` (commit `4fb09f5`). Both are warnings of the private-terms audit, not errors. | Accepted; listed here so it is a decision, not a surprise. |
| 9 | Informational | `pip-audit -r backend/requirements.txt` found no known vulnerabilities (GitHub, 2026-11-01). | - |
| 10 | Informational | The pre-push audit reports warnings for synthetic e-mail addresses (`first.last@...`, `someone@gmail.com` as a rejected-domain test), made-up IFSC-like and long numbers in tests, and two header-only CSV templates. No personal address of the owner is in tracked files. | Reviewed. |
| 11 | Informational | Open design limits: tokens in `sessionStorage` (no HttpOnly cookie), no MFA or SSO, no encryption at rest, one SQLite file and one process, e-mail sign-in proves mailbox control only, SMTP delivery untested, no external penetration test. | Documented in `docs/SECURITY.md`. |
| 12 | Low | New endpoint `POST /api/cheques/from-sample` reads image files from the server's dataset folder (demo samples). Controls: administrator or operator only; the image is named by split, bank and file name and must appear in the folder listing, never by a client path; names allow only letters, digits and `_ . -`; the resolved path must lie inside the dataset folder (a link leading out is refused); same size, format, pixel limit, rate limit and image slots as an upload; the audit log records split and bank, not the file name. | Tested: 7 tests, two of the protections mutation-checked (removing them makes a test fail). |

**Controls verified since the first review** (each has automated tests; the container ones also ran on GitHub): signed bearer tokens with
server-side revocation; three roles on every endpoint; persisted sign-in throttle per (address, user) without locking accounts;
per-user rate limits; password policy (12 characters on Render, checked for the first administrator at start-up); strict
Content-Security-Policy and other security headers, HSTS only over HTTPS; upload validation by content, a pixel limit read from the
header before decoding, random storage names; private files (mode 600) and a non-root server (uid 10001, checked in a real
container); crash-safe writes and start-up recovery; backups that exclude the secret key; e-mail one-time codes stored only as
keyed hashes with a cap on guesses and approval by an administrator for new accounts; `scripts/prepush_audit.py` against secrets,
datasets, images, databases and (locally) the owner's private values.

**Recommendation.** The two High findings concern the repository history (#1) and build-time tooling (#2). They do not stop a
pilot deployment that holds no real cheque data, but #1 should be decided and #2 closed **before real cheque data is loaded or the
repository is shared more widely**. This system is not validated for banking decisions: see the signature and OCR limits in
`DATASET_TEST_RESULTS.md`.
