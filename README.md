# ChequeVerify AI

**Intelligent Cheque Information Extraction and Signature Verification System**

## 1. Project Overview

ChequeVerify AI is an intelligent cheque processing platform designed to extract cheque information, validate extracted fields and support signature verification.

The project combines OCR, computer vision, machine learning and a web-based application.

## 2. Main Objectives

- Upload cheque images through a web interface.
- Extract important cheque information using OCR.
- Identify bank and branch information.
- Validate cheque data and identify inconsistencies.
- Detect and extract handwritten signatures.
- Compare cheque signatures with authorized specimen signatures.
- Generate structured verification results.
- Support manual review for uncertain cases.

## 3. Technology Stack

### Frontend
- React
- Vite
- TypeScript / JavaScript (as used in the existing source)
- CSS

### Backend
- Python
- Flask
- REST API

### OCR and Image Processing
- PaddleOCR
- OpenCV
- Python image-processing utilities

### Planned Signature Verification
- OpenCV
- Siamese neural network
- Signature embeddings
- Similarity-based comparison

## 4. Cheque Field Extraction

The extraction pipeline supports these ten fields:

1. Bank name
2. Cheque number
3. MICR code
4. Account number
5. Date
6. Payee name
7. Amount in figures
8. Amount in words
9. IFSC code
10. Branch name

Additional response fields such as account holder name and normalized amount may also be provided by the backend.

## 5. Supported Bank Test Groups

The development and testing work covers:

- Axis Bank
- Canara Bank
- ICICI Bank
- Syndicate Bank

Bank-specific parsing and extraction improvements have been implemented.

Previous targeted tests reported:
- Syndicate: five images, 50/50 populated fields.
- Selected Axis and ICICI images: successful targeted API results.
- ICICI image 162954.jpeg: subsequent diagnostic returned all ten target fields.

**Important:** A complete, correctly located 20-image regression and ground-truth accuracy evaluation remain pending. Field completeness is not the same as field-value accuracy.

## 6. Current Implemented Components

- Flask backend application.
- Image upload API.
- OCR processing API.
- PaddleOCR integration.
- Ten-field cheque extraction pipeline.
- Bank identification.
- IFSC extraction improvements.
- Date extraction improvements.
- Amount words extraction improvements.
- MICR parsing improvements.
- Cheque-number parsing improvements.
- Branch extraction improvements.
- Confirmed-label handling.
- Targeted bank-specific regression tests.

## 7. System Workflow

1. Upload cheque image.
2. Process the image through OCR.
3. Extract cheque fields.
4. Validate the extracted information.
5. Detect and crop the signature region.
6. Compare against an authorized specimen signature.
7. Generate a verification result and review reasons.

Steps 5 and 6 are planned integration work.

## 8. Signature Verification - Planned Module

The signature verification module is a major next development milestone.

Planned capabilities:

- Signature-region detection.
- Signature extraction and preprocessing.
- Secure specimen-signature enrollment.
- Feature and embedding extraction.
- Similarity comparison.
- Threshold calibration.
- Genuine / suspicious / review-required classification.
- Human review for uncertain results.

A Siamese neural network is a proposed approach. It requires a suitable genuine and forged signature dataset and independent evaluation.

A similarity score alone cannot establish legal authenticity.

## 9. Remaining Development Roadmap

### Phase 1 - OCR Validation
- Locate the correct 20-image test dataset.
- Run complete four-bank API regression.
- Compare all fields against ground truth.
- Evaluate precision, recall and field-level accuracy.
- Fix remaining bank-specific extraction failures.

### Phase 2 - Signature Verification
- Implement signature-region detection.
- Build signature preprocessing.
- Design authorized signature enrollment.
- Develop signature similarity model.
- Evaluate false acceptance and false rejection rates.

### Phase 3 - Verification Engine
- Integrate OCR and signature results.
- Add cheque consistency checks.
- Add missing-field and suspicious-pattern detection.
- Generate structured verification reports.

### Phase 4 - Frontend
- Complete upload and processing workflow.
- Display extracted fields.
- Display confidence and validation results.
- Add signature comparison results.
- Add review and correction workflow.

### Phase 5 - Finalization
- End-to-end testing.
- Security and privacy review.
- Audit logging.
- Deployment.
- Final documentation.

## 10. Repository Structure

```text
ChequeVerify-AI/
├── README.md
├── .gitignore
├── start.ps1
├── Install-ChequeVerifyAI-ReactTS.ps1
├── backend/
│   ├── app.py
│   ├── api/
│   ├── utils/
│   └── requirements.txt
├── Frontend/
├── training/
├── docs/
├── scripts/
└── reports/
```

This describes the intended organization; the actual repository contents determine which components are present.

## 11. Privacy and Security

- Do not commit real cheque images or private financial information.
- Do not commit API credentials or environment secrets.
- Do not publish private customer or account records.
- Keep model weights and large datasets outside Git unless intentionally approved.
- Use human review for uncertain verification results.

## 12. Project Status

ChequeVerify AI is under active development.

The OCR and backend processing pipeline have been implemented and targeted tests have been performed. Full regression, independent accuracy evaluation, signature verification and complete end-to-end validation remain pending.

## 13. License

A project license should be selected before public distribution.
