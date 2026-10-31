# Security and privacy

| Concern | Control |
|---|---|
| Authentication | Signed, time-limited bearer tokens (itsdangerous, 8 h). Tokens embed a per-user version; logout, password change, reset and deactivation revoke all tokens. Werkzeug password hashing. Lockout after 5 failures for 5 minutes. Minimum password policy. |
| Authorization | Every endpoint declares allowed roles. Enrollment, specimen changes, users, settings, dataset import, training and deletion of verifications are admin-only. Auditors are read-only. |
| Unauthenticated enrollment | Impossible: specimen upload requires an admin token; knowing a signer ID is not enough. |
| Biometric data | Specimen and query images are served only to admin/operator via authenticated endpoints, each specimen view is written to the audit log. No public static route exposes storage. |
| Uploads | Size limit (413), extension allow-list, magic-byte check, full decode, dimension checks; stored under UUID names; client filename is never used as a path. |
| Path traversal | All file reads resolve inside the storage area (`storage.safe_path`); dataset images are looked up through the manifest, not by client path; report downloads accept only plain names. |
| Error leakage | Unhandled errors return a generic message; no stack traces or filesystem paths in responses. `/api/health` returns only status and version. |
| CORS | Allow-list (`CV_CORS_ORIGINS`), default only the Vite dev origin. |
| Headers | `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store` on API responses. |
| Logs | Audit log stores action, user, record id and IP – never images, passwords or full account numbers. |
| Exports | Account numbers masked in lists, CSV exports and PDF reports; CSV cells starting with `= + - @` are escaped. |
| Secrets | No hard-coded secrets. Secret key from `CV_SECRET_KEY` or a random key in `instance/secret_key` (0600). First admin password random unless set. |
| Original data | Datasets are opened read-only; `.gitignore` excludes datasets, the instance folder and `.env`. |

## Development-only limitations
- No HTTPS in the bundled server: put it behind a TLS reverse proxy (IIS/nginx) for any networked use.
- Tokens in `sessionStorage` are readable by scripts on the same origin; the app has no third-party scripts, but an HttpOnly-cookie session with CSRF protection is preferable in production.
- Files and the SQLite database are not encrypted at rest.
- No MFA/SSO, no data-retention automation (setting reserved), no rate limiting beyond login lockout.
- No independent penetration test has been performed.
**Do not describe this system as production-ready for banking without addressing the above.**
