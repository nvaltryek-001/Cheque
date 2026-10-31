# Final project completion report

## Delivered
A working web application (Flask API + React/TypeScript UI) with:
- Cheque OCR: ten fields, confidence and review status per field, validation checks, quality assessment,
  automatic + manual signature cropping, correction workflow that preserves machine output, batch upload,
  PDF reports.
- A trained E-13B MICR reader: cheque number correct on 20 of 20 held-out test cheques.
- Signature enrollment with full CRUD and safeguards, and signature verification against all active
  specimens with explained MATCH / REVIEW / MISMATCH decisions and manual review.
- Dataset audits, training readiness, background training/evaluation jobs, analytics, history, CSV exports,
  users/roles, settings and an audit trail.
- 54 automated backend tests and a 19-step browser test, all passing in the build environment.
- Documentation and the reports in this folder.

## Honest status
| Item | Status |
|---|---|
| Handwritten-field OCR | Not trained – the data has no handwriting and no labels |
| Field accuracy beyond cheque number | Not measurable with this data; plausibility metrics reported |
| Signature verification vs other people's signatures | Works well on this data (0/60 false accepts on test) |
| Detection of small alterations | Weak (AUC ≈ 0.66); conservative thresholds send most cases to REVIEW |
| Signer-specific / deep model | Blocked by data (1 specimen per signer) |
| Production banking security | Not claimed – see SECURITY_AUDIT.md residual risks |
| Vite build, PaddleOCR, Waitress, Windows script | Written, not executable in the build environment – run `.\start.ps1 -Test` |

No metric in this project is reported as 100 % accuracy; perfect scores on 19–20 test images are reported
as counts with their confidence bound.

## Recommended next steps
1. Run `.\start.ps1 -Setup` and `.\start.ps1 -Test` on the Windows machine; report any error.
2. Point `CV_OCR_ENGINE=paddle` and compare `python manage.py eval-ocr --split test` with the Tesseract numbers here.
3. Start collecting real data: several specimens per signer and real handwritten cheques; staff corrections
   in the review screen already form a labelled set.
4. Before any real deployment: HTTPS reverse proxy, SSO/MFA, encrypted storage, retention policy, pen test.
