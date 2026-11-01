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
`render.yaml` is the **Free-plan** blueprint (the one-click link uses it). `render.paid.yaml` is the Starter-plan
blueprint with a 1 GB persistent disk, for data that must survive.

**What the Free plan means** (Render's limits, <https://render.com/docs/free>; the application cannot change them):
- **No persistent disk.** Users, cheques, enrolled signatures and the database are erased whenever the instance
  restarts, is redeployed or goes to sleep. The administrator account is created again from `CV_ADMIN_PASSWORD` at
  every start; everything else has to be uploaded again.
- **Sleeps after 15 minutes without a visit.** The next visit waits about a minute while the instance wakes up.
- **0.1 CPU and 512 MB.** The Starter plan has 0.5 CPU and the same memory. Reading one cheque is CPU-bound and needs
  about 3 seconds of CPU (measured), so on Free it takes roughly half a minute (an estimate; one tenth of a CPU) instead
  of about 3 seconds on a full CPU. The blueprint processes one image at a time and lets later uploads wait.
- 750 free instance hours per month for the whole workspace; builds use the workspace's monthly pipeline minutes.

**Steps**
1. Make sure the branch you want to run is on GitHub (see *Pushing* below). Nothing needs to be merged into `main`.
2. render.com → New → **Blueprint** → connect `nvaltryek-001/cheque` → in the Blueprint settings choose the branch
   (for example `chequeverify-v2`). Render reads `render.yaml` from that branch.
   Shortcut with the repository and branch filled in (the repository is public, so Render needs no GitHub connection):
   <https://render.com/deploy?repo=https://github.com/nvaltryek-001/Cheque>
3. Enter a value for `CV_ADMIN_PASSWORD` when asked (12 or more characters; it is not stored in git) → **Apply**.
   Render builds the Dockerfile (the first build takes several minutes) and gives you an
   `https://chequeverify-ai-xxxx.onrender.com` URL. The blueprint turns auto-deploy off, so a push never restarts the
   service (which would erase its temporary data); to run newer code use **Manual Deploy → Deploy latest commit**.
4. Open the URL → *Administrator sign-in* → user `admin` and the password from step 3. Sign-in by emailed code needs
   the `CV_SMTP_*` values (below); until they are set only the administrator sign-in works. To test the running service with your
   own images, use `scripts/live_check.py` (see *Verifying a deployment*).
5. The hosted copy starts empty. The private datasets are not in git or in the image. To use your own cheques, sign in
   and use the batch upload on the Cheques page; signature specimens are added on the Signers page.

**To keep data (paid):** in the Render dashboard choose the Starter plan and add a 1 GB disk mounted at `/data`, or create
the Blueprint from `render.paid.yaml` (Render lets you change the Blueprint file name during setup; or copy that file over
`render.yaml`). Details that apply to a service with a disk:
- What Render's documentation says about a service with a disk (checked against docs.render.com, not exercised here
  because nothing has been deployed): only files under the mount path (`/data`) survive a deploy or restart; the disk is
  not available while the image is built or during a pre-deploy command, so nothing may depend on it then; a service
  with a disk runs a single instance and **cannot do zero-downtime deploys** (expect a short outage on every deploy);
  the disk is snapshotted daily and snapshots are kept for 7 days; when a service is stopped Render sends SIGTERM and
  then SIGKILL after a delay (30 seconds by default). The server handles SIGTERM itself (see *Stopping and crashing*).
- `PORT` is `10000` on Render; the blueprint states it, the entrypoint listens on `$PORT` and the health check follows it.
- Environment values marked `sync: false` in a blueprint (`CV_ADMIN_PASSWORD`) are asked for only when the Blueprint is
  first created. Add anything new afterwards (the `CV_SMTP_*` values) in the dashboard under Environment.
