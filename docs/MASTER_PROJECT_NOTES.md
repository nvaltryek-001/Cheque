# ChequeVerify AI – master project notes

Living document. Update whenever architecture, dependencies, APIs, environment variables, database
schema, model behaviour or deployment configuration change.
Status values: NOT STARTED · IN PROGRESS · BLOCKED · TESTING · COMPLETED (completed = implemented **and** verified).

Last updated: 2026-11-01 (evening) · branch `chequeverify-v2` pushed to GitHub at `93c2f0c` at the owner's request (fast-forward, no force) ·
PR #1 (open, not merged) · `main` untouched · the Render service is created by the owner (see §10).

## 1. What the application does
Web application for Indian CTS-2010 cheques: upload a cheque → OCR of 10 fields with per-field
confidence/status and validation → signature crop → comparison against enrolled specimens →
MATCH / REVIEW / MISMATCH with reasons → manual review → history, CSV/PDF reports, audit log.
Decision support only; a person confirms every result.

## 2. Technology stack
| Layer | Technology |
|---|---|
| Frontend | React 18 + TypeScript + Vite (`Frontend/`), no UI library, hash router; one stylesheet / design system, Inter fonts self-hosted (SIL OFL) |
| Sign-in | Password (administrator sign-in) or one-time code mailed to an `@saividya.ac.in` address (`docs/AUTHENTICATION.md`) |
| Backend | Python 3.12 + Flask 3, Waitress in production (`backend/app`) |
| Database | SQLite (stdlib `sqlite3`), schema in `backend/app/db.py` |
| OCR | Tesseract 5 (PaddleOCR 3.x adapter, unverified) + trained E-13B MICR template model |
| Signature | OpenCV preprocessing, 8 similarity features, logistic fusion, alignment-based alteration check |
| Deployment | Docker (single container), Render blueprint, GitHub Actions CI |
| OpenCV | `opencv-python-headless>=4.8,<5` (5.x wheels lack `HOGDescriptor`) |

## 3. Folder structure
`backend/app/{api,ocr,signature,services}` · `backend/tests` · `backend/models_store` (bundled models) ·
`backend/manage.py` (CLI) · `backend/docker_entrypoint.py` · `Frontend/src/{pages,components}` · `Frontend/public/fonts` + `Frontend/scripts/subset_fonts.py` ·
`docs/` · `reports/final/` · `legacy/` (v1, unchanged) · `dataset/`, `signature/` (placeholders; private data is never committed).

## 4. Running
Windows: `.\start.ps1 -Setup`, `.\start.ps1 -Test`, `.\start.ps1` (prod, :5000), `.\start.ps1 -Dev`.
Linux/macOS: `./start.sh setup|test|dev`. Docker: `docker build -t chequeverify .` then
`docker run -p 8000:8000 -e CV_ADMIN_PASSWORD=… -v cv-data:/data chequeverify`. Details: `README.md`, `docs/DEPLOYMENT.md`.

