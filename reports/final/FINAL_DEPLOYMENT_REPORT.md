# Final deployment report

Date: 2026-11-01 · repository `nvaltryek-001/Cheque` · pull request #1 (`chequeverify-v2` → `main`) · target: Render, **Free plan** (chosen by the owner).
No password, key, one-time code, cheque image, account number or other private value is written in this report; the live check it describes prints
counts and seconds only.

## 1. Status: NOT DEPLOYED YET - there is no public address

The application, the container image and the Render blueprint are finished and checked (section 4), but **no Render service exists**. Creating it needs two
things only the owner can do: sign in to Render, and type the administrator password into Render's form. Neither can be done on the owner's behalf: signing in
to a hosting account and typing a password into it are actions the assistant is not allowed to take. Until the service exists, the three addresses the owner asked
for do not exist either, and this report does not invent them.

| What | Value |
|---|---|
| Frontend URL | **none yet.** It will be `https://<service name>.onrender.com`; the page and the API are one service, so this is also the backend URL |
| Backend URL | **none yet.** Same address (`/api/...` paths) |
| Health URL | **none yet.** `https://<service name>.onrender.com/api/health` → `{"ok": true, "data": {"status": "ok", "version": "2.0.0"}}` |
| Live test with the supplied dataset | **not run on a live address.** The same test was run against a local copy of the application: section 4.4 |
| Code commit verified by CI | see section 5 |

The moment the owner sends the address (or the output of the live check), section 6 is run against it and this table is filled in with what was actually seen.

## 2. What the owner has to do (about 10 minutes, no payment, no card)

1. Open <https://render.com/deploy?repo=https://github.com/nvaltryek-001/Cheque> (it opens Render's Blueprint page with this repository and its default branch, `main`, filled in; the repository is public, so Render needs no GitHub permission to read it).
2. Sign in to Render (a free account; "Sign in with GitHub" is fine).
3. In the form, give the service a name (this becomes the address) and **type the administrator password** into `CV_ADMIN_PASSWORD`: at least 12 characters, not used anywhere else. Do not paste it into a chat, a file or a commit. Leave the other values as they are; the plan is already `Free`.
4. Press **Apply** / **Create**. The first build takes several minutes (an estimate; it compiles the page and installs Tesseract). The service is ready when Render shows **Live**.
5. Send the address (`https://….onrender.com`) back. Then, from your own computer, run the live check (it asks for the password with hidden typing and prints no private value):
   ```bash
   python3 scripts/live_check.py https://YOUR-SERVICE.onrender.com --cheques "<OCR dataset folder>" --signatures "<signature dataset folder>"
   ```
   The first request after a quiet period waits about a minute (the Free service sleeps); the script waits for it by itself.

If anything in Render asks for a card, stop: the Blueprint is a `Free` web service and should not. Whatever Render shows, send it.

