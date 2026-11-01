# Dataset validation report

Date: 2026-10-31 · Branch `chequeverify-v2`, local commit `fcc5b17` (the six unpushed commits) · Nothing pushed, merged or deployed.
Engine: Tesseract 5 + bundled E-13B MICR model (`micr_e13b.npz`); signature model `signature_fusion.json` with the alteration check.

This report contains **no cheque images, account numbers, payee names, amounts or signature images**.
Cheques are identified as T01–T20 and signers by their dataset ID (`person_NNN`). The full table with
ground-truth and predicted values, the label file and the evaluation scripts are kept outside the repository.

## Key findings
1. **OCR, 20 test cheques:** 183 of 200 fields correct (91.5 %, Wilson 95 % CI 86.8–94.6 %). Only 8 of 20
   cheques have all 10 fields correct (40 %, CI 21.9–61.3 %), and every cheque has at least one field sent to review.
   Two wrong values are marked "ok" (both payee names); the other 15 errors are flagged or missing.
2. **Ground truth is not independent.** The labels were transcribed by Claude from the images, not by a person.
   All 17 disagreements were re-checked against the image. One label was wrong and has been corrected (T02 branch).
   The labels still need human verification.
3. **No signer has an independent genuine signature.** Each of the 120 signers has one genuine image, and it is a
   crop of the signature on that signer's only cheque. **Genuine acceptance rate and false rejection rate cannot be
   measured with this data, so they are not reported.** The earlier "genuine re-capture" figures in
   `MODEL_EVALUATION.md` are the same image re-captured and must not be read as a genuine acceptance rate.
4. **There are no skilled forgeries.** The 240 "forged" images are script-generated edits of the genuine images.
   Skilled-forgery rejection cannot be measured.
5. **Measurable results:** random-impostor signatures from other signers' cheques were never accepted
   (0 of 380 test pairs; 0 of 14,280 pairs over all 120 signers). Synthetic alterations submitted unchanged were
   accepted 2 of 40 times on test signers (5 %) and 16 of 240 times over all signers (6.7 %).
6. About 80 of the 120 "genuine signatures" look like names typed in a script font rather than handwritten.
   This is a visual impression, not verified. Results on these images say little about real handwritten signatures.

This data does **not** support a claim of production readiness for signature verification.

---

## Part A – Cheque OCR test set

### A1. Images
| Check | Result |
|---|---|
| Test images present | 20 (Axis 5, Canara 5, ICICI 5, Syndicate 5) |
| Decode with Pillow `verify()` and OpenCV | 20 / 20 |
| Byte-identical duplicates within test | 0 |
| Formats / sizes | PNG 11, JPEG 9; 1514×704 (6), 1024×469 (5), 1024×476 (5), 1508×688 (3), 1505×688 (1) |
| Images with a label row | 20 / 20 (no unlabelled or extra images) |
| Overlap with train/validation | none by content (`OCR_DATASET_AUDIT.md`); same blank cheque templates recur across splits |

All filled-in text on the test cheques is machine-printed; there is no handwriting to evaluate.

### A2. Ground-truth labels
- Ten fields per cheque, 200 values, transcribed from the images by Claude during the production audit.
- Each of the 17 values where OCR and label disagree was re-checked against the image:
  - **1 label error found and corrected.** T02's branch is printed "MEHIDPATHAM"; the label said "MEHDIPATHAM"
    and the OCR was right.
  - **2 printed-text cases**, scored strictly against the printed text:
    - T02: the cheque prints an IFSC with "8" where the official Axis prefix has "B". The OCR outputs the
      official prefix and marks the field for review.
    - T04: the amount in words is printed "lack". The OCR normalises it to "Lac" and marks it for review.
    - If these two were accepted as equivalent, the total would be 185/200.
  - 14 disagreements are real OCR errors.
- In the 183 fields where OCR and label agree, both would have had to make the same mistake for an error to go
  unseen. That is unlikely but not ruled out, because the same model family produced both.
- **Labels must still be verified by a person.**

