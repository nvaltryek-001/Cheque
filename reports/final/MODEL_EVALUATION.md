# Model evaluation

All numbers below were produced by `python manage.py train-micr`, `train-signature` and
`eval-ocr --split {train,val,test}` on 2026-10-31 and are stored next to this file as JSON.
Small test sets → wide uncertainty; no figure here should be read as a production accuracy.

## 1. MICR reader (E-13B template model)
Labels: 6-digit filename stem = first MICR group. Trained on the 80 train cheques only.

| Split | Labelled cheques | Exact cheque-number match |
|---|---|---|
| train (training data – not a generalisation result) | 80 | 80 |
| val | 19 | 19 |
| test | 20 | 20 (95 % Clopper–Pearson lower bound ≈ 83 %) |

The other MICR groups (MICR code, short account, transaction code) have no labels and are reported
without an accuracy figure.

## 2. Field extraction (Tesseract 5.3.4 + region isolation)
Rules were developed while looking at train and validation images; the test split was run once, after
development. There are no transcriptions, so these are the only honest measurements:

| Measure | train (80) | val (20) | **test (20)** |
|---|---|---|---|
| Cheque number exact (filename label) | 80/80 | 19/19 | **20/20** |
| Account number exists in signature registry (same bank) | 70/80 | 16/20 | **18/20** |
| Amount in words = amount in figures (self-consistency) | 72/80 | 18/20 | **17/20** |
| Date read as a valid calendar date | 88.8 % | 85 % | **95 %** |
| Fill rate – bank, payee, amount words/figures, IFSC, MICR | 95–100 % | 100 % | **100 %** |
| Fill rate – branch / account / date | 88.8 / 92.5 / 88.8 % | 85 / 90 / 85 % | **85 / 90 / 95 %** |
| Mean time per cheque (CPU) | 1.4 s | 1.4 s | 1.4 s |

Per-bank test results: amount consistency Axis 4/5, Canara 5/5, ICICI 3/5, Syndicate 5/5; account in
registry Syndicate 3/5 (others 5/5).
Not measurable without labels: payee name CER/WER, amount-in-words WER, date exact match, branch
accuracy. "Valid date" and "consistent amounts" show plausibility, not correctness.
Handwritten-field CER/WER: not applicable (no handwritten fields, no transcriptions).
PaddleOCR baseline comparison: not run (PaddleOCR could not be installed in the build environment).

## 3. Signature comparator
### Protocol
- Pairs per split (signers never cross splits): reference R = the signer's genuine image;
  positive = 3 simulated re-captures of R (rotation ±4°, scale 0.9–1.1, shift, blur, brightness/contrast,
  noise, JPEG 55–92); altered = re-captures of the 2 synthetic alterations of R; impostor = re-captures of
  3 random other signers' genuine images.
- Every query is re-captured. A first experiment without this showed the shortcut: untouched alterations
  look *more* similar than re-captured positives (per-feature AUC ≈ 0.01), so a model would learn
  "untransformed ⇒ altered". That version was discarded.
- Fusion: logistic regression over 8 features, class-balanced, L2, non-negative weights, fit on train
  (640 pairs). Thresholds on validation: match = lowest score with FAR ≤ 5 %, mismatch = highest score
  with FRR ≤ 5 %. Test (160 pairs) scored once with frozen model and thresholds.
- Model version: `fusion-lr-…-ef4c7123` (weights digest); seed 1337.

### Baselines – single features, validation AUC
| Feature | all negatives | vs alterations | vs other signers |
|---|---|---|---|
| SIFT matches | 0.854 | 0.634 | 1.000 |
| ORB matches | 0.778 | 0.539 | 0.938 |
| HOG cosine | 0.675 | 0.454 | 0.822 |
| Chamfer | 0.637 | 0.443 | 0.766 |
| Aspect ratio | 0.623 | 0.495 | 0.708 |
| SSIM | 0.596 | 0.525 | 0.644 |
| Projection profiles | 0.588 | 0.441 | 0.686 |
| Ink density | 0.537 | 0.484 | 0.572 |

Learned weights: SIFT 1.40, ORB 0.22, HOG 0.18, profile 0.17, aspect 0.07, others 0.

### Fused model
| Split | Pairs | ROC-AUC | EER | AUC vs alterations | AUC vs other signers |
|---|---|---|---|---|---|
| train | 640 | 0.862 | 0.241 | 0.660 | 0.997 |
| val | 160 | 0.842 | 0.250 | 0.605 | 1.000 |
| **test** | 160 | **0.864** | **0.232** | **0.663** | **0.998** |

Operating point (validated thresholds: match ≥ 0.843, mismatch ≤ 0.539), test split:

| | predicted MATCH | not MATCH |
|---|---|---|
| same signature (60) | 13 | 47 |
| altered or other signer (100) | 1 | 99 |

FAR 1.0 %, FRR 78.3 %, precision 0.929, recall 0.217, F1 0.351.
Three-way test decisions: positives 13 MATCH / 34 REVIEW / 13 MISMATCH; alterations 1 / 27 / 12;
other signers 0 / 0 / 60.

At the test EER threshold (threshold-free summary only, not an operating point): FAR 23 %, FRR 23 %, F1 0.713.

### Reading these results
- Rejecting a **different person's** signature works well (0 of 60 accepted, AUC ≈ 1.0).
- Detecting **small synthetic stroke alterations** is weak (AUC ≈ 0.66). Thresholds were set for few false
  accepts, so most genuine comparisons land in REVIEW – the system defers to a human instead of guessing.
- Positives are simulated; real signature variation between signing sessions is larger, so real FRR would
  be higher. Results are not evidence of performance against skilled forgeries.
- Scores are similarity scores, not calibrated probabilities.
