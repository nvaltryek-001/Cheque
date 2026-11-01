# Training readiness

| Task | Status | Basis | Action taken |
|---|---|---|---|
| Handwritten field OCR (PaddleOCR fine-tune) | **BLOCKED** | No transcriptions; fields are typed, not handwritten | Not trained. Data collection workflow documented (`docs/OCR_DATASET.md`); in-app corrections are stored as labels |
| Printed field extraction | Rule-based (no training) | No text labels | Developed on train/val; test evaluated once |
| MICR E-13B digits | READY | Filename = cheque number (119 labels) | Trained on train (80 cheques, 480 glyphs, all 10 digits present); evaluated on val and test |
| Signature fusion model | READY (limited) | 120 signers, 1 specimen each, 240 synthetic alterations | Trained on train pairs, thresholds on val, evaluated once on test |
| Signer-specific / Siamese | **BLOCKED** | 1 specimen per signer | Not trained. Requirements documented |

Hardware: CPU only; no GPU needed for any implemented component (signature training ≈ 30 s, MICR < 5 s).

## Data required to unblock
- Handwritten OCR: ≥ 2,000 line crops of real handwritten date/payee/amount fields with verified
  transcriptions, PaddleOCR rec format (`image_path\ttext`), split by cheque and by writer.
- Signer verification: ≥ 5 genuine specimens per signer on different days, ≥ 3 skilled forgeries per signer,
  ≥ 200 signers, signer-disjoint splits, consent for biometric processing.