- **Memory.** The Free and Starter plans both have 512 MB. A 25-megapixel scan costs about 250 MB while it is processed and four at once
  peaked above 1 GB in the rehearsal, which would end in an out-of-memory kill and a restart for every user. The blueprint
  therefore sets `CV_MAX_IMAGE_PIXELS=12000000` (a full 600 dpi cheque is 10.7 MP) and `CV_MAX_CONCURRENT_IMAGES` (2 on Starter, 1 on Free)
  (others wait up to `CV_IMAGE_QUEUE_SECONDS`, then get `503 busy` with `Retry-After`), and the image sets glibc malloc
  options, and the Dockerfile itself now defaults to the same two limits. With these, eight simultaneous maximum-size
  uploads peaked between 250 and 395 MB in repeated rehearsals (see
  `reports/final/DEPLOYMENT_VERIFICATION.md` for what that measurement does and does not show). Raise the limits only
  together with a larger plan.
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
| `CV_STORAGE_TEMPORARY` | detected | set by the container entrypoint when `/data` is not a mounted disk or volume (the Render Free plan); the log, the sign-in screen and a banner then say that data is erased on restart. `0` or `1` overrides the detection |
| `CV_EMAIL_DELIVERY` | empty (email sign-in off) | `smtp` once the `CV_SMTP_*` values below are set. **Never `log` on a hosted copy** |
| `CV_SMTP_HOST`, `CV_SMTP_PORT`, `CV_SMTP_SECURITY`, `CV_SMTP_USER`, `CV_SMTP_FROM` | -, 587, `starttls`, -, - | your mail service's values; add them in the Render dashboard → Environment (not in `render.yaml`) |
| `CV_SMTP_PASSWORD` | - | a secret: type it in the dashboard only, never in a file, a commit or a chat |
| `CV_EMAIL_DOMAINS` | `saividya.ac.in` | keep, or list the domains that may sign in (exact match) |
| `CV_EMAIL_DEFAULT_ROLE` | `pending` | keep: new people wait for an administrator's approval |
| `CV_ADMIN_EMAILS` | empty | the address(es) of the first email administrator(s); otherwise use the `admin` password account to approve people |
| `CV_PASSWORD_LOGIN` | `1` | `0` only after an email administrator exists and mail delivery is proven |
| `CV_PROXY_HOPS` | 0 | 1 behind Render / one load balancer; 0 when the app is reached directly |
| `CV_RATE_LIMIT_PER_MIN` | 120 | keep; 0 switches the per-user limit off |
| `CV_LOGIN_MAX_ATTEMPTS`, `CV_LOGIN_IP_MAX_ATTEMPTS`, `CV_LOGIN_LOCK_SECONDS` | 5, 20, 300 | keep |
| `CV_MAX_IMAGE_PIXELS` | 12000000 in the Docker image (25000000 when the app runs without Docker) | the image is safe in 512 MB as shipped; raise it only on a host with more memory. A 300 dpi A4 scan is ≈ 8.7 million pixels, a 600 dpi cheque ≈ 10.7 million. Larger images are refused with `413 image_too_large` and a message that names the limit |
| `CV_MAX_CONCURRENT_IMAGES`, `CV_IMAGE_QUEUE_SECONDS` | 2, 30 | keep on a 512 MB instance; images are processed this many at a time, others wait this long and then get `503 busy` |
| `CV_STOP_GRACE_SECONDS` | 20 | seconds the server may take to finish after SIGTERM before it ends itself (keep it below the platform's grace period; Render's is 30) |
| `PORT` | 8000 (image), 10000 (Render) | set by the platform |
| `CV_HSTS_MAX_AGE` | 31536000 | keep once HTTPS works; 0 while testing |
| `CV_CORS_ORIGINS` | localhost dev origins (image: empty) | leave empty for the bundled UI |

Email sign-in (`docs/AUTHENTICATION.md`) needs a mail service you control; without it the sign-in screen offers only the
administrator form. Check it by signing in with your own address after the first deploy and keep the password account
available until delivery works. Choosing a mail service (and any cost or sender verification it needs) is yours to approve.

Locked out after too many wrong passwords? Wait for the window to pass or run `python manage.py unlock-logins`
inside the container (`docker exec <name> python manage.py unlock-logins`).

## What is stored where, and what survives
| Folder (in the image) | Holds | Survives a redeploy? |
|---|---|---|
| `/data/instance` | the SQLite database (users, signers, cheques, verifications, audit log, settings, sign-in throttle), `secret_key` (unless `CV_SECRET_KEY` is set), the signature test-use ledger | yes: it is on the disk |
| `/data/instance/storage` | cheque images, signature crops, specimens, query images | yes |
| `/data/models` | the trained MICR templates and signature model | yes (see the next section for how a new release refreshes them) |
| `/data/reports` | evaluation and audit reports | yes |
| `/data/.seed_manifest.json` | which shipped files were copied to the disk, and what they looked like | yes |
| everything else in the container (code, `/tmp`) | nothing that matters | no, by design |

