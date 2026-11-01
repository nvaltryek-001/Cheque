# Signature dataset

Location: `signature/` (configurable with `CV_SIGNATURE_DATASET`; the folder containing
`genuine_signatures.csv` is found up to three levels deep). Manifests are read with `utf-8-sig` (they
start with a BOM) and are never modified.

| Manifest | Rows | Columns |
|---|---|---|
| `genuine_signatures.csv` | 120 | filename, bank, signer_id, split |
| `forged_signatures.csv` | 240 | filename, bank, target_signer_id, split |

Verified facts (from `reports/final/SIGNATURE_DATASET_AUDIT.json`):
- 120 signers, exactly 1 genuine specimen each; 2 synthetic alterations per signer.
- Splits by signer: 80 train / 20 val / 20 test (20/5/5 per bank); no signer appears in two splits.
- Every manifest row has an existing, decodable image; no unlisted files; no duplicate filenames or
  byte-identical images; alterations are in the same split and bank as their target.
- Formats: genuine 116 JPEG + 4 PNG, altered 232 JPEG + 8 PNG. Genuine size 76–647 × 31–152 px (median 293×73).
- Genuine filenames are the account numbers printed on the matching cheques; specimens were cropped from
  those cheque scans.
- `generate_forged_signatures.py` shows the alterations are random added strokes and/or erased regions
  applied to the genuine image – synthetic edits, not forgeries by another person.

## What the data supports
| Task | Status |
|---|---|
| Genuine vs synthetic alteration baseline | Feasible, with caveats |
| Different-signer rejection | Feasible |
| Signer-specific trained model | **Blocked** – one specimen per signer |
| Siamese / metric learning | **Blocked** – no within-signer variation, no real forgeries |
| Independent evaluation | Limited – disjoint signers, but positive pairs must be simulated. Genuine acceptance and skilled-forgery rejection are **not measurable**: see `reports/final/SIGNATURE_EVALUATION_STATUS.md` and `docs/SIGNATURE_EVALUATION_PROTOCOL.md` |

## Data needed to unblock signer-specific learning
≥ 5 genuine specimens per signer collected on different days (≥ 3 days for signers used to evaluate), ≥ 3 skilled
forgeries per signer by ≥ 2 other people, ≥ 200 signers, splits by signer, consent and secure storage for biometric
data. Collection and evaluation rules: `docs/SIGNATURE_ENROLLMENT.md`, `docs/SIGNATURE_EVALUATION_PROTOCOL.md`.
