# API reference

Base URL: `http://127.0.0.1:5000`. All responses are JSON envelopes:

```json
{ "ok": true,  "data": { ... }, "meta": { "total": 42, "page": 1, "per_page": 20 } }
{ "ok": false, "error": { "code": "validation_error", "message": "…", "details": { ... } } }
```

Authentication: `POST /api/auth/login` returns a token; send it as `Authorization: Bearer <token>`.
Roles: **admin** (everything), **operator** (process, verify, review), **auditor** (read-only + audit log).
Image endpoints also require the token – the frontend fetches them as blobs.

| Method | Path | Role | Purpose |
|---|---|---|---|
| GET | `/api/health` | public | Liveness (status + version only) |
| POST | `/api/auth/login` | public | `{username, password}` → `{token, user}`; 5 failures lock the account for 5 min |
| POST | `/api/auth/logout` | any | Revokes all of the user's tokens |
| GET | `/api/auth/me` | any | Current user |
| POST | `/api/auth/change-password` | any | `{current_password, new_password}` → new token |
| GET / POST | `/api/users` | admin | List / create users |
| PATCH / DELETE | `/api/users/{id}` | admin | Update role, name, active flag, password / delete |
| **Cheques** ||||
| POST | `/api/cheques` | admin, operator | multipart `file` → OCR, validation, signature detection. Returns the full record |
| POST | `/api/cheques/batch` | admin, operator | multipart `files` (≤ 20) |
| GET | `/api/cheques?q=&status=&page=&per_page=` | any | List (account numbers masked) |
| GET | `/api/cheques/{id}` | any | Full record: `fields`, `corrected`, `final`, `validation`, `quality`, `verifications`, `suggested_signer` |
| PATCH | `/api/cheques/{id}` | admin, operator | `{corrections: {field: value or null}, status, notes}` – machine values are never overwritten |
| POST | `/api/cheques/{id}/reprocess` | admin, operator | Re-run the pipeline |
| DELETE | `/api/cheques/{id}` | admin, operator | Delete record + stored images |
| GET | `/api/cheques/{id}/image` | any | Original image |
| GET | `/api/cheques/{id}/signature` | any | Detected/selected signature crop |
| POST | `/api/cheques/{id}/signature-crop` | admin, operator | `{box: [x, y, w, h]}` page fractions |
| GET | `/api/cheques/{id}/report.pdf` | any | PDF report (account masked) |
| **Signers & specimens** ||||
| GET / POST | `/api/signers` | any / admin | List (filters `q`, `bank`, `status`) / create `{signer_code, bank, full_name, account_number, notes}` |
| GET / PATCH / DELETE | `/api/signers/{id}` | any / admin / admin | Detail incl. specimens + history / update / delete (cascades specimens) |
| GET | `/api/signers/lookup?account=` | any | Signer for an account number |
| POST | `/api/signers/{id}/specimens` | admin | multipart `file` (+ `allow_poor_quality=true`) – quality check, preprocessing, duplicate detection |
| PUT | `/api/specimens/{id}` | admin | Replace (old one kept inactive, `replaced_by` set) |
| PATCH | `/api/specimens/{id}` | admin | `{status: active or inactive}` |
| DELETE | `/api/specimens/{id}` | admin | Delete |
| GET | `/api/specimens/{id}/image?variant=processed|original` | admin, operator | Biometric image (every view is audited) |
| POST | `/api/signers/import-dataset` | admin | `{splits: [...]}` enroll genuine specimens from `genuine_signatures.csv` |
| **Signature verification** ||||
| GET | `/api/signature/health` | any | Model + enrollment readiness |
| POST | `/api/signature/preprocess` | admin, operator | multipart `file` (+ `mode=cheque`) → normalised preview + quality (nothing stored) |
| POST | `/api/verifications` | admin, operator | `signer_id` + either `cheque_id` (optional `box`) or multipart `file` (+ `mode=cheque`). 409 if the signer has no active specimen |
| GET | `/api/verifications?decision=&signer_id=&cheque_id=&reviewed=0` | any | History |
| GET | `/api/verifications/{id}` | any | Detail incl. per-specimen feature scores and reasons |
| PATCH | `/api/verifications/{id}` | admin, operator | `{review_decision: APPROVED, REJECTED or null, review_note}` |
| DELETE | `/api/verifications/{id}` | admin | Delete |
| GET | `/api/verifications/{id}/query-image` | admin, operator | Questioned signature |
| **Datasets, models, reports, system** ||||
| GET | `/api/datasets/ocr/audit[?refresh=1]` | any (refresh: admin) | OCR dataset audit |
| GET | `/api/datasets/signature/audit[?refresh=1]` | any (refresh: admin) | Signature dataset audit |
| GET | `/api/datasets/ocr/items`, `/api/datasets/signature/items` | any | Paged manifest listing |
| GET | `/api/datasets/{ocr|signature}/image?filename=&split=&bank=` | admin, operator | Image resolved through the manifest (no client paths) |
| GET | `/api/models/status` | any | OCR engine, MICR model, signature model + thresholds |
| GET | `/api/models/readiness` | any | Which training tasks the data supports |
| POST | `/api/models/{signature|micr|ocr-eval}/run` | admin | Start a background job (one at a time) |
| GET | `/api/models/runs`, `/api/models/runs/{id}` | any | Job history and metrics |
| GET | `/api/reports/files`, `/api/reports/files/{name}` | any | Audit/evaluation reports in `reports/final` |
| GET | `/api/reports/{cheques|verifications|signers|audit-log}.csv` | any (audit-log: admin, auditor) | CSV exports (formula-injection safe, accounts masked) |
| GET | `/api/dashboard`, `/api/analytics` | any | Live metrics |
| GET / PUT | `/api/settings` | any / admin | Threshold overrides, specimen limits, auto-crop |
| GET | `/api/audit-log` | admin, auditor | Audit trail |
| GET | `/api/system/status` | any | Versions and dataset availability |

Common error codes: `auth_required` 401, `invalid_token` 401, `forbidden` 403, `not_found` 404,
`validation_error` 400, `weak_password` 400, `unsupported_type` 415, `file_too_large` 413 (upload size),
`image_too_large` 413 (decoded pixel count, checked from the header before decoding), `corrupted_image` 422,
`invalid_signature` 422, `poor_quality` 422, `duplicate_specimen` 409, `duplicate_other_signer` 409,
`no_reference` 409, `job_running` 409, `too_many_attempts` 429 (sign-in / password-check throttle),
`rate_limited` 429 (per-user limit on uploads, verifications and other heavy calls), `internal_error` 500
(no stack traces or paths are returned). Every 429 carries a `Retry-After` header (seconds) and `details.retry_after`.
