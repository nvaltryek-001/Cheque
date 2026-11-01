# Initial project audit

Date: 2026-10-31. Environment: cloud Linux workspace (Python 3.13, OpenCV 5.0, Flask 3.1, Tesseract 5.3.4,
Node 22, no internet package registry).

## What was available
| Input | Content |
|---|---|
| Project directive (attachment) | Full specification for ChequeVerify AI (15 phases) |
| Previous README (attachment) | Describes the original repo (Flask + React/TS + PaddleOCR, 10-field extraction, signature work "planned") |
| `new_datset.zip` | OCR dataset – 120 cheque images |
| `Signature_dataset.zip` | Signature dataset – manifests, 360 images, two generator scripts |

## What was not available
The original source code at `N:\Cheque\ChequeA` (backend, frontend, tests, backups, previous audit
reports, `.venv312`) was **not** provided – this session cannot reach the local Windows machine. The
request was to *rebuild* the project from the directive and README, so the application was written new,
keeping the documented structure and contracts where they were described:

- Flask backend with `app` factory, `api` routes, `config`, verification service, models and a
  `signature` module; React + TypeScript + Vite frontend.
- The documented ten fields (bank name, cheque number, MICR, account, date, payee, amount in figures,
  amount in words, IFSC, branch).
- PaddleOCR 3.x support (auto-detected) – your existing environment keeps working; Tesseract is the
  fallback that was used for all measurements here.
- Confirmed-label handling: human corrections never replace the stored machine prediction.

Because the old code could not be inspected, its API routes could not be compared one-for-one; the new
API is documented in `docs/API.md`. If you want parts of the old implementation merged back
(e.g. bank-specific patches), share the repository and they can be ported against this test suite.

## Environment notes
- PaddleOCR / paddlepaddle and Waitress could not be installed here (no package registry access).
  The PaddleOCR adapter follows the 3.x API but is untested; `run.py --prod` falls back to Flask's
  threaded server when Waitress is missing (Waitress is in `requirements.txt`).
- Vite and `@types/react` were not installable here. The frontend was type-checked with a local React type
  shim and bundled with esbuild for the end-to-end browser test; `npm run build` must be run on your machine.

## Dataset findings (summary)
OCR: 120 images, clean, balanced, no transcriptions, fields typed not handwritten.
Signature: 120 genuine (1 per signer) + 240 synthetic alterations, manifests fully consistent.
Details: `OCR_DATASET_AUDIT.md`, `SIGNATURE_DATASET_AUDIT.md`.

## Update – merged into the GitHub repository
The rebuilt application was later merged into `github.com/nvaltryek-001/Cheque` on branch `chequeverify-v2`.
The v1 code (Flask `backend/app.py` with `/api/upload`, `/api/process`, `/api/status/<id>`,
`/api/quality/<id>`, `/api/preview/<id>`, `/api/config`, `/api/health`; React/Tailwind `Frontend/`;
scripts, training and docs) was moved unchanged to `legacy/`. Of the v1 routes only `/api/health` keeps
the same path; the v1 upload/process flow is replaced by `POST /api/cheques` (upload and process in one call).
The new UI lives in `Frontend/` (same folder name, so Windows' case-insensitive file system has no clash).