**What the Free plan means** (Render's limits; the application cannot change them):
- **No disk.** Everything created in the application - users, cheques, signers, results - is erased when the instance sleeps (after 15 minutes without a visit), restarts or is redeployed. The administrator account is created again from `CV_ADMIN_PASSWORD` at every start. The sign-in screen and a banner say so. Load the cheques again with *Batch upload* when you need them (for example just before a demonstration).
- **0.1 CPU, 512 MB.** Reading one cheque needs about 3 seconds of CPU on a full CPU; on Free it will take roughly half a minute (an estimate, **not measured on Render**). The blueprint processes one image at a time and lets later uploads wait.
- 750 free instance hours per month for the workspace. Pushing to GitHub does not redeploy (`autoDeployTrigger: "off"`): press **Manual Deploy** after a later push.
- To keep data across restarts, use `render.paid.yaml` (Starter plan + 1 GB disk): that needs the owner's payment details.

## 3. The data exposure the owner asked me to investigate and contain

Full details: `SECURITY_AUDIT.md`, findings 1, 8 and 13-15. In short:

- **Found.** Real values from the delivered datasets were in the public repository: one account number, cheque numbers, MICR/routing codes, amounts in words, one payee name and dataset file names (which are cheque and account numbers), in tests, in a label file, in an audit report and - the part a text search cannot see - in the trained MICR model, whose digit labels were stored in training order and so spelled out the training cheques' numbers. No cheque image, signature image, database, secret or password was ever tracked (checked in every revision, including the binaries).
- **Contained in the current files** (two commits, both checked by the pre-push audit against 518 private values and by CI): every value replaced by an invented one; the model stored in a fixed pseudo-random order (the 120 dataset cheques read identically before and after); training and report code no longer writes file names; new tests keep it that way. A search of the final files for all 518 values finds only one coincidence, a digit run inside a model weight.
- **Not contained: the Git history.** The older commits of `main` (4 commits, from `7ed8fe7`) and the branch's first 29 commits still hold the values, and so does anything already cloned (GitHub's traffic counters showed 68 clones by 24 distinct clients in the 14 days before checking, most of them probably the project's own CI). Removing them means rewriting history and force-pushing, which the owner told the assistant not to do without asking first. **The choice is open**, and it is the owner's:
  1. **Clean the history** (recommended if the repository must stay public): I rewrite `main` and the branch, replacing the 16 file versions that hold a value; I have rehearsed this on a throwaway copy and the same scan then finds none of the 518 values in any file, commit message, author field or branch name (apart from one coincidental run of digits inside a model weight), and every rewritten file still parses; the branch's current files come out byte-identical. It changes every commit hash, needs a force-push of `main` and the branch, and GitHub Support must be asked to drop cached copies of pull request 1. Anyone who already cloned keeps the old data.
  2. **Make the repository private** (Settings → Danger Zone): stops new readers at once, history stays. Render then needs a GitHub connection to read it.
  3. **Accept the disclosure** and treat the numbers as public.

## 4. What has been verified, and where

### 4.1 Tests
- Backend, 2026-11-01, final code: **609 passed** with the private datasets present (245 s); **602 passed, 7 skipped** without them (217 s; the 7 need the datasets and skip when absent). Ten of these tests were added for the live checker and for the privacy of reports.

### 4.2 GitHub Actions (five jobs per run: backend, frontend, dependency-audit, private-data-audit, docker)
- `04e5024` (Free-plan blueprint): 5 of 5 green on the push run (41311174019) and on the pull-request run (41311179584), including the new Docker step "No disk at all (the Render Free plan)".
- `15b7bb6` (cheque-data containment 2 of 2, Vite 6.4.5): 5 of 5 green on the push run (41326055412) and on the pull-request run (41326061671); `npm audit` for the whole tree: 0 vulnerabilities.
- `f5962db` (lock file and `npm ci`, blocking `npm audit`, reports; the last code change): push run 41327069274 and pull-request run 41327075332: 5 of 5 jobs green on both, including the Docker image build with `npm ci`.
- The Docker job builds the image from the repository (including the real `npm run build`) and runs it as a host would: unprivileged user, private files, security headers, 32-step workflow, restart, redeploy and hard-kill persistence, clean stop, reverse-proxy headers, a 512 MB memory check, no disk at all. Details: `DEPLOYMENT_VERIFICATION.md`.

### 4.3 Security
- `pip-audit`: no known vulnerabilities. `npm audit` (all packages, high and critical): **0 vulnerabilities** since Vite 5.4 → 6.4.5 (CI log of run 41326055412); the page itself contains only React and React-DOM.
- Open design limits (tokens in `sessionStorage`, no MFA/SSO, no encryption at rest, one SQLite file and process, SMTP delivery untested, no external penetration test) are listed in `SECURITY_AUDIT.md`, finding 11.

### 4.4 Rehearsal of the live check with the supplied datasets - on this machine, **not** on Render
`scripts/live_check.py` was run against a local copy of the application (Flask's server, one image at a time, 2 CPUs, temporary-storage mode like the Free plan), with the owner's OCR and signature datasets:
**91 checks passed, 0 failed.** 16 cheques spread over the four banks (axis, canara, icici, syndicate): every one uploaded and read with 10 of 10 fields found, median 3.3 s, slowest 3.6 s, on average 2.3 fields flagged for review per cheque; two cheques sent one by one share one batch id; 8 signers enrolled with their one genuine specimen, the enrolled image verified (REVIEW as an exact copy, never MATCH) and both altered copies of each verified and never accepted as MATCH (9 of 16 MISMATCH, 7 of 16 REVIEW); the test records were removed again. Public checks (health, page and assets, security headers, 401 on six protected routes, wrong password refused) passed too. The script prints no file name, password, token or value read from a cheque.

This shows the pipeline works end to end with the real datasets. It is **not** a measurement of accuracy: one genuine specimen per signer cannot measure it, and the application does not claim to detect skilled forgeries (`DATASET_TEST_RESULTS.md`, `SIGNATURE_EVALUATION_STATUS.md`).

## 5. Commits

| | |
|---|---|
| `main` | `b90662cf73e7019ff7876e0613fee5916f47d8ae` - the squash merge of pull request #1 (before it, `main` was `d509c14e59162afbe937ac7dcabcd5e929c860c3`, 4 commits). Its tip is clean; its first four commits still hold the old values (section 3) |
| Branch `chequeverify-v2` | `f5962db0617f0078c714081695d9091ab8c43050` (kept; its files are identical to the squash merge on `main`). It is also the last code change, verified by CI (section 4.2); later commits on `main` change documentation only |
| Pull request | #1, `chequeverify-v2` → `main`, **merged** (squash) on 2026-11-01 after CI was green on its head; the branch is kept |

## 6. What will be checked on the live address (as soon as there is one)

From outside, without signing in: `GET /api/health` is `ok`; `GET /` serves the page and its script and style files; the security headers are present; `GET /api/auth/config` says `storage_temporary: true`; protected routes answer 401 to a visitor; a cold start (first request after sleeping) answers within a few minutes. With the owner's password, run by the owner: `scripts/live_check.py` (section 2, step 5) - the upload and signature checks with the supplied datasets and the seconds per cheque on the Free plan. Only results that were seen will be written here.

## 7. Not verified

- A real Render deployment, the Free plan's speed and cold-start time, and what Render's build does with the image.
- E-mail sign-in (SMTP) with a real mailbox.
- Signature accuracy on genuine and skilled-forgery data; OCR on photographs and on banks other than the four in the dataset.
- Anything the owner decides about the Git history (section 3).
