# Deployment

The app ships as one Docker image: the React UI is built with Vite and served by the Flask API
(Waitress). Data (SQLite database, uploaded cheques, enrolled specimens, secret key) lives in `/data`,
which must be a persistent volume.

## 1. Any Docker host (VPS, office server, laptop)
```bash
docker build -t chequeverify .
docker run -d --name chequeverify -p 8000:8000 \
  -e CV_ADMIN_PASSWORD='Choose-a-strong-one-123' \
  -v chequeverify-data:/data \
  chequeverify
# open http://<server>:8000  – sign in as admin
```
To use the datasets inside the container (dataset pages, import, training), mount them read-only:
`-v /path/to/dataset:/data/ds/ocr:ro -v /path/to/signature:/data/ds/sig:ro -e CV_OCR_DATASET=/data/ds/ocr -e CV_SIGNATURE_DATASET=/data/ds/sig`.
Put a TLS reverse proxy (nginx, Caddy, IIS) in front for anything beyond a private network.

## 2. Render (managed hosting, connected to this GitHub repo)
1. Push this branch to GitHub (see below) and merge it into `main`.
2. render.com → New → **Blueprint** → select `nvaltryek-001/Cheque`. Render reads `render.yaml`.
3. Enter a value for `CV_ADMIN_PASSWORD` when asked → **Apply**. Render builds the Dockerfile and gives
   you an `https://chequeverify-ai-xxxx.onrender.com` URL. Every push to `main` redeploys.
- `render.yaml` uses the *starter* plan because a persistent disk is required to keep data; on the free
  plan the service works but loses all records on every restart/redeploy.
- The private datasets are not in git, so dataset audit/import/training screens show "not found" on the
  hosted copy. Enroll signers by uploading specimens instead, or keep the dataset work on your own machine.

## 3. Windows machine without Docker
`.\start.ps1` (see README) – serves on http://127.0.0.1:5000. Add `--host 0.0.0.0` in `start.ps1` and open
the Windows firewall port to reach it from other PCs on the LAN.

## Settings that matter for a hosted copy
| Variable | Default | Hosted recommendation |
|---|---|---|
| `CV_ADMIN_PASSWORD` | random, printed once | set it (first start only); it must satisfy the password policy or the server refuses to start |
| `CV_MIN_PASSWORD_LENGTH` | 8 | 12 (set in `render.yaml`) |
| `CV_PROXY_HOPS` | 0 | 1 behind Render / one load balancer; 0 when the app is reached directly |
| `CV_RATE_LIMIT_PER_MIN` | 120 | keep; 0 switches the per-user limit off |
| `CV_LOGIN_MAX_ATTEMPTS`, `CV_LOGIN_IP_MAX_ATTEMPTS`, `CV_LOGIN_LOCK_SECONDS` | 5, 20, 300 | keep |
| `CV_MAX_IMAGE_PIXELS` | 25000000 | keep (a 300 dpi A4 scan is ≈ 8.7 million) |
| `CV_HSTS_MAX_AGE` | 31536000 | keep once HTTPS works; 0 while testing |
| `CV_CORS_ORIGINS` | localhost dev origins (image: empty) | leave empty for the bundled UI |

Locked out after too many wrong passwords? Wait for the window to pass or run `python manage.py unlock-logins`
inside the container (`docker exec <name> python manage.py unlock-logins`).

## Continuous integration
`.github/workflows/ci.yml` runs on every push and weekly. Jobs: backend tests (dataset tests skip
automatically), frontend type-check + Vite build, `dependency-audit` (`pip-audit`, `npm audit`, uploads a
generated `package-lock.json`), `private-data-audit` (`scripts/prepush_audit.py --base origin/main`) and a
Docker job (build, live smoke test, unprivileged user, private files, security headers, persistence across a
restart, HSTS and client address behind one proxy hop, password policy). Actions are pinned to commit SHAs and
the workflow token is read-only; Dependabot keeps pip, npm, Actions and Docker current.

Before pushing, run the audit locally with your private terms file: `docs/SECURITY.md` → *Pre-push audit*.

## Pushing the `chequeverify-v2` branch
From a clone of the repository where you have push rights:
```bash
git fetch /path/to/chequeverify-v2.bundle chequeverify-v2:chequeverify-v2
git push -u origin chequeverify-v2
```
Then open a pull request `chequeverify-v2 → main` on GitHub. The previous implementation is kept unchanged
under `legacy/`.

## Before handling real cheques
HTTPS, strong admin password, backups of `/data`, SSO/MFA and encrypted storage – see `docs/SECURITY.md`.
