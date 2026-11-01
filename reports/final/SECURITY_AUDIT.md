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
| 1 | **High** | Real values from the owner's private list were published in the repository: one real account number, four cheque numbers and four bank routing codes in `backend/utils/test_field_extraction_pipeline_patched.py` (on `main`, first commit `d509c14`, and its copy under `legacy/` on the branch); and, found by the deeper scan of 2026-11-01 (finding 13 and the method below), four cheques' file names, dates, amounts and one payee name typed into `legacy/backend/utils/confirmed_labels.py`, dataset file names (= cheque numbers) in `reports/final/OCR_DATASET_AUDIT.json`, routing and base numbers in the MICR test fixtures, and amounts in words in tests and a test log. The Docker image does not contain `legacy/` (`.dockerignore`). | **Contained in the current files; the history is still OPEN (owner decision).** Every one of these values was replaced by an invented one in the commit "Contain exposed cheque data" (tests pass; the re-ordered MICR model reads all 120 dataset cheques exactly as before). The values remain in older commits of `main` and of the branch, and anything already cloned keeps them: removing them from history needs a force-push of `main` and the branch, and GitHub Support for cached pull-request copies. Not done without the owner's approval. Treat the numbers as disclosed. |
| 2 | **High** (by the audit's own threshold) | `npm audit --audit-level=high` reports one package at high level: the build tool **Vite <= 6.4.3** (a direct dev dependency; this project uses 5.4.x). The advisories are about Vite's **development server**: path traversal in optimized-dependency `.map` handling, cross-origin script execution through custom middleware, files outside `server.fs.allow` served when project paths match system paths, a `server.fs.deny` bypass on Windows alternate paths, and several `launch-editor` issues on Windows (the helper behind "open in editor"). npm's suggested fix is Vite 8.3.5, a major version. No other package is reported at high or critical level, including `react` and `react-dom`, the only packages inside the page. Vite is not in the Docker image: the build stage compiles the page and only the compiled files are copied on. | **OPEN.** Exposure: a developer's machine while `npm run dev` is running; not the deployed application. Fix, which needs a machine that can run npm: upgrade Vite and `@vitejs/plugin-react` to a patched version, run `npm run build`, commit `package-lock.json`. Not done here because a major upgrade of the build tool could not be tested offline. CI now gates the packages that ship (`npm audit --omit=dev`) and reports the build-tool advisories as warnings on every run. |
| 3 | Medium | Waitress 3 removed `X-Forwarded-For` / `-Proto` before the application saw them. Behind Render's proxy every visitor would have shared one address (one sign-in throttle for all users, wrong audit-log addresses) and HSTS would not be sent. | **FIXED** (`clear_untrusted_proxy_headers=False`; `CV_PROXY_HOPS` stays the only trust setting). Pinned by a test; verified in a real container on GitHub: the step "Behind one reverse proxy" passed on commits `93c2f0c`, `1215ebc` and `23df57e` (`DEPLOYMENT_VERIFICATION.md`). |
| 4 | Medium | The SIGTERM handler could fail to start its watchdog (a signal during `print` raised "reentrant call"): a stuck request could keep the process alive until the platform killed it (availability, and a risk of cutting a write short). | **FIXED**; new test fails on the old code; 60 of 60 repeated runs under CPU load exit in about 1 s (about 1 in 3 hung before). |
| 5 | Medium | Real account numbers and payee names were in test files of a local, unpushed commit. | **FIXED before publishing**: the pushed history has that commit squashed with the commit that replaced them by synthetic values. |
| 6 | Medium | Memory exhaustion: four simultaneous 25-megapixel uploads peaked at 0.7-1.1 GB, enough for an out-of-memory kill on a 512 MB instance. | **FIXED**: header-based pixel limit (12 MP in the Docker image and on Render), two images at a time, `503 busy` for the rest, glibc malloc settings. Eight simultaneous maximum-size uploads in a 512 MB container: no out-of-memory kill (GitHub container run); peak 250-395 MB in local rehearsals. |
| 7 | Low | Frontend builds are not reproducible: no `package-lock.json`, and the Dockerfile runs `npm install`, so each build resolves the newest versions inside the ranges. | OPEN (same fix as #2). |
| 8 | Low | Lower-sensitivity values of the owner's list (a bank routing code and a base number in three OpenCV test fixtures, amounts in words in two test files and in a test log, file names in the dataset audit report). | **Replaced by invented values in the current files** (same commit as finding 1); still in older commits. |
| 9 | Informational | `pip-audit -r backend/requirements.txt` found no known vulnerabilities (GitHub, 2026-11-01). | - |
| 10 | Informational | The pre-push audit reports warnings for synthetic e-mail addresses (`first.last@...`, `someone@gmail.com` as a rejected-domain test), made-up IFSC-like and long numbers in tests, and two header-only CSV templates. No personal address of the owner is in tracked files. | Reviewed. |
| 11 | Informational | Open design limits: tokens in `sessionStorage` (no HttpOnly cookie), no MFA or SSO, no encryption at rest, one SQLite file and one process, e-mail sign-in proves mailbox control only, SMTP delivery untested, no external penetration test. | Documented in `docs/SECURITY.md`. |
| 12 | Low | New endpoint `POST /api/cheques/from-sample` reads image files from the server's dataset folder (demo samples). Controls: administrator or operator only; the image is named by split, bank and file name and must appear in the folder listing, never by a client path; names allow only letters, digits and `_ . -`; the resolved path must lie inside the dataset folder (a link leading out is refused); same size, format, pixel limit, rate limit and image slots as an upload; the audit log records split and bank, not the file name. | Tested: 7 tests, two of the protections mutation-checked (removing them makes a test fail). |
| 13 | **High** | The trained MICR model in the repository (`backend/models_store/micr_e13b.npz`) stored its digit labels in training order. Templates are learned cheque by cheque from the first six MICR digits, which are the cheque number and, in this dataset, the file name, so the label list spelled out the 80 training cheques' numbers one after the other (all 80 matched the owner's list of file names). A plain-text scan cannot see this: the file is a compressed binary. | **FIXED in the current files**: the templates are stored in a fixed pseudo-random order (`MicrModel.mixed`), which does not change what is matched; all 120 dataset cheques are read identically before and after (0 differences), and `test_the_model_in_the_repository_is_not_stored_in_training_order` guards it. The older commits still hold the ordered file (finding 1). |
| 14 | Medium | The training and report code wrote dataset file names into its output: the MICR training metadata (skipped files), the MICR evaluation reports (misread files and the digits read), and the OCR and signature dataset audits (duplicate, near-duplicate, missing, corrupt and unlisted images). File names here are cheque numbers and account numbers, so running the tools and committing their reports would have published them again. | **FIXED**: reports refer to an image by its position (`train/icici/#7`, `genuine #12`), metadata carries counts only, evaluation errors carry the number of wrong digits only. New tests in `backend/tests/test_privacy_of_reports.py`. |
| 15 | Informational | GitHub side of the exposure (checked 2026-11-01 with the repository's own API): public; 0 forks, 0 stars, 0 watchers; no issues (the only open item is pull request 1), no releases, no tags, no wiki content, no Pages; the 8 stored CI artifacts are package-lock files only. The traffic counters show 68 clones by 24 distinct clients in the last 14 days; every CI job checks the repository out, so most of these are the project's own CI, but outside copies cannot be ruled out. | Considered when treating the values as disclosed. |

**Controls verified since the first review** (each has automated tests; the container ones also ran on GitHub): signed bearer tokens with
server-side revocation; three roles on every endpoint; persisted sign-in throttle per (address, user) without locking accounts;
per-user rate limits; password policy (12 characters on Render, checked for the first administrator at start-up); strict
Content-Security-Policy and other security headers, HSTS only over HTTPS; upload validation by content, a pixel limit read from the
header before decoding, random storage names; private files (mode 600) and a non-root server (uid 10001, checked in a real
container); crash-safe writes and start-up recovery; backups that exclude the secret key; e-mail one-time codes stored only as
keyed hashes with a cap on guesses and approval by an administrator for new accounts; `scripts/prepush_audit.py` against secrets,
datasets, images, databases and (locally) the owner's private values.

**How the exposure was investigated (2026-11-01).** The owner's list of 323 private values (file names, account numbers, payee names,
amounts in words, cheque numbers, routing codes) was extended by reading every one of the 120 OCR-dataset images with the project's own
pipeline, which added 195 more (payee names, amounts in words, MICR segments, long numbers); both lists stay outside the repository. They
were searched with tolerant matching (spaces, hyphens and colons inside digit strings, no word boundaries) in every file of all 30
revisions of `main` and the branch (439 distinct files), in every commit message and file path (0 hits), and in the binary files:
there are six in the whole history (five web fonts and the MICR model), none is identical to any of the 480 dataset images (SHA-256), and
the model is finding 13. No cheque image, signature image, database, secret or password is, or ever was, tracked. This report prints
no private value: it names files, labels and counts only.

**Recommendation.** The remaining High finding is the history (#1) and the build-time tooling (#2). They do not stop a pilot deployment
that holds no real cheque data. #1 needs the owner's decision (purge the history, make the repository private, or accept the
disclosure) and #2 should be closed **before real cheque data is loaded or the repository is shared more widely**. This system is not
validated for banking decisions: see the signature and OCR limits in `DATASET_TEST_RESULTS.md`.
