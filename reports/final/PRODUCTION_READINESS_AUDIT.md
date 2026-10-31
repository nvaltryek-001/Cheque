# Production readiness audit – branch `chequeverify-v2` @ `9ff5eb4`

Date: 2026-10-31. Scope: audit only. No application code was changed, `main` was not touched, nothing
was merged or deployed. All numbers below come from tests run during this audit; anything not tested is
marked **UNVERIFIED**.

Environment: Linux, Python 3.13, OpenCV **5.0 (contrib)**, Tesseract 5.3.4, Chromium/Playwright.
GitHub Actions ran the same commit on Python 3.12 with OpenCV **4.x** (the version pinned for deployment).
Accuracy figures were measured with OpenCV 5.0 locally; the 4.x build has **not** been accuracy-tested.

---

## 1. Frontend functionality
- **Implementation**: React 18 + TypeScript, 12 screens + login, cheque detail, signer detail (`Frontend/src`).
- **Tests performed**
  1. GitHub Actions `frontend` job: `npm install`, `tsc --noEmit`, `vite build` with the real React types.
  2. Playwright main script (19 checks) against `run.py --prod` on a fresh database.
  3. Playwright extra script: batch upload, upload-mode verification, add + deactivate specimen, create user,
     threshold setting, CSV download, auditor read-only view (10 checks).
- **Results**: CI frontend job ✅. Main script 19/19 ✅. Extra script 10/10 ✅ (first attempt had 3 failures
  caused by the test script itself – wrong wait selector, an already-enrolled image correctly refused as a
  duplicate, a stale route after sign-out – all re-checked and passing).
- **Evidence**: CI run 41216550271 (frontend: success); Playwright output logs kept with the audit workspace.
- **Remaining problems**
  - After signing out and in again, the app stays on the last page instead of the dashboard (minor).
  - The UI build tested in the browser here was produced with esbuild; the Vite build was proven only to
    compile in CI, not clicked through in a browser. **UNVERIFIED**: Vite bundle in a browser.
  - No automated frontend tests are in the repository (the Playwright scripts are audit-only).
- **Files**: `Frontend/src/App.tsx` (reset route on login), new `Frontend/tests/*` + CI job for browser tests.

## 2. Backend API functionality
- **Implementation**: Flask app factory, 5 blueprints, 60+ routes (`backend/app/api`).
- **Tests**: `pytest -v` with both datasets; CI `backend` job without datasets; live API workflow script (§6).
- **Results**: 54/54 passed locally with datasets; CI 48 passed / 6 skipped (datasets absent) ✅.
- **Remaining problems**: see §8 (PDF crash on markup in corrected fields).
- **Files**: `backend/app/services/reports.py`.

## 3. OCR extraction accuracy – all 10 fields
- **Implementation**: Tesseract 5 + region isolation (`backend/app/ocr/fields.py`, `regions.py`), E-13B MICR
  template model (`micr.py`).
- **Test performed**: the 20 held-out **test** cheques were transcribed by hand (by Claude, from the images,
  before looking at any prediction) and every field compared to the app's output.
  ⚠ These labels have **not been checked by a human**; two cheques contain printed oddities that were labelled
  exactly as printed. Labels are kept outside the public repository.
- **Results** (20 cheques, exact match after case/spacing normalisation):