Scoring rules:
- Bank, branch and payee are compared after upper-casing and removing punctuation and extra spaces.
- Amount in figures is compared as a parsed number.
- Amount in words is compared word by word, ignoring "ONLY".
- IFSC, cheque number, MICR, account number and date (ISO) must match exactly.
- Review status comes from the application:
  - `ok`: confidence ≥ 0.75 and no validation issue.
  - `rev`: review.
  - `miss`: no value.

### A3. Per-cheque results
Each cell shows: correct (✓/✗), confidence, review status. Values are withheld here.

| Cheque | Issuer | Bank | Branch | IFSC | Chq no. | MICR | Account | Date | Payee | Amt (fig) | Amt (words) | Correct |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| T01 | Axis | ✓ 0.95 ok | ✓ 0.80 rev | ✓ 0.92 rev | ✓ 0.89 ok | ✓ 0.89 ok | ✓ 0.55 rev | ✓ 0.96 ok | ✓ 0.85 ok | ✓ 0.90 ok | ✓ 0.90 ok | 10/10 |
| T02 | Axis | ✓ 0.95 ok | ✓ 0.80 rev | ✗ 0.93 rev | ✓ 0.82 ok | ✓ 0.82 ok | ✓ 0.71 rev | ✓ 0.96 ok | ✗ 0.66 rev | ✓ 0.90 ok | ✓ 0.90 ok | 8/10 |
| T03 | Axis | ✓ 0.95 ok | ✓ 0.80 rev | ✓ 0.93 ok | ✓ 0.92 ok | ✓ 0.92 ok | ✓ 0.92 ok | ✓ 0.00 rev | ✓ 0.55 rev | ✓ 0.90 ok | ✓ 0.90 ok | 10/10 |
| T04 | Axis | ✓ 0.95 ok | ✓ 0.80 rev | ✓ 0.92 ok | ✓ 0.91 ok | ✓ 0.91 ok | ✓ 0.72 rev | ✓ 0.96 ok | ✓ 0.57 rev | ✗ 0.57 rev | ✗ 0.76 rev | 8/10 |
| T05 | Axis | ✓ 0.95 ok | ✓ 0.80 rev | ✓ 0.93 ok | ✓ 0.90 ok | ✓ 0.90 ok | ✓ 0.25 rev | ✓ 0.00 rev | ✓ 0.81 ok | ✓ 0.90 ok | ✓ 0.90 ok | 10/10 |
| T06 | Canara | ✓ 0.95 ok | ✗ 0.00 miss | ✓ 0.31 rev | ✓ 0.90 ok | ✓ 0.90 ok | ✓ 0.96 ok | ✓ 0.97 ok | ✓ 0.96 ok | ✓ 0.90 ok | ✓ 0.96 ok | 9/10 |
| T07 | Canara | ✓ 0.95 ok | ✗ 0.00 miss | ✓ 0.60 rev | ✓ 0.85 ok | ✓ 0.85 ok | ✓ 0.80 ok | ✓ 0.96 ok | ✓ 0.97 ok | ✓ 0.90 ok | ✓ 0.96 ok | 9/10 |
| T08 | Canara | ✓ 0.95 ok | ✗ 0.00 miss | ✓ 0.60 rev | ✓ 0.87 ok | ✓ 0.87 ok | ✓ 0.96 ok | ✓ 0.96 ok | ✓ 0.96 ok | ✓ 0.90 ok | ✓ 0.96 ok | 9/10 |
| T09 | Canara | ✓ 0.95 ok | ✗ 0.00 miss | ✓ 0.68 rev | ✓ 0.85 ok | ✓ 0.85 ok | ✓ 0.96 ok | ✓ 0.96 ok | ✓ 0.96 ok | ✓ 0.90 ok | ✓ 0.96 ok | 9/10 |
| T10 | Canara | ✓ 0.95 ok | ✗ 0.00 miss | ✓ 0.35 rev | ✓ 0.84 ok | ✓ 0.84 ok | ✓ 0.97 ok | ✓ 0.96 ok | ✓ 0.96 ok | ✓ 0.90 ok | ✓ 0.97 ok | 9/10 |
| T11 | ICICI | ✓ 0.95 ok | ✓ 0.80 rev | ✓ 0.90 rev | ✓ 0.87 ok | ✓ 0.87 ok | ✓ 0.49 rev | ✗ 0.00 rev | ✗ 0.80 ok | ✓ 0.90 ok | ✓ 0.94 ok | 8/10 |
| T12 | ICICI | ✓ 0.95 ok | ✓ 0.80 rev | ✓ 0.91 ok | ✓ 0.84 ok | ✓ 0.84 ok | ✓ 0.29 rev | ✗ 0.00 miss | ✓ 0.95 ok | ✗ 0.41 rev | ✓ 0.88 rev | 8/10 |
| T13 | ICICI | ✓ 0.95 ok | ✓ 0.80 rev | ✓ 0.92 ok | ✓ 0.81 ok | ✓ 0.81 ok | ✓ 0.32 rev | ✓ 0.96 ok | ✓ 0.82 ok | ✓ 0.90 ok | ✓ 0.94 ok | 10/10 |
| T14 | ICICI | ✓ 0.95 ok | ✓ 0.80 rev | ✓ 0.92 ok | ✓ 0.75 ok | ✓ 0.75 ok | ✓ 0.96 ok | ✓ 0.00 rev | ✗ 0.85 ok | ✗ 0.44 rev | ✓ 0.95 rev | 8/10 |
| T15 | ICICI | ✓ 0.95 ok | ✓ 0.80 rev | ✓ 0.68 rev | ✓ 0.74 rev | ✓ 0.74 rev | ✓ 0.28 rev | ✓ 0.00 rev | ✓ 0.92 ok | ✓ 0.90 ok | ✓ 0.94 ok | 10/10 |
| T16 | Syndicate | ✓ 0.95 ok | ✓ 0.80 rev | ✓ 0.88 ok | ✓ 0.81 ok | ✓ 0.81 ok | ✓ 0.81 ok | ✓ 0.96 ok | ✓ 0.95 ok | ✓ 0.90 ok | ✓ 0.90 ok | 10/10 |
| T17 | Syndicate | ✓ 0.95 ok | ✓ 0.67 rev | ✓ 0.69 rev | ✓ 0.78 ok | ✓ 0.78 ok | ✗ 0.00 miss | ✓ 0.96 ok | ✓ 0.97 ok | ✓ 0.90 ok | ✓ 0.90 ok | 9/10 |
| T18 | Syndicate | ✓ 0.95 ok | ✓ 0.63 rev | ✓ 0.78 ok | ✓ 0.85 ok | ✓ 0.85 ok | ✓ 0.30 rev | ✓ 0.97 ok | ✓ 0.83 ok | ✓ 0.90 ok | ✓ 0.90 ok | 10/10 |
| T19 | Syndicate | ✓ 0.95 ok | ✓ 0.56 rev | ✓ 0.83 ok | ✓ 0.79 ok | ✓ 0.79 ok | ✗ 0.00 miss | ✓ 0.97 ok | ✓ 0.96 ok | ✓ 0.90 ok | ✓ 0.90 ok | 9/10 |
| T20 | Syndicate | ✓ 0.95 ok | ✓ 0.80 rev | ✓ 0.66 rev | ✓ 0.81 ok | ✓ 0.81 ok | ✓ 0.72 rev | ✓ 0.96 ok | ✓ 0.93 ok | ✓ 0.90 ok | ✓ 0.90 ok | 10/10 |

