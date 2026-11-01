# Guided build sequence

## Phase A — launch the UI and quality API
Run `start.ps1`. Upload a sample image. Confirm quality metrics are returned from `/api/upload`. Switch light/dark mode in the header. No login is present.

## Phase B — audit the supplied dataset
Run `..\.venv\Scripts\python.exe training\audit_dataset.py` from the project root. Review `reports\dataset_audit.json`. The audit checks manifest paths and split counts.

## Phase C — make training possible
Place only the original dataset files referenced by the provided manifests into their expected paths. Keep train/validation/test separation and ensure all derivatives of a parent cheque stay in the same split. Do not create fake labels to inflate performance.

## Phase D — train and evaluate
Before training, confirm the dataset has both genuine and forged labels, enough independent cheque/account identities, and image files for every manifest row. Add a baseline classifier only after this validation passes. Report precision, recall, F1, ROC-AUC, confusion matrix and a separate bank-held-out result where the data permits.

## Phase E — OCR and verification
Configure a local OCR engine and a separately trained signature comparison model. Use an uncertainty band (for example, low confidence -> manual review) selected on validation data, not the test set. Do not call an image authentic based on quality score alone.
