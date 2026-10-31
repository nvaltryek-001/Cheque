# Security audit

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

## Residual risks (development-only limitations)
No TLS in the bundled server; tokens in `sessionStorage`; no encryption at rest; no MFA/SSO; no general
rate limiting; SQLite single-file database; no automated retention; no external penetration test.
**Not production-ready for banking use until these are addressed.**