### A4. Field-level accuracy
| Field | Correct | ok | review | missing | Wrong but "ok" | Correct but sent to review | Mean conf. |
|---|---|---|---|---|---|---|---|
| Bank name | 20/20 | 20 | 0 | 0 | 0 | 0 | 0.95 |
| Branch name | 15/20 | 0 | 15 | 5 | 0 | 15 | 0.57 |
| IFSC | 19/20 | 9 | 11 | 0 | 0 | 10 | 0.77 |
| Cheque number | 20/20 | 19 | 1 | 0 | 0 | 1 | 0.84 |
| MICR code | 20/20 | 19 | 1 | 0 | 0 | 1 | 0.84 |
| Account number | 18/20 | 8 | 10 | 2 | 0 | 10 | 0.60 |
| Date | 18/20 | 14 | 5 | 1 | 0 | 4 | 0.67 |
| Payee name | 17/20 | 17 | 3 | 0 | **2** | 2 | 0.86 |
| Amount (figures) | 17/20 | 17 | 3 | 0 | 0 | 0 | 0.84 |
| Amount (words) | 19/20 | 17 | 3 | 0 | 0 | 2 | 0.92 |
| **Total** | **183/200 (91.5 %)** | 140 | 52 | 8 | **2** | 45 | |

By bank: Axis 46/50, Canara 45/50, ICICI 44/50, Syndicate 48/50.
Fields are not independent: five cheques per bank share one template. The interval above is therefore optimistic.

