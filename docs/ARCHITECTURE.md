# Architecture

## Processes
One Python process (Flask; Waitress in production) serves both the REST API under `/api/*` and the
compiled React app from `Frontend/dist`. In development, Vite serves the UI on :5173 and proxies `/api`
to Flask on :5000. Training/evaluation jobs run in a background thread inside the server, one at a time,
and are recorded in `model_runs`.

## Data
SQLite (`backend/instance/chequeverify.db`) tables: `users`, `cheques`, `signers`, `specimens`,
`verifications`, `model_runs`, `settings`, `audit_log`, `login_attempts` (persisted sign-in throttle) and `login_codes`
(one-time email sign-in codes, stored as keyed hashes only). Images live in `backend/instance/storage/<area>/`
under random UUID names (`cheques`, `crops`, `specimens`, `specimens_processed`, `queries`).
The original datasets are only read.

## OCR pipeline (`app/ocr`)
1. `quality.assess_cheque` – resolution, sharpness (Laplacian variance), brightness, contrast, aspect.
2. `micr.read_micr` – crops the bottom band, segments E-13B glyphs by connected components, groups them
   at symbols/gaps, classifies digits by 1-NN correlation against templates learned from the train split.
   Gives cheque number (group 1) and MICR code (9-digit group).
3. `engine` – PaddleOCR 3.x or Tesseract 5 (command line, TSV output with word boxes).
4. `regions.text_rows` – inside each CTS-2010 field region: background-normalised binarisation, removal
   of box lines/underlines, keep components at the height of the filled-in text, group into rows and
   redraw them on a clean canvas. `regions.boxed_digits` reads the 8 date boxes.
5. `fields.extract_cheque` – bank (keywords + IFSC prefix), IFSC (pattern + OCR confusions), branch
   (header heuristic), payee, amount in words (number-word filter, fuzzy matching), amount in figures
   (Indian digit grouping; the ₹ sign misread as a digit is removed when the words confirm it),
   account number, date.
6. `fields._validate` – IFSC format and bank consistency, account length, MICR/cheque formats, date
   validity (stale/post-dated), amount words vs figures, completeness. Failing cross-checks downgrade
   the fields to `review`.

## Signature module (`app/signature`)
- `detect.find_signature` – lower-right search area, ink clustering, caption removal; manual override.
- `preprocess.preprocess` – grayscale → background normalisation → median denoise → CLAHE → Otsu
  (adaptive fallback) → speck removal → foreground crop → aspect-preserving resize + padding to 128×384,
  stroke grey levels kept → quality report (ink fraction, contrast, sharpness, fragments, size).
- `compare.features` – ORB and SIFT ratio-test matching (safe with empty descriptors and k<2 neighbour
  lists), SSIM, HOG cosine, projection-profile correlation, chamfer agreement, ink-density and aspect ratios.
- `compare.FusionModel` – logistic regression with non-negative weights, trained on train pairs;
  thresholds chosen on validation pairs (`match` at ≤ 5 % FAR, `mismatch` at ≤ 5 % FRR). Without a model
  file an unweighted baseline with fixed thresholds is used and the result says so.
- `compare.decide` – compares against every active specimen, takes the best score, maps to
  MATCH / REVIEW / MISMATCH, downgrades MATCH to REVIEW on poor query quality, explains the decision.
- `training` – pair construction, training, threshold selection, evaluation (see MODEL_EVALUATION.md).

## Sign-in (`app/services/email_login.py`, `mailer.py`, `auth.py`)
Two ways to a token: username + password, or a one-time code mailed to an institutional address
(`@saividya.ac.in` by default, no list of permitted people). `email_login` validates the address, issues and
checks codes (keyed hash, single use, attempt and request limits) and creates the account on first use;
`mailer` delivers the code (SMTP with STARTTLS, a development-only log mode, an in-memory outbox for tests).
New email accounts are stored with the least-privileged role plus a `pending_approval` flag; the API reports
them as role `pending` and `login_required` refuses them everywhere except `/api/auth/me` and `/api/auth/logout`.
See `docs/AUTHENTICATION.md`.

## Frontend (`Frontend/src`)
`api.ts` (fetch wrapper, types), `auth.tsx` (session), `router.ts` (hash router), `components/ui.tsx`
(buttons, panels, states, toasts, modal, pagination, authenticated images, file drop, decision stamp),
`pages/*` (one file per screen; `pages/Login.tsx` holds the sign-in views and the waiting-for-approval page),
`identity.ts` (display name and initials from an address, role labels). Tokens are kept in `sessionStorage`
(cleared when the tab closes).

### Visual design
`styles.css` is one stylesheet built on CSS custom properties: a midnight-ink navigation and sign-in hero, a pale
paper canvas, ultramarine for actions and an aqua "scan" accent that appears only where the system is reading an
image (viewfinder corners around analysed cheques, detection boxes in the sign-in drawing). Green, amber and red are
reserved for decisions and statuses. Text is Inter (body) and Inter Display (headings), subsetted to Latin and
served from `/fonts` under the SIL Open Font License (`Frontend/public/fonts/OFL.txt`; `Frontend/scripts/subset_fonts.py`
regenerates the files). Nothing is loaded from another origin and there are no inline styles, so the strict
Content-Security-Policy still holds; `backend/tests/test_frontend_theme.py` guards that, the contrast ratios and
reduced-motion support. Motion is limited to the one-time scan on the sign-in screen, the decision stamp and
loading indicators.
