# ChequeVerify AI – master project notes

Living document. Update whenever architecture, dependencies, APIs, environment variables, database
schema, model behaviour or deployment configuration change.
Status values: NOT STARTED · IN PROGRESS · BLOCKED · TESTING · COMPLETED (completed = implemented **and** verified).

Last updated: 2026-10-31 · branch `chequeverify-v2` · PR #1 (open, not merged) · nothing deployed.

## 1. What the application does
Web application for Indian CTS-2010 cheques: upload a cheque → OCR of 10 fields with per-field
confidence/status and validation → signature crop → comparison against enrolled specimens →
MATCH / REVIEW / MISMATCH with reasons → manual review → history, CSV/PDF reports, audit log.
Decision support only; a person confirms every result.

## 2. Technology stack
| Layer | Technology |
|---|---|
| Frontend | React 18 + TypeScript + Vite (`Frontend/`), no UI library, hash router |
| Backend | Python 3.12 + Flask 3, Waitress in production (`backend/app`) |
| Database | SQLite (stdlib `sqlite3`), schema in `backend/app/db.py` |
| OCR | Tesseract 5 (PaddleOCR 3.x adapter, unverified) + trained E-13B MICR template model |
| Signature | OpenCV preprocessing, 8 similarity features, logistic fusion, alignment-based alteration check |
| Deployment | Docker (single container), Render blueprint, GitHub Actions CI |
| OpenCV | `opencv-python-headless>=4.8,<5` (5.x wheels lack `HOGDescriptor`) |

## 3. Folder structure
`backend/app/{api,ocr,signature,services}` · `backend/tests` · `backend/models_store` (bundled models) ·
`backend/manage.py` (CLI) · `backend/docker_entrypoint.py` · `Frontend/src/{pages,components}` ·
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
| `CV_MAX_UPLOAD_MB`, `CV_TOKEN_MAX_AGE`, `CV_CORS_ORIGINS`, `CV_LOGIN_MAX_ATTEMPTS`, `CV_LOGIN_LOCK_SECONDS` | 10, 28800, localhost:5173, 5, 300 | limits |

## 6. API and database
API reference: `docs/API.md` (≈60 routes, JSON envelope, roles admin/operator/auditor).
Tables: `users`, `cheques` (machine fields + human corrections kept separately), `signers`, `specimens`,
`verifications`, `model_runs`, `settings`, `audit_log` (`backend/app/db.py`).

## 7. AI/ML workflow (current behaviour)
- OCR: page normalised to 1600 px; header read at 2× (+ sparse pass for the branch); field regions
  isolated from labels/lines; rows grow over lower-case letters; amounts cross-checked; IFSC confidence
  from OCR, flagged when characters were corrected or the prefix does not match the bank.
- MICR: E-13B 1-NN templates learned from the train split (labels = filename cheque number).
- Signature: score = fusion of ORB, SIFT, SSIM, HOG, profiles, chamfer, density, aspect (thresholds from
  validation). Then an alteration check aligns the query to each specimen: an edited near-copy → MISMATCH,
  a near pixel-identical copy → REVIEW.

### Measured results (details: `reports/final/MODEL_EVALUATION.md`)
| Measure | Result |
|---|---|
| OCR, 20 test cheques, hand-transcribed by Claude (not yet human-verified) | 182/200 fields; wrong values marked "ok": 2 of 18 |
| Cheque number / MICR code / bank name | 20/20 each |
| Synthetic forgery submitted as-is (test signers) | accepted 2/40, rejected 21/40, review 17/40 |
| Other-signer signature (test) | accepted 0/60 |
| MATCH on any negative (test) | 1.4 % |
| Live API: synthetic forgery vs target | 1/36 MATCH (was 36/36) |

## 8. Task checklist
| # | Task | Status | Evidence / next step |
|---|---|---|---|
| 1 | OpenCV `HOGDescriptor` compatibility | TESTING | Pinned `<5` (CI green on 4.x). Golden-value test added; must pass in CI after the next push |
| 2 | PDF crash on markup in corrections | COMPLETED | escaped in `reports.py`; regression test fails before / passes after |
| 3 | OCR payee truncation + wrong "ok" statuses | COMPLETED (partially improves accuracy) | test 171→182/200, wrong-ok 6→2; 2 ICICI payee mis-reads remain |
| 4 | Synthetic forgeries accepted | COMPLETED | 36/36 → 1/36 MATCH in the live app; ~10 % of forgeries have no visible edit |
| 5 | Models/reports persistence + root-owned disk | TESTING | entrypoint verified locally (simulated, as root); Docker CI job extended – runs after push |
| 6 | Real client IP behind proxy | COMPLETED | ProxyFix via `CV_PROXY_HOPS`; test added |
| 7 | Private file permissions | COMPLETED | umask 077 → 600 files (verified) |
| 8 | Admin lockout by anyone (5 wrong passwords) | NOT STARTED | per-IP throttling in `services/auth.py` |
| 9 | CSP / HSTS headers | NOT STARTED | `app/__init__.py` |
| 10 | Canara branch, ICICI dates, Syndicate account extraction | NOT STARTED | `ocr/fields.py`, `ocr/regions.py` |
| 11 | Human verification of the 40 OCR labels | BLOCKED | needs a person; labels delivered privately |
| 12 | Real genuine/skilled-forgery signature data | BLOCKED | needs data collection |
| 13 | Dataset tests in CI | BLOCKED | datasets are private; needs private runner or synthetic fixtures |
| 14 | Render deployment | NOT STARTED | needs owner approval and a paid plan for the disk |

## 9. Known bugs / limitations
- 2 of 18 wrong OCR values on the test set are still marked "ok" (ICICI payee mid-word mis-reads).
- Canara branch name not extracted on the test cheques; ICICI boxed dates sometimes missing.
- Signature "genuine" pairs in the dataset are copies of one image, so genuine verification mostly ends
  in REVIEW; behaviour on real, independently signed genuine signatures is unverified.
- 10 % of synthetic forgeries are visually identical to the genuine image (undetectable by design).
- Waitress not tested in the build environment (falls back to Flask's threaded server if missing).
- After sign-out/in the UI stays on the last page.

## 10. Deployment status
Not deployed. Docker image built and smoke-tested in CI (previous commit). New entrypoint and
persistence checks run in CI on the next push. Render requires the owner's approval and a paid plan
for persistent storage (see `docs/DEPLOYMENT.md`).

## 11. Change log
| Date | Change |
|---|---|
| 2026-10-31 | v2 rebuild merged into branch `chequeverify-v2`, PR #1 |
| 2026-10-31 | CI fixes: dataset-test skips, OpenCV pin |
| 2026-10-31 | Production readiness audit (`reports/final/PRODUCTION_READINESS_AUDIT.md`) |
| 2026-10-31 | Blocker fixes: OpenCV golden tests, PDF escaping, OCR row extent/IFSC/branch, signature alteration check, Docker /data persistence, ProxyFix, umask |