| Field | Exact | Mean CER | Notes |
|---|---|---|---|
| Bank name | 20/20 | 0.000 | |
| Branch name | **6/20** | 0.355 | Canara: empty or wrong text (5/5 wrong); Syndicate: bank logo text prepended; Axis: trailing OCR junk |
| IFSC | 19/20 | 0.005 | 1 cheque prints an invalid IFSC; the app "corrected" it and marked it ok |
| Cheque number | 20/20 | 0.000 | MICR model |
| MICR code | 20/20 | 0.000 | MICR model |
| Account number | 18/20 | 0.100 | 2 Syndicate cheques: not extracted |
| Date | 18/20 | 0.065 | 2 ICICI: one wrong date, one missing |
| Payee name | **14/20** | 0.048 | Last letters cut off on 5 cheques; 1 mis-read |
| Amount (figures) | 17/20 | 0.150 | 3 truncated amounts (all flagged by the words-vs-figures check) |
| Amount (words) | 19/20 text, 20/20 value | 0.002 (WER 0.013) | |

  Overall: **171/200 fields (85.5 %)**, **3/20 cheques with all 10 fields correct**.
  Review flags: 23 of the 29 wrong fields were flagged `review`/`missing`; **6 wrong fields were marked `ok`**
  (5 truncated payee names, 1 IFSC) – the confidence/status signal is not reliable for these fields.
- **Remaining problems**
  - Payee truncation: `regions.text_rows` sets the row's right edge from tall glyphs only, so trailing
    lowercase letters are cut; Tesseract's word confidence stays high, so the field is wrongly marked ok.
  - Branch extraction is a heuristic and fails on Canara/Syndicate layouts.
  - Measured on only 20 typed cheques with templates also seen in training; not representative of real or
    handwritten cheques. PaddleOCR path **UNVERIFIED**. OpenCV 4.x build **UNVERIFIED**.
  - Previous reports' "fill rate 100 %" for payee/amounts measured presence, not correctness.
- **Files**: `backend/app/ocr/regions.py` (row extent), `backend/app/ocr/fields.py` (payee confidence,
  branch per-bank rules, IFSC "correction" should be flagged review), `reports/final/MODEL_EVALUATION.md`.

## 4. Signature verification with the authorised dataset
- **Implementation**: preprocessing + 8 similarity features + logistic fusion; thresholds from validation.
- **Tests performed** (live API, 120 signers imported from `genuine_signatures.csv`):
  a) each test cheque's signature crop vs the signer suggested by its account number;
  b) each test cheque vs a random **other** signer of the same bank;
  c) each synthetic forged image (`forged_signatures/`) uploaded **as is** vs its target signer.
- **Results**
  - a) 18 cheques with a suggested signer: MATCH 10, REVIEW 6, **MISMATCH 2** (scores 0.432–0.951). The
    specimens were cropped from these very scans, so this is not an independent genuine test.
  - b) other signer: **MISMATCH 18/18** (scores 0.063–0.415) ✅
  - c) synthetic forgeries: **MATCH 36/36** (scores 0.854–0.998) ❌
- **Interpretation**: the synthetic forgeries are the enrolled image with a few strokes added/erased. The
  comparator measures global similarity and does not detect such local edits. The earlier model evaluation
  re-captured (rotated/blurred) every query, which hid this; when the forged file is submitted directly, it is
  accepted every time. No real genuine-vs-genuine or skilled-forgery data exists to evaluate further.
- **Files**: `backend/app/signature/compare.py` (add local-difference / tamper features), `training.py`
  (also evaluate un-recaptured forgeries), `reports/final/MODEL_EVALUATION.md` and `README.md` (correct the
  claims).

## 5. Dataset-dependent tests (the 6 skipped in CI)
- **Tests**: `test_cheque_ocr_crud_and_report`, `test_batch_processing`, `test_dataset_audits_and_readiness`,
  `test_dataset_import_enrollment`, `test_micr_training_job_sync`,
  `test_verify_from_cheque_with_form_fields_only`.
- **Result**: all 6 **passed** locally with the real datasets (54/54 total). They still skip in CI because the
  datasets are private.
- **Remaining problem**: no CI evidence for them. Options: a private repository/runner with the datasets, or
  a small synthetic fixture set committed to the repo.
- **Files**: `.github/workflows/ci.yml`, `backend/tests/conftest.py`.

## 6. Upload → OCR → verification → storage → history workflow
- **Test performed**: live server (`run.py --prod`), operator account, all 20 test cheques uploaded through
  `POST /api/cheques`, signature verified from the stored cheque crop, manual review saved, history, dashboard,
  CSV and PDF fetched.