### A5. Cheque-level accuracy
| Measure | Result |
|---|---|
| All 10 fields correct | **8 / 20** (40 %) |
| 9 of 10 correct / 8 of 10 correct | 7 / 5 |
| All 10 fields correct **and** marked ok (could be accepted without review) | **0 / 20** |
| Every error caught (flagged, missing, or no error) | 18 / 20 |
| Contains a wrong value marked ok | 2 / 20 (T11, T14) |

### A6. Review routing
- Of the 140 fields marked **ok**, 138 are correct; 2 are wrong.
- Of the 17 wrong fields, 15 were sent to review or left empty (8 empty); 2 were not.
- 45 of the 183 correct fields were also sent to review: most branch names, IFSC on Canara and some account numbers.
  This costs reviewer time but is not an error.

### A7. Incorrect fields
Values that would identify a person, account or amount are described, not shown.

| Cheque | Field | Status | What went wrong |
|---|---|---|---|
| T02 | IFSC | review | printed IFSC misprint ("8" for "B"); OCR returned the official prefix (scored strictly as wrong) |
| T02 | Payee | review | a few character errors (one letter inserted, one replaced by punctuation) |
| T04 | Amount (figures) | review | only the first digit group read |
| T04 | Amount (words) | review | printed misspelling normalised (scored strictly as wrong) |
| T06–T10 | Branch | missing | Canara branch line not found (same template on all five) |
| T11 | Date | review | boxed-digit date misread (wrong year/month) |
| T11 | Payee | **ok** | surname mis-read; **silent error** |
| T12 | Date | missing | boxed-digit date not read |
| T12 | Amount (figures) | review | only the first digit group read |
| T14 | Payee | **ok** | one letter dropped; **silent error** |
| T14 | Amount (figures) | review | only the first digit group read |
| T17, T19 | Account number | missing | spaced digits on Syndicate template not read |

In all three amount-figure failures the amount in words was read correctly. The amount-consistency check
caught each one and sent it to review.

Speed: 31 s for 20 cheques (≈ 1.55 s per cheque, single CPU process).

---

## Part B – Signature dataset

### B1. Inventory (the seven questions)
| # | Question | Answer (measured) |
|---|---|---|
| 1 | Enrolled signers | **120** (train 80 · validation 20 · test 20; 30 per bank) |
| 2 | Genuine samples per signer | **1** for every signer (120 images). Each one is a crop of the signature on that signer's only cheque (B2), so there are **0 independent genuine samples** |
| 3 | Skilled forgeries | **0.** The 240 "forged" images (2 per signer) come from `generate_forged_signatures.py`, which adds random strokes and/or erases regions on the genuine image |
| 4 | Random impostor samples | No dedicated set. Impostors can be built from other signers' signatures: 120 × 119 = 14,280 pairs (380 within the test split) |
| 5 | Independent signing sessions? | **No.** The one genuine image and the cheque signature are the same signing |
| 6 | Evaluation samples duplicated in enrollment? | The only possible "genuine" queries (the cheque signature, or a re-capture of the specimen) are the enrollment image itself. No byte-identical files (360 hashed). Of the 240 synthetic alterations, 9 differ from their genuine image in no pixel by more than 60 grey levels, and 23 more in under 0.2 % of pixels |
| 7 | Train/validation/test overlap | None by signer ID, file hash or perceptual hash (closest pair of different signers differs in 11 of 128 bits; the app treats ≤ 6 as a duplicate). **However**, `person_038` (train) and `person_099` (test) carry the same written name in different styles, as do `person_032` and `person_068` (both train). Whether these are the same person cannot be told from the data |