## 5. Environment variables
| Variable | Default | Purpose |
|---|---|---|
| `CV_SECRET_KEY` | random, persisted in `instance/secret_key` | token signing |
| `CV_ADMIN_PASSWORD` | random, printed once | first admin |
| `CV_INSTANCE_DIR` | `backend/instance` (Docker: `/data/instance`) | DB + uploads + specimens |
| `CV_MODELS_DIR` | `backend/models_store` (Docker: `/data/models`, seeded on first start) | trained models |
| `CV_REPORTS_DIR` | `reports` (Docker: `/data/reports`, seeded) | audit/evaluation reports |
| `CV_OCR_DATASET`, `CV_SIGNATURE_DATASET` | `dataset`, `signature` | private datasets (read-only) |
| `CV_OCR_ENGINE` | `auto` | `auto`, `paddle`, `tesseract`, `none` |
| `CV_PROXY_HOPS` | `0` (Render: `1`) | trusted reverse proxies for client IP |
| `CV_MAX_UPLOAD_MB`, `CV_TOKEN_MAX_AGE`, `CV_CORS_ORIGINS` | 10, 28800, localhost:5173 (Docker image: empty) | limits |
| `CV_LOGIN_MAX_ATTEMPTS`, `CV_LOGIN_IP_MAX_ATTEMPTS`, `CV_LOGIN_LOCK_SECONDS` | 5, 20, 300 | sign-in throttle per (client IP, username), per client IP, and the window |
| `CV_MIN_PASSWORD_LENGTH` | 8 (Render: 12) | password policy; also checked for `CV_ADMIN_PASSWORD` at start-up |
| `CV_RATE_LIMIT_PER_MIN` | 120 | expensive calls per user per minute (0 = off) |
| `CV_MAX_IMAGE_PIXELS` | 25000000 | decoded-size limit, checked from the header before decoding |
| `CV_HSTS_MAX_AGE` | 31536000 | HSTS, sent only on HTTPS (0 = never) |
| `CV_EMAIL_DELIVERY` | empty = email sign-in off | `smtp` (real mail), `log` (development only), `memory` (tests) |
| `CV_SMTP_HOST`, `CV_SMTP_PORT`, `CV_SMTP_SECURITY`, `CV_SMTP_USER`, `CV_SMTP_FROM` | -, 587, `starttls`, -, - | mail server for the codes |
| `CV_SMTP_PASSWORD` | - | **secret**, environment only, read when a mail is sent |
| `CV_EMAIL_DOMAINS` | `saividya.ac.in` | exact domains that may sign in by email (no list of people) |
| `CV_EMAIL_DEFAULT_ROLE` | `pending` | role of a first email sign-in: `pending` (waits for approval), `auditor`, `operator`; never `admin` |
| `CV_ADMIN_EMAILS` | empty | addresses that become administrators after proving the mailbox (not an allow-list) |
| `CV_PASSWORD_LOGIN` | `1` | `0` switches username + password sign-in off |
| `CV_EMAIL_CODE_TTL`, `CV_EMAIL_CODE_ATTEMPTS`, `CV_EMAIL_RESEND_SECONDS`, `CV_EMAIL_REQUESTS_PER_ADDRESS`, `CV_EMAIL_REQUESTS_PER_IP`, `CV_EMAIL_REQUEST_WINDOW` | 600, 5, 30, 5, 20, 900 | code lifetime, wrong tries, resend gap, request limits |

## 6. API and database
API reference: `docs/API.md` (≈60 routes, JSON envelope, roles admin/operator/auditor).
Tables: `users`, `cheques` (machine fields + human corrections kept separately), `signers`, `specimens`
(+ `session_id`, `source_ref`, `origin`), `enrollment_sessions` (signing day, method, instrument, consent reference),
`verifications`, `model_runs`, `settings`, `audit_log`, `login_attempts` (sign-in throttle), `login_codes` (email sign-in codes, keyed hashes only);
`users` gained `email` (unique, case-insensitive) and `pending_approval` (`backend/app/db.py`).
Older databases are upgraded in place by an additive, idempotent migration at start-up (`db.migrate`).

## 7. AI/ML workflow (current behaviour)
- OCR: page normalised to 1600 px; header read at 2× (+ sparse pass for the branch); field regions
  isolated from labels/lines; rows grow over lower-case letters; amounts cross-checked; IFSC confidence
  from OCR, flagged when characters were corrected or the prefix does not match the bank.
- MICR: E-13B 1-NN templates learned from the train split (labels = filename cheque number).
- Signature: score = fusion of ORB, SIFT, SSIM, HOG, profiles, chamfer, density, aspect (thresholds from
  validation). Then an alteration check aligns the query to each specimen: an edited near-copy → MISMATCH,
  a near pixel-identical copy → REVIEW. Reference-set policy (Phase 3): a MATCH is only issued when the signer
  has ≥ 5 independent specimens (recorded signing sessions, same-source images count once) from ≥ 2 different
  signing days; otherwise it is lowered to REVIEW (`details.capped_by_policy`). MISMATCH is never softened.