- **Results**: 20/20 uploads 201; signature crop found 20/20; signer suggested from OCR'd account number
  18/20 (2 failed because the account number was not extracted); 72 verifications stored and listed in history;
  review persisted; dashboard counts matched (72); verifications CSV 72 rows; PDF 200 OK. Mean upload +
  processing time 1.44 s per cheque.
- **Remaining problems**: 2 cheques need manual signer selection; workflow quality limited by §3 and §4.

## 7. Database persistence and file storage
- **Test performed**: recorded counts and files, stopped the server, restarted it on the same instance folder
  without `CV_ADMIN_PASSWORD`, re-checked.
- **Results**: cheques 20, signers 120, verifications 72, audit entries 101 – identical after restart; an
  existing session token stayed valid (secret key persisted); admin was not re-created; cheque image, signature
  crop, query image and specimen image all served after restart; a manual review decision persisted ✅.
- **Remaining problems**
  - Database file permissions 644 and storage directories 755 (readable by other local users).
  - In Docker only `/data/instance` is persistent. Models (`backend/models_store`) and generated reports live
    inside the image: retraining from the UI is lost on every redeploy.
  - No backup/restore procedure; SQLite only (single instance).
- **Files**: `backend/app/db.py` / `services/storage.py` (umask 077), `Dockerfile` + `backend/app/config.py`
  (`CV_MODELS_DIR=/data/models`, `CV_REPORTS_DIR=/data/reports`, seed bundled models on first start).

## 8. Authentication, authorisation, security
- **Tests performed** (live server): unauthenticated access to 7 endpoints; operator attempts on 5 admin
  actions; modified and foreign-key tokens; SQL injection in search; path traversal on 3 routes; random bytes
  and SVG uploads; reportlab markup in a corrected field; CORS from a foreign origin; response headers;
  account lockout; file permissions; client IP behind a proxy.
- **Results**
  - ✅ 401 on all unauthenticated requests; 403 on all operator escalation attempts; 401 on tampered tokens;
    SQL injection returned an empty, valid result; traversal attempts returned 400/404 or the SPA index;
    random/SVG uploads rejected (415); no CORS header for foreign origins; nosniff / X-Frame-Options /
    Referrer-Policy present.
  - ❌ **PDF report returns 500** after a field is corrected to text containing `<b>`/`<font>` – unescaped user
    input is parsed as ReportLab markup (report generation denial of service for that cheque).
  - ❌ **Admin lockout by anyone**: 5 wrong passwords from any client lock the admin for 5 minutes (429).
  - ❌ No Content-Security-Policy, no HSTS.
  - ❌ Audit log records `request.remote_addr`; behind Render's proxy every entry will show the proxy IP
    (no ProxyFix).
  - ⚠ Tokens in `sessionStorage`; no MFA/SSO; no encryption at rest; no dependency vulnerability scan was
    possible offline. **UNVERIFIED**: penetration test.
- **Files**: `backend/app/services/reports.py` (escape values with `xml.sax.saxutils.escape`),
  `backend/app/services/auth.py` (per-IP throttling instead of account lock), `backend/app/__init__.py`
  (ProxyFix, CSP, HSTS), storage permissions as in §7.

## 9. Docker production configuration
- **Implementation**: multi-stage `Dockerfile` (Node 20 build → Python 3.12-slim + Tesseract), non-root user,
  Waitress, health check.
- **Test performed**: GitHub Actions `docker` job – image build, container start, `/api/health` and `/` checked.
  No Docker daemon is available in the audit environment, so nothing beyond the CI smoke test was run.
- **Result**: CI docker job ✅ (run 41216550271).
- **Remaining problems / UNVERIFIED**
  - Inside the container: login, upload, OCR, MICR, signature, PDF – **UNVERIFIED**.
  - OpenCV 4.x accuracy vs the measured 5.0 numbers – **UNVERIFIED**.
  - Models/reports not on the volume (see §7). Datasets not in the image (by design).