Other facts:
- Every manifest row has a decodable file; no files are unlisted.
- Every alteration is in the same split and bank as its target.
- Specimen images show cheque background colour and printed captions, consistent with being cropped from cheque scans.
- About 80 of the 120 genuine images look like full names set in script fonts. The other ~40 look hand-drawn.
  Five of the 20 test signers are in the hand-drawn group. This is a visual impression, not verified.

### B2. Evidence that each genuine image was cut from its cheque
1. **Account numbers.** Genuine filenames are account numbers. On the 40 labelled validation and test cheques,
   each account number appears on exactly one cheque, and that cheque is in the same split as the signer: 40 of 40.
2. **Template matching.** Each specimen was matched against all 120 cheques at 33 scales (0.5–2.5×). The best
   cheque is in the specimen's own split for 120 of 120, and the 120 best matches are 120 different cheques.
3. **Stroke alignment.**
   - Method: the app's alteration-check alignment (ORB + RANSAC), between the signature auto-cropped from that
     cheque and the specimen.
   - Own specimen: aligned for all 120, with 46–546 inliers (median 238); median ink agreement 0.976.
   - Another signer's specimen from the same bank: aligned for only 2 of 120.
   - A separate signing would not align stroke-for-stroke like this, so these are the same signing.

Because of this, **no evaluation below uses the cheque signature, or any version of the enrolled image, as a genuine query.**

### B3. Evaluation design
- **Enrollment:** each signer's one genuine image, exactly as the app enrolls a specimen.
- **Decision:** `decide()` with the bundled fusion model and the alteration check. Outcomes are MATCH, REVIEW or
  MISMATCH; REVIEW counts as inconclusive.
- **Groups:**
  - The *test split* (20 signers) is the clean result: these signers were never used for fitting or threshold choice.
  - *All 120* is also reported because you asked for it. Train signers were used to fit the fusion model and the
    alteration thresholds, and validation signers to choose the decision thresholds, so the all-120 numbers are optimistic.
- **Query sets:**

| Query set | How it was built | Valid? |
|---|---|---|
| Genuine acceptance / false rejection | needs a second, independent genuine signature | **Not measurable – 0 of 120 signers qualify** |
| Random impostor | another signer's signature, auto-cropped from that signer's cheque, against the target's enrolled image | Yes (zero-effort impostors only) |
| Skilled forgery | needs forgeries written by another person imitating the signer | **Not measurable – none in the dataset** |
| Synthetic alteration, as submitted | the dataset's altered image against the target | Yes, for synthetic edits only |
| Synthetic alteration, re-captured | same, after simulated rotation, scale, blur, contrast, noise and JPEG | Yes, for synthetic edits only |

### B4. Results
| Query set | Test split (20 signers) | All 120 signers (optimistic) |
|---|---|---|
| Genuine acceptance rate | **not measurable** | **not measurable** |
| False rejection rate | **not measurable** | **not measurable** |
| Random impostor, same bank – MATCH / REVIEW / MISMATCH | 0 / 0 / 80 | 0 / 0 / 3,480 |
| Random impostor, other bank – MATCH / REVIEW / MISMATCH | 0 / 0 / 300 | 0 / 0 / 10,800 |
| **Random impostor false acceptance** | **0 / 380** (95 % CI upper bound 1.0 %) | 0 / 14,280 (upper bound 0.03 %) |
| Skilled forgery rejection | **not measurable** | **not measurable** |
| Synthetic alteration as submitted – MATCH / REVIEW / MISMATCH | **2 / 17 / 21** of 40 | 16 / 97 / 127 of 240 |
| Synthetic alteration re-captured – MATCH / REVIEW / MISMATCH | 2 / 9 / 29 of 40 | 11 / 75 / 154 of 240 |
| **Alteration accepted (false acceptance)** | 5.0 % as submitted (CI 1.4–16.5 %); 5.0 % re-captured | 6.7 % (CI 4.1–10.6 %); 4.6 % |
| Alteration rejected outright | 52.5 % as submitted; 72.5 % re-captured | 52.9 %; 64.2 % |
| Inconclusive (REVIEW) rate | impostors 0 %; alterations 42.5 % / 22.5 % | impostors 0 %; alterations 40.4 % / 31.3 % |

