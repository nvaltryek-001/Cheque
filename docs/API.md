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
| GET / POST | `/api/signers` | any / admin | List (filters `q`, `bank`, `status`; every row has `reference_status`, `independent_samples`, `independent_days`) / create `{signer_code, bank, full_name, account_number, notes}` |
| GET / PATCH / DELETE | `/api/signers/{id}` | any / admin / admin | Detail incl. specimens, `sessions`, `enrollment` (readiness) and history / update / delete (cascades specimens) |
| GET | `/api/signers/lookup?account=` | any | Signer for an account number |
| POST | `/api/signers/{id}/specimens` | admin | multipart `file` (+ `allow_poor_quality=true`, `session_id`, `source_ref`) – quality check, preprocessing, duplicate and same-signing detection. Without `session_id` the specimen does not count as independent |
| POST | `/api/signers/{id}/specimens/batch` | admin | multipart `files` (≤ `CV_MAX_BATCH_FILES`, default 20) + **`session_id` (required)** (+ `allow_poor_quality`). Each file is judged on its own: `{results: [{file, ok, specimen \| error{code,message}}], enrolled, rejected, enrollment}`; 201 if any file was enrolled, else 200 |
| GET | `/api/signers/{id}/enrollment` | any | Reference-set readiness: `ready`, `status` (`none`/`incomplete`/`ready`), `independent_samples`, `independent_days`, `missing`, `notes`, `policy`, `sessions`, `consent_recorded` |
| GET | `/api/enrollment/overview` | any | Policy and counts of signers that are ready / incomplete / without specimens (per-signer status: `GET /api/signers`) |
| POST | `/api/signers/{id}/sessions` | admin | Record a signing session `{session_date (YYYY-MM-DD, not in the future), method, instrument, consent_ref, notes}` |
| PATCH / DELETE | `/api/sessions/{id}` | admin | Edit a session / delete an **empty** session (409 `session_has_specimens` otherwise) |
| PUT | `/api/specimens/{id}` | admin | Replace (old one kept inactive, `replaced_by` set; the new one keeps the old one's session and source unless `session_id`/`source_ref` are sent) |
| PATCH | `/api/specimens/{id}` | admin | `{status: active or inactive}` and/or `{session_id: id or null, source_ref}` (audited; reactivation re-runs the same-signing check) |
| DELETE | `/api/specimens/{id}` | admin | Delete |
| GET | `/api/specimens/{id}/image?variant=processed|original` | admin, operator | Biometric image (every view is audited) |
| POST | `/api/signers/import-dataset` | admin | `{splits: [...]}` enroll genuine specimens from `genuine_signatures.csv` |
| **Signature verification** ||||
| GET | `/api/signature/health` | any | Model + enrollment readiness |
| POST | `/api/signature/preprocess` | admin, operator | multipart `file` (+ `mode=cheque`) → normalised preview + quality (nothing stored) |
| POST | `/api/verifications` | admin, operator | `signer_id` + either `cheque_id` (optional `box`) or multipart `file` (+ `mode=cheque`). 409 if the signer has no active specimen. `details.reference_set` reports the readiness of the signer's references; when it is not ready a `MATCH` is returned as `REVIEW` with `details.capped_by_policy`, `details.uncapped_decision` |
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
| GET / PUT | `/api/settings` | any / admin | Threshold overrides, specimen limits, auto-crop, `enrollment.min_specimens`, `enrollment.min_sessions`, `enrollment.cap_match_when_not_ready` |
| GET | `/api/audit-log` | admin, auditor | Audit trail |
| GET | `/api/system/status` | any | Versions and dataset availability |

Common error codes: `auth_required` 401, `invalid_token` 401, `forbidden` 403, `not_found` 404,
`validation_error` 400, `weak_password` 400, `unsupported_type` 415, `file_too_large` 413 (upload size),
`image_too_large` 413 (decoded pixel count, checked from the header before decoding), `corrupted_image` 422,
`invalid_signature` 422, `poor_quality` 422, `duplicate_specimen` 409, `duplicate_other_signer` 409,
`same_signing` 409 (a second capture of a signing already enrolled), `session_has_specimens` 409, `too_many_files` 400,
`no_reference` 409, `job_running` 409, `too_many_attempts` 429 (sign-in / password-check throttle),
`rate_limited` 429 (per-user limit on uploads, verifications and other heavy calls), `internal_error` 500
(no stack traces or paths are returned). Every 429 carries a `Retry-After` header (seconds) and `details.retry_after`.