- **Files**: `Dockerfile`, `.github/workflows/ci.yml` (extend smoke test: login, upload a synthetic cheque,
  verify a signature).

## 10. Render deployment readiness
- **Implementation**: `render.yaml` (Docker runtime, starter plan, 1 GB disk at `/data`, generated secret key,
  admin password entered in dashboard).
- **Test performed**: configuration review only. **No deployment was made** (as instructed) – nothing on Render
  is verified.
- **Remaining problems**
  - The container runs as uid 10001; whether the Render disk mounted at `/data` is writable by that user is
    **UNVERIFIED** – if not, the app fails at start (cannot create `/data/instance`).
  - Persistent disk requires a paid plan; on the free plan all data is lost on restart.
  - Proxy IP issue (§8). Datasets unavailable on the hosted copy.
- **Files**: `Dockerfile` (entrypoint that prepares `/data` or documented root fallback), `render.yaml`,
  `docs/DEPLOYMENT.md`.

---

# PRODUCTION READINESS REPORT

## Completed (verified in this audit)
- Backend API: 54/54 tests with datasets; CI backend, frontend and Docker jobs green.
- Frontend: 29 browser checks across all screens and roles.
- End-to-end workflow: 20 real test cheques through upload → OCR → signature → review → history → CSV/PDF.
- Persistence across restart (records, files, sessions, reviews).
- Authentication, role enforcement, token integrity, upload validation, path-traversal and SQL-injection resistance.
- Cheque number, MICR code, bank name: 20/20 on the test cheques; amount-in-words value 20/20.

## Partially completed
- OCR: 171/200 fields correct on 20 typed test cheques (labels not yet human-verified); branch 6/20, payee 14/20.
- Signature: rejects other people's signatures (18/18), but see blockers.
- Security: good access control; missing hardening (CSP, HSTS, proxy IPs, lockout design, file permissions).
- Docker: builds and starts in CI; functionality inside the container untested.

## Not completed
- Human verification of the OCR test labels.
- Evaluation on real handwritten cheques, real genuine-vs-genuine signatures and skilled forgeries.
- Dataset-dependent tests in CI.
- Render deployment and any live-environment test.
- PaddleOCR path, OpenCV 4.x accuracy, backups, monitoring.

## Critical blockers
1. **Synthetic forged signatures are accepted (36/36 MATCH)** when submitted directly. Signature
   verification must not be relied on until tamper/local-difference detection is added and re-evaluated.
2. **Wrong OCR values marked "ok"** (6 of 29 errors, mostly truncated payee names) – reviewers would not be
   prompted to check them.
3. **PDF report crash** on markup in corrected fields (500).
4. **Render disk permission for the non-root container user is unverified**; the deployment may fail to start,
   and models/reports are not persisted.
5. Accuracy numbers were measured with OpenCV 5.0; the deployed image uses 4.x – must be re-measured.

## Recommended next actions (in order)
1. Fix blockers 2 and 3 (`regions.py`, `fields.py`, `reports.py`) – small, targeted changes with tests.
2. Add a tamper-sensitive signature feature and an evaluation that includes un-recaptured forgeries
   (`compare.py`, `training.py`); update `MODEL_EVALUATION.md` and `README.md` claims.
3. Docker: persist models/reports under `/data`, handle `/data` ownership, extend the CI smoke test to a full
   login → upload → verify flow; re-run the OCR scoring script inside the image (OpenCV 4.x).
4. Security hardening: ProxyFix, CSP/HSTS, per-IP login throttling, restrictive file permissions.
5. Have a person verify the 20 test labels (and label more cheques), then re-run the field scoring.
6. Only then merge PR #1 and deploy to Render; repeat §6–§8 against the live URL.

**Recommendation: do not merge or deploy yet.** Merge after blockers 1–5 are resolved and re-tested.