Notes on these results:
- Impostor pairs are not independent: each query signature is reused against 19 targets, or 119 for all 120.
- The impostors are *different names*, mostly in different fonts. 0 accepts here does not mean a person
  imitating a signature would be rejected.
- A pooled false-acceptance rate over all negatives (test: 4 of 460 = 0.87 %) mixes easy impostors with
  alterations, so it is shown only here, for completeness.

### B5. Failed cases
**Accepted synthetic alterations, test split** (score ≥ match threshold 0.8431, alteration check did not fire):

| Target | Alteration file | Submission | Score | Pixels changed |
|---|---|---|---|---|
| person_095 | `…_forged_1` | as submitted | 0.995 | 0.99 % |
| person_097 | `…_forged_1` | as submitted | 0.997 | 0.39 % |
| person_097 | `…_forged_1` | re-captured | 0.906 | 0.39 % |
| person_093 | `…_forged_1` | re-captured | 0.851 | 0.02 % |

- Both as-submitted misses are **thin straight strokes drawn across existing writing** (checked visually).
  They are clearly visible to a person but too thin and too overlapped with real ink for the alteration check's
  difference threshold.
- All 16 as-submitted accepts over the 120 signers have a visible edit (≥ 0.2 % of pixels changed), so these are
  genuine misses, not undetectable images.
- The 9 alterations with no changed pixel all ended in REVIEW (scores 0.991–0.998), each through the
  near-pixel-identical-copy rule (re-run and checked individually).
- Note: `MODEL_EVALUATION.md` counted "25 of 240 differ only by JPEG noise", using a different criterion.
  This report measures 9 with no pixel changed by more than 60 grey levels, plus 23 with under 0.2 % changed.

**Not testable at all:**
- Behaviour on a genuine signature from a different signing session.
- Behaviour on skilled forgeries.

Because the fusion model was trained, and its MATCH threshold chosen, with positives that are re-captures of the
same image, it is **likely, but unmeasured**, that real genuine signatures would often fall below the MATCH
threshold and land in REVIEW or MISMATCH.

### B6. What the dataset cannot support
- Any statement of genuine acceptance rate, false rejection rate, equal error rate or skilled-forgery rejection.
- Any claim about handwritten signatures in general: about two-thirds of the specimens appear to be typeset names.
- The "genuine re-capture" rows in `MODEL_EVALUATION.md` §3 and §3b (for example 4 / 45 / 11 of 60). These are
  the enrolled image itself, slightly transformed. They are a pipeline-consistency check, not a genuine acceptance rate.

### B7. Data needed for a valid signature evaluation
- At least 5 genuine signatures per signer, written in separate sessions (ideally on different days), with
  reference and query images kept apart.
- Skilled forgeries: another person imitating each signer after seeing the genuine signature (for example 5 per signer).
- Collected under consent, labelled by a person, and split by signer before any training.

---

## Part C – Method, reproducibility and privacy
- Scripts (kept outside the repository):
  - `ocr_validate.py`: integrity check and per-field scoring.
  - `provenance.py`, `prov2.py`: specimen-to-cheque template matching.
  - `same_source.py`: alignment evidence.
  - `sig_inventory.py`: counts, hashes, duplicates.
  - `sig_eval.py`: decisions.
  - Fixed seed `dataset-validation-2026-10-31` for re-captures.
- Private outputs, not in git:
  - Full 200-row table with ground-truth and predicted values (`OCR_TEST_FIELD_TABLE_PRIVATE.csv`).
  - Corrected label file.
  - Per-pair decisions and provenance mapping.
- Code evaluated: the six local commits ending `fcc5b17`; no code was changed for this report.
- Numbers in this report that update earlier documents:
  - OCR test 183/200 (was 182/200 before the label correction).
  - The genuine-signature figures are withdrawn as a genuine acceptance rate.
- Production readiness: **not claimed.**
  - OCR still needs human-verified labels and fixes for the Canara branch, ICICI dates, Syndicate account numbers
    and payee mis-reads marked ok.
  - Signature verification has never been tested on an independent genuine signature or a skilled forgery.