The application writes only to these folders (and to a temporary folder for large uploads), so the container's own
filesystem can be read-only (`docker run --read-only --tmpfs /tmp`); the CI job does exactly that.

### How the shipped models and reports reach the disk
The image contains the trained models (`backend/models_store`) and the reports (`reports/final`). At every start the
entrypoint, **after dropping root**, brings them to the disk and records a SHA-256 of what it copied
(`/data/.seed_manifest.json`):

| State of the file on the disk | What happens |
|---|---|
| missing | copied (atomically: a temporary file, then a rename, so a kill cannot leave a half-copied model) |
| identical to the shipped file | left alone |
| different, but still exactly the copy made earlier | **updated** to the new release's file, so improved models reach an existing disk |
| different and changed on the disk (a model retrained in the app, a regenerated report) | **kept**, and a line `seed: models/... kept: changed on this disk` is logged |
| different and not in the manifest (a disk from before the manifest existed) | kept, with `kept: ... origin is unknown` |
| not a regular file (a link, a folder) | left alone |

To go back to the shipped model after retraining, delete the file on the disk (`docker exec <name> rm /data/models/...`
or the Render shell) and restart: the shipped one is copied again. Nothing is ever overwritten that cannot be shown to
be an untouched copy.

### Stopping and crashing
* **Stop.** A container's main process is PID 1, and the kernel gives PID 1 no default action for SIGTERM, so a server
  without its own handler ignores `docker stop` and a Render deploy until the platform's grace period ends and kills it
  in whatever it was doing. `run.py` installs a handler: the server stops, finishes what it can and exits with code 0 in
  well under a second when idle. If something refuses to stop, a watchdog ends the process after `CV_STOP_GRACE_SECONDS`.
  (`wsgi.py`, used by an external WSGI runner, does not install the handler.)
* **Crash safety.** Models, reports, the secret key, the evaluation ledger and every stored image are written to a
  temporary file in the same folder and renamed into place (`app/services/atomic.py`), so an out-of-memory kill, a power
  cut or SIGKILL leaves the old file or the new file, never a truncated one. Leftover `.tmp-*` files older than a minute
  are removed at the next start. SQLite runs in WAL mode and recovers by itself.
* **Unfinished jobs.** A training or evaluation job that the stopped process never finished is marked `failed`
  ("interrupted: the server stopped before this job finished; start it again") at the next start, not left "running".
* **A damaged model file** no longer breaks every request: it is logged, the dashboard says "file damaged: retrain or
  restore a backup", and the service runs as if that model had not been trained yet.
* **One process only.** SQLite, the job lock and the start-up housekeeping assume a single server process on one disk.
  Do not scale out, and do not run two containers on the same volume.

## Backups and restore
Render's daily snapshots (7 days) and your host's volume snapshots protect against losing the disk. A backup file made
with the tool below protects against mistakes (a deleted signer, a bad import), is portable and can be copied off the
server. Both are worth having.

```bash
# inside the container / on the machine that has the data folders (docker exec -it chequeverify bash)
python manage.py backup --out /data/backups            # a folder that is NOT inside the data it backs up
python manage.py verify-backup /data/backups/chequeverify-backup-20261101T120000Z.tar.gz
python manage.py restore-backup FILE --into /restore   # a new, empty folder; verifies first
```
* One `.tar.gz` file (mode 600): the database (a consistent snapshot taken with SQLite's online backup, safe while the
  server runs), the other files in the instance folder, all stored images (`--skip-images` leaves them out, then cheque
  and specimen records point at images that are not in the backup), the models and the reports, plus a `MANIFEST.json`
  with the size and SHA-256 of every file.
* **Not in the backup, on purpose:** the secret key (a leaked backup must not allow forged sign-ins; everybody signs in
  again after a restore, and on Render the key is an environment value anyway), and environment settings or passwords.
* `verify-backup` reads the whole file and compares every checksum; `restore-backup` refuses anything that fails that
  check, any entry that is not a plain file in a known folder (no `..`, links, absolute paths or odd characters, even if
  the checksum list was forged to match), and a target that is not empty. After extracting it opens the database and
  compares `integrity_check` and every table's row count with the backup's record. It never overwrites or deletes.