### Measured results (details: `reports/final/MODEL_EVALUATION.md`)
| Measure | Result |
|---|---|
| OCR, 20 test cheques, hand-transcribed by Claude (not yet human-verified; 1 label corrected 2026-10-31) | 183/200 fields; 8/20 cheques fully correct; wrong values marked "ok": 2 of 17 |
| Genuine signature acceptance / false rejection | **not measurable** – the only genuine image per signer is cropped from that signer's cheque (`DATASET_VALIDATION_REPORT.md`) |
| Random impostor (other signer's cheque signature), test | accepted 0/380 |
| Cheque number / MICR code / bank name | 20/20 each |
| Edited copy (script-made edit of the specimen; **not** a forgery by another person) submitted as-is (test signers) | accepted 2/40, rejected 21/40, review 17/40 |
| Skilled-forgery acceptance / rejection | **not measurable** – no forgery by another person exists in the data (`SIGNATURE_EVALUATION_STATUS.md`) |
| Other-signer signature (test) | accepted 0/60 |
| MATCH on any negative (test) | 1.4 % |
| Live API: edited copy vs target | 1/36 MATCH (was 36/36) |

## 8. Task checklist
| # | Task | Status | Evidence / next step |
|---|---|---|---|
| 1 | OpenCV `HOGDescriptor` compatibility | TESTING | Pinned `<5` (CI green on 4.x). Golden-value test added; must pass in CI after the next push |
| 2 | PDF crash on markup in corrections | COMPLETED | escaped in `reports.py`; regression test fails before / passes after |
| 3 | OCR payee truncation + wrong "ok" statuses | COMPLETED (partially improves accuracy) | test 171→182/200, wrong-ok 6→2; 2 ICICI payee mis-reads remain |
| 4 | Edited copies (script-made edits of a specimen) accepted | COMPLETED | 36/36 → 1/36 MATCH in the live app; ~10 % of the edits have no visible change. Earlier notes and reports call these "synthetic forgeries"; they are not forgeries by another person |
| 5 | Models/reports persistence + root-owned disk | COMPLETED (Docker); Render disk unverified | Verified in a real container on GitHub (2026-11-01): non-root server, private database, restart, redeploy onto the same disk, SIGKILL + restart with integrity check, clean `docker stop`. Local rehearsal 43/43. The Render disk itself is documentation-only until deployed |
| 6 | Real client IP behind proxy | COMPLETED | ProxyFix via `CV_PROXY_HOPS`; test added |
| 7 | Private file permissions | COMPLETED | umask 077 → 600 files (verified) |
| 8 | Admin lockout by anyone (5 wrong passwords) | COMPLETED | Phase 2: persisted throttle per (IP, username) + per IP, no account lock, uniform answers for unknown users, `Retry-After`, `manage.py unlock-logins`; `tests/test_security.py` |
| 9 | CSP / HSTS headers | COMPLETED | Phase 2: strict CSP (no inline/eval), HSTS on HTTPS only, Permissions-Policy, COOP/CORP, neutral `Server`. Chromium check (`tests/browser_check.py`, 30 checks) on an esbuild bundle of the sources; HSTS behind a proxy tested with a simulated proxy, real-container check runs in CI |
| 10 | Canara branch, ICICI dates, Syndicate account extraction; payee marked ok | TESTING | Phase 1 (2026-10-31): fixed on val/train/test; test 196/200, 0 wrong-but-ok (test set not blind – see §9). Needs fresh human-verified cheques |
| 11 | Human verification of the 40 OCR labels | BLOCKED | needs a person; labels delivered privately |
| 12 | Real genuine/skilled-forgery signature data | BLOCKED | needs data collection; requirements in `docs/SIGNATURE_EVALUATION_PROTOCOL.md` §4 |
| 13 | Dataset tests in CI | BLOCKED | datasets are private; needs private runner or synthetic fixtures |
| 15 | Dataset validation (OCR test set + signature dataset) | COMPLETED | `reports/final/DATASET_VALIDATION_REPORT.md`; awaiting owner review before push |
| 14 | Render deployment | IN PROGRESS (owner's step) | Code pushed 2026-11-01 at the owner's request. The service must be created by the owner: Render → New → Blueprint → branch `chequeverify-v2` (paid Starter plan + disk; `CV_ADMIN_PASSWORD` entered in the dashboard). No URL exists until then |
| 16 | Upload memory-exhaustion guard | COMPLETED | Phase 2: pixel limit from the file header before decoding on every upload route; bomb PNG 968 MB → ≈111 MB peak |
| 17 | Per-user rate limits on heavy endpoints | COMPLETED | Phase 2: `services/throttle.py` (`CV_RATE_LIMIT_PER_MIN`), in memory per process |
| 18 | Password policy | COMPLETED | Phase 2: length (12 on Render), mix, common/username, ≠ current, first admin checked at start-up |
| 19 | Pre-push audit (`scripts/prepush_audit.py`) + CI job | COMPLETED | Phase 2: 32 tests; with private terms it found real account numbers/names in an unpushed commit (see §9) |
| 20 | CI hardening: read-only token, SHA-pinned actions, `pip-audit`, `npm audit`, Dependabot | TESTING | Ran on GitHub 2026-11-01 (latest checked commit `1215ebc`): backend tests, frontend (real `vite build`), private-data audit, `pip-audit` and the real-container job pass. `npm audit` is split: the packages that ship (`--omit=dev`) gate the run; the build tools are reported as warnings (task 30) |
| 21 | `package-lock.json` and dependency vulnerability scan | BLOCKED | npm/PyPI registries are blocked in the build sandbox (403); CI `dependency-audit` generates the lock and the audit results |
| 22 | HttpOnly-cookie sessions, MFA/SSO, encryption at rest | NOT STARTED | design decisions for the deployer, see `docs/SECURITY.md` |
| 23 | Genuine-signature collection and enrollment workflow (software) | COMPLETED | Phase 3: signing sessions, independence/readiness rule, same-signing refusal (≥ 98 % aligned ink agreement), batch upload, MATCH cap, `manage.py import-collection`, UI (reference-set panel, sessions, multi-file upload). 88 new tests (`test_enrollment.py` 63, `test_collection.py` 25), live smoke 28/28, Chromium check 41/41 under the CSP. Protocol: `docs/SIGNATURE_ENROLLMENT.md` |
| 24 | Real collection of genuine signatures under that protocol | BLOCKED | needs consenting signers, a collector, an encrypted store; target ≥ 30 signers × ≥ 10 signatures on ≥ 2 days (see §9) |
| 25 | Valid signature evaluation protocol (software + rules) | COMPLETED | Phase 4: `app/signature/protocol.py`, `manage.py eval-signatures`, `docs/SIGNATURE_EVALUATION_PROTOCOL.md`. Signer-level splits, references = thinnest ready set from the earliest days, genuine queries strictly later, skilled forgeries need ≥ 3 per signer by ≥ 2 forgers, synthetic edits excluded from every forgery metric, thresholds from validation, test split only with `--final` + ledger, signer-level bootstrap intervals, "not measurable" instead of numbers. 94 tests (`test_protocol.py`); whole backend suite 346 passed / 7 skipped, live smoke 28/28, Chromium check 41/41 under the CSP; synthetic data only |
| 27 | Passwordless sign-in with an institutional email (`@saividya.ac.in`, no list of people) | TESTING | Backend + UI done: 8-digit mailed code (single use, keyed hash, 10 min, 5 tries, per-address/per-IP limits, shared throttle, no enumeration), accounts created on first use and **awaiting approval** (403 `approval_pending` on every protected route, proven by a route-table walk), `CV_ADMIN_EMAILS` bootstrap, password sign-in kept as "Administrator sign-in" (`CV_PASSWORD_LOGIN`), email accounts cannot get passwords. 94 tests (`test_email_login.py`), mutation-checked; Chromium flow 60/60 under the CSP. **Not verified:** delivery through a real mail provider (sandbox cannot send mail; SMTP path tested against a local fake server). `docs/AUTHENTICATION.md` |
| 28 | Frontend visual refresh (all pages, one design system) | TESTING | New tokens, typography (self-hosted Inter, OFL), buttons/cards/navigation/icons/dashboard, sign-in hero, waiting page; layout and every feature kept. `test_frontend_theme.py` (40) guards CSP compliance, no external resources, contrast, reduced motion. Checked in Chromium on desktop and phone width (screens reviewed by eye); built with esbuild because Vite is unavailable here, CI builds with Vite. No other browser or real screen reader tested |
| 29 | Mail service for email sign-in (choice, credentials, deliverability check) | BLOCKED | needs the owner: SMTP host/user/from/password as environment secrets (never in git or chat), then a real sign-in test; until then only the administrator sign-in is offered |
| 26 | Run the protocol on a real evaluation set | BLOCKED | needs ≥ 30 test signers on ≥ 3 days with forgeries by ≥ 2 other people (task 24 + forgers); until then genuine acceptance and skilled-forgery rejection stay unmeasured |
| 30 | Frontend `npm audit` high/critical finding | OPEN (build tools only) | Vite <= 6.4.3 (we use 5.4.x): five development-server advisories plus `launch-editor` issues on Windows; npm suggests Vite 8.3.5 (major). Vite is not in the image, so the deployed app is not exposed; a developer is while `npm run dev` runs. Fix on a machine with npm access: upgrade Vite and `@vitejs/plugin-react`, run `npm run build`, commit `package-lock.json` (also makes builds reproducible). Details: `reports/final/SECURITY_AUDIT.md` #2 |
| 31 | Real account number / cheque number / MICR code in the original code | OPEN - owner decision | `backend/utils/test_field_extraction_pipeline_patched.py` on `main` and its copy under `legacy/` in the first branch commit (public since 2026-10-31). Removing it from history needs a force-push of `main` and the branch, which is not done without the owner's explicit approval |
| 33 | Demo samples: process a dataset image from the UI | COMPLETED (local); hosted copy has no dataset by design | `POST /api/cheques/from-sample` + *Process with OCR* button in the OCR dataset viewer. 7 tests (identity by split/bank/name, no client path, link leaving the folder refused, roles, size and format refusals, original untouched, duplicate detection); two protections mutation-checked; clicked through in Chromium against the real sample cheques (6/6). Datasets stay outside git and the image |
| 32 | Container checks found two bugs (2026-11-01) | COMPLETED | Waitress 3 stripped `X-Forwarded-*` (all clients looked like the proxy, no HSTS) -> `clear_untrusted_proxy_headers=False`; SIGTERM handler printed before starting its watchdog (about 1 in 3 hangs under load) -> watchdog first, `os.write`. Both pinned by tests that fail on the old code |

## 9. Known bugs / limitations
- OCR (after phase 1, test set, not blind): 4 of 200 fields still wrong, all flagged for review; 44 of 196
  correct fields are still sent to review (conservative); the Canara branch is always sent to review.
- Private values were found by the pre-push audit: (a) real account numbers and payee names in test files of the local
  phase 1 commit `9e1c3f6` (scrubbed in `57dba7d`). They were **not published**: the branch pushed on 2026-11-01 was rebuilt on top of
  GitHub's tip with those two commits squashed (`4fb09f5` in the pushed history; the pre-push audit reports 0 errors for everything
  that was pushed). The unsquashed commits exist only in the sandbox's local branch; (b) already on `main` (your original code,
  untouched): `backend/utils/test_field_extraction_pipeline_patched.py` holds one real account number plus cheque numbers and MICR
  codes, its copy under `legacy/` is in the first pushed branch commit, and `confirmed_labels.py` holds dataset file names (task 31);
  (c) already pushed on `chequeverify-v2`: cheque numbers/file names in `reports/final/OCR_DATASET_AUDIT.json`
  and generic amount-in-words phrases in `test_units.py` (low sensitivity).
