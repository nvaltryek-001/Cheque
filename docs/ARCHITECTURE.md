# Architecture

## Processes
One Python process (Flask; Waitress in production) serves both the REST API under `/api/*` and the
compiled React app from `Frontend/dist`. In development, Vite serves the UI on :5173 and proxies `/api`
to Flask on :5000. Training/evaluation jobs run in a background thread inside the server, one at a time,
and are recorded in `model_runs`.

## Data
SQLite (`backend/instance/chequeverify.db`) tables: `users`, `cheques`, `signers`, `specimens`,
`verifications`, `model_runs`, `settings`, `audit_log`. Images live in `backend/instance/storage/<area>/`
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

## Frontend (`Frontend/src`)
`api.ts` (fetch wrapper, types), `auth.tsx` (session), `router.ts` (hash router), `components/ui.tsx`
(buttons, panels, states, toasts, modal, pagination, authenticated images, file drop, decision stamp),
`pages/*` (one file per screen). Tokens are kept in `sessionStorage` (cleared when the tab closes).