* **The file is personal data** (user addresses, cheque images, signature specimens) and is **not encrypted** by the
  tool. Keep it on an encrypted volume or encrypt it before it leaves the server, and copy it off the server: a backup on
  the same disk does not survive losing the disk.
* **Restoring for real.** Stop the service (on Render: suspend it), restore into a new empty folder, check it, then
  replace the data: the restored folder has the layout of `/data` (`instance/` with `storage/` inside it, `models/`,
  `reports/`). On a fresh Docker volume you can restore straight into it before the first start:
  `docker run --rm -v chequeverify-data:/data -v "$PWD":/b chequeverify python manage.py restore-backup /b/FILE --into /data/restored`
  and then move the folders up one level. Rehearse this once before you need it; `scripts/deploy_simulation.py` does so
  with synthetic data on every run.
* Pruning old backups is deliberately not automated: delete them yourself when you have decided how long to keep them.

## Verifying a deployment
* `python3 scripts/live_check.py https://YOUR-SERVICE.onrender.com --cheques "<OCR dataset folder>" --signatures "<signature dataset folder>"`
  checks a running service from the outside, with your own images, on your own computer (Python 3 only, nothing to install):
  it waits for a sleeping service to wake, then checks health, the page and its files, the security headers, the sign-in
  options, that a visitor who is not signed in is refused, that a wrong password is refused; asks for the administrator
  password with hidden typing (or reads `CV_LIVE_PASSWORD`; never printed, never stored); checks the trained models; uploads
  cheques one at a time and reports seconds, fields read and fields flagged for review; enrols a few signers and verifies
  the enrolled image and the altered copies; and removes what it created (`--keep` leaves it visible in the application).
  It prints counts, decisions and seconds only, never a file name or a value read from a cheque, so its output can be pasted
  into a report. Exit code 0 means every check passed. Because it signs in as the administrator, run it only against a
  service you own; on the Free plan the test cheques and signers vanish by themselves when the service sleeps.
* `python backend/tests/persistence_check.py snapshot BASE PASSWORD > state.json` before a redeploy and
  `... verify BASE PASSWORD --state state.json` after it: compares users, settings, signers, the hash of every specimen,
  cheque and query image, verification history, models, reports, that the session issued before the restart is still
  valid (the secret key persisted) and that a locked-out account is still locked out. Exit code 0 means identical, 1 means
  something differs (listed), 2 means it could not run. `--allow models,reports` tolerates files that a new release ships.
  Delete `state.json` afterwards: it holds a short-lived sign-in token.
* `sudo python3 scripts/deploy_simulation.py` rehearses the start-up, a redeploy, a kill, a new release, a backup and
  restore, an empty disk and a hostile disk on this machine without Docker (root and `unshare` give the container-like
  conditions; without them the affected checks are skipped and said so). `--profile docker` uses the Dockerfile's
  settings only; the default profile adds the values from `render.yaml`.
* `reports/final/DEPLOYMENT_VERIFICATION.md` records what was run, the numbers, and what could **not** be verified
  (no Docker build, no Render deploy, Waitress not tested).

## Continuous integration
`.github/workflows/ci.yml` runs on every push and weekly. Jobs: backend tests (dataset tests skip
automatically), frontend type-check + Vite build, `dependency-audit` (`pip-audit`; `npm audit` blocks at high and critical, for the packages that ship and
for the whole tree including the build tools; installs from the committed `Frontend/package-lock.json` with `npm ci`), `private-data-audit` (`scripts/prepush_audit.py --base origin/main`) and a
Docker job (build, live smoke test, a fingerprint of all stored data, unprivileged user, private files, security headers,
a restart, a clean `docker stop` in under 8 seconds with exit code 0, a new container on the same disk compared with the
fingerprint, a SIGKILL and restart followed by a database integrity check, HSTS and client address behind one proxy hop,
password policy, eight simultaneous maximum-size uploads inside a 512 MB container that must not be OOM-killed, and a
run with a read-only root filesystem). Actions are pinned to commit SHAs and the workflow token is read-only; Dependabot
keeps pip, npm, Actions and Docker current. **The stop, redeploy, kill, memory and read-only steps were written without
Docker available and have not run on GitHub yet; if one fails on the first run, the failure is information about the
image, not necessarily about the step.**

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