- The signature dataset has no independent genuine sample (each specimen is cropped from its signer's only
  cheque) and no skilled forgeries; ~2/3 of specimens appear to be typeset names. Behaviour on real,
  independently signed genuine signatures and on skilled forgeries is unknown.
- 10 % of the synthetic edits ("edited copies") are visually identical to the genuine image (undetectable by design).
- No claim of forgery detection is made or supported: the only negatives with an edit are script-generated edits of the
  specimen itself (reported as edited-copy detection); the 120 specimens have no signing dates, so genuine acceptance /
  false rejection are not measurable either. `eval-signatures --dataset` regenerates this statement from the manifests.
- The copy-and-alter rule was tuned on synthetic edits. On pseudo-signatures written twice by the same "hand" with a few
  pixels of wobble it flagged every re-signing as altered (synthetic test data, not a measurement): its false-rejection rate on
  real re-signed signatures is unknown and will show up as `genuine_mismatch_rate` in the first real evaluation.
- The deployed signature model predates the protocol and does not record which signers it was fitted on, so the
  protocol reports that check as a warning for it; models trained from now on store signer hashes (`meta.fitted_signer_hashes`).
- The committed `reports/final/signature_model_evaluation.json` still has the older `forgery_*` key names; it is regenerated
  by `manage.py train-signature` (planned for the regression run).
