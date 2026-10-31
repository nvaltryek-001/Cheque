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

## Continuous integration
`.github/workflows/ci.yml` runs on every push: backend tests (dataset tests skip automatically), the
frontend type-check + Vite build, and a Docker build with a health-check smoke test.

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
