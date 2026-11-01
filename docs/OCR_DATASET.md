# OCR dataset

Location: `dataset/` (configurable with `CV_OCR_DATASET`). Layout: `<split>/<bank>/<image>`; the
validation folder is spelled `vaildate` in the original and is treated as `val`. Nothing is renamed or modified.

| | train | val | test | total |
|---|---|---|---|---|
| Axis | 20 | 5 | 5 | 30 |
| Canara | 20 | 5 | 5 | 30 |
| ICICI | 20 | 5 | 5 | 30 |
| Syndicate | 20 | 5 | 5 | 30 |

- Formats: 59 PNG, 61 JPEG; extensions match content; 0 corrupted.
- Sizes: 1024×467–476 (79 images) and ~1505–1514×688–704 (41 images).
- Exact duplicates: none. Same filename in two splits: none.
- 16 pairs are near-identical at 8×8 hash level: inspection shows the same blank template filled with
  different text. Test cheques therefore share layouts with training cheques.
- Annotation files: **none**.
- 119 of 120 filenames are the 6-digit cheque number (verified visually on samples from every bank against
  the MICR line); `vaildate/syndicate/image.jpeg` is the exception.

## Handwriting
Payee, amounts, date and account number are typed in machine fonts on these images; the signature is
the only handwriting. The dataset cannot train or evaluate a handwritten-text recognizer, and it has no
transcriptions for PaddleOCR recognition (`rec_gt` line labels) or detection (polygons).

## How it is used
- MICR digit templates: train split only, labels from filenames.
- Field-extraction rules were developed while inspecting train and validation images; the test split was
  evaluated once at the end.
- Evaluation without transcriptions: cheque-number exact match (filename label), account number found in the
  signature registry, amount words-vs-figures self-consistency, date validity, fill rate.

## Building a real labelled set
Every correction saved in the cheque review screen keeps the machine value and the human value side by side.
Export `cheques.csv` from Reports to obtain confirmed field values; for handwriting, collect real handwritten
cheques, crop each field line and record its transcription (≥ 2,000 lines across banks, split by cheque).

## Scoring against human-verified labels
`python manage.py score-ocr --images DIR --labels labels.csv [--private-csv out.csv] [--masked-report out.md]`

`labels.csv` columns: `file` (path relative to `DIR`), `bank_name`, `branch_name`, `ifsc_code`, `cheque_number`,
`micr_code`, `account_number`, `date` (ISO), `payee_name`, `amount_value` (integer rupees), `amount_words`.
Keep the file and `--private-csv` output **outside the repository** (they contain account numbers and names);
`--masked-report` holds only ✓/✗, confidence and review status and is safe to commit. Rules: `app/ocr/scoring.py`.
The command reports field accuracy and cheque accuracy separately, wrong values marked "ok" (the dangerous
case), wrong values caught by review, and correct values sent to review (reviewer workload).

**Use labels a person has verified, and cheques that were not used while tuning the extraction rules.** The
20 test cheques were scored five times while the Phase 1 fixes were written and their failure types had
already been seen, so their numbers are not a blind estimate; a fresh set of 50–100 labelled cheques is needed
for a real accuracy claim.