- Enrollment policy (Phase 3, **behaviour change**): the cap is on by default, so every signer whose specimens have
  no recorded signing session (all existing enrollments, all dataset imports) gets REVIEW instead of MATCH until
  sessions are recorded or linked. This is deliberate (one specimen cannot support a MATCH) and can be switched off
  in Settings (audited). Dataset-imported signers can never become ready from that data.
- The independence of signing days is attested by whoever records the session; the software refuses byte/perceptual
  duplicates and re-captures that agree ≥ 98 % after alignment, but two separate photocopies of one signing can pass.
  "Ready" (5 specimens / 2 days) is a conservative default, not a value derived from data; a ready set makes a
  MATCH allowed, not reliable.
- `retention.days` is only a setting: there is no automatic deletion of signatures or consent withdrawal workflow
  beyond deleting the signer.
- Waitress is not installable in the build sandbox (falls back to Flask's threaded server there). The container run on GitHub uses it and
  found the proxy-header bug (task 32); behaviour under load on Render is untested.
- After sign-out/in the UI stays on the last page.
- Email sign-in proves control of a mailbox, not identity: anyone who can read a user's mail (compromised, shared or
  forwarded mailbox) can sign in as that user. No MFA beyond the mailbox, no SSO. Real SMTP delivery, spam filtering and
  provider limits are untested. Nothing stops someone from asking many institutional mailboxes for codes (WAF / CAPTCHA).
- With `CV_EMAIL_DEFAULT_ROLE=operator` or `auditor`, everyone with an institutional mailbox sees cheque/signature data at once.
- The visual refresh was not run through Vite, other browsers or an accessibility audit tool; contrast is checked by test and by eye.

## 10. Deployment status
Code is on GitHub (branch `chequeverify-v2`; the newest commit is shown by `git log`). **No Render service has been created by this project's
tooling**, so there is no URL yet; the owner creates it: Render -> New -> Blueprint -> repository `nvaltryek-001/Cheque` -> branch
`chequeverify-v2` -> enter `CV_ADMIN_PASSWORD` (12+ characters, letters and digits/symbols) -> Apply (paid Starter plan + 1 GB disk).
Sign in at the service URL with *Administrator sign-in*, user `admin`. The hosted copy starts empty (datasets are private). Container
checks run on GitHub on every push; results and what is still unverified: `reports/final/DEPLOYMENT_VERIFICATION.md`.

## 11. Change log
| Date | Change |
|---|---|
| 2026-10-31 | v2 rebuild merged into branch `chequeverify-v2`, PR #1 |
| 2026-10-31 | CI fixes: dataset-test skips, OpenCV pin |
| 2026-10-31 | Production readiness audit (`reports/final/PRODUCTION_READINESS_AUDIT.md`) |
| 2026-10-31 | Blocker fixes: OpenCV golden tests, PDF escaping, OCR row extent/IFSC/branch, signature alteration check, Docker /data persistence, ProxyFix, umask |
| 2026-10-31 | Dataset validation report; genuine-signature figures withdrawn as GAR; OCR test label corrected (183/200) |
| 2026-10-31 | Phase 1 OCR: digit-pass account read, 3-rendering payee vote, amount-box re-read, boxed-date families, per-line header reads, IFSC prefix repair, spelling-normalisation flag; `score-ocr` command |
| 2026-11-01 | Phase 2 security: header-based image size limit, persisted sign-in throttle (no account lock), per-user rate limits, password policy, CSP/HSTS/Permissions-Policy/COOP/CORP, CI hardening + Dependabot, `.env` comment parsing fix, pre-push audit tool; real values removed from tests (`57dba7d`) |
| 2026-11-01 | Phase 3 signature enrollment: `enrollment_sessions` table + specimen provenance columns (additive migration), readiness rule and MATCH cap, same-signing refusal, batch upload, session/readiness API, `manage.py import-collection`, UI (reference-set panel, sessions, multi-file upload, readiness column, verification banner, settings), `docs/SIGNATURE_ENROLLMENT.md`, manifest template; default `signature.max_specimens_per_signer` 5 → 20 for new installs |
| 2026-11-01 | Phase 4 signature evaluation protocol: `app/signature/protocol.py`, `manage.py eval-signatures` (`--dataset` status report, `--split val`, `--split test --final`), test-use ledger, suggested thresholds from validation only, claims generated from the report; training report relabelled ("edited copy", `scope` block, `fitted_signer_hashes` in the model); Training page notice; `docs/SIGNATURE_EVALUATION_PROTOCOL.md`, manifest template, `reports/final/SIGNATURE_EVALUATION_STATUS.{md,json}`; "Valid signature evaluation" added to training readiness as BLOCKED |
| 2026-11-01 | Passwordless institutional-email sign-in (`email_login.py`, `mailer.py`, `login_codes`, `users.email`/`pending_approval`, waiting-for-approval gate, approval in Settings → Users and roles, `docs/AUTHENTICATION.md`) and a frontend visual refresh (design system, fonts, sign-in hero, icons, dashboard stat tiles, decision mix, user table, mobile layout; `test_frontend_theme.py`); browser/live smoke tests extended |
| 2026-11-01 | Phase 5 (persistence): atomic writes, start-up recovery, SIGTERM handling, seeding with a manifest, backup/verify/restore, image concurrency + 12 MP limit, deployment rehearsal, persistence checker; `docs/DEPLOYMENT.md` rewritten |
| 2026-11-01 | Demo samples: *Process with OCR* in the OCR dataset viewer (`POST /api/cheques/from-sample`); CI: read-only-container probe uses the container's own port, `npm audit` split into the part that ships (blocking) and the build tools (reported as warnings); `.env.example` and `docs/API.md` updated |
| 2026-11-01 | Pushed to GitHub at the owner's request (clean rebuilt history, audit 0 errors). Container checks on GitHub found and fixed two bugs (Waitress stripped proxy headers; SIGTERM watchdog race). Reports: dataset results, deployment verification, regression, security audit, final completion report |
