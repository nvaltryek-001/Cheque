# ChequeVerify AI — clean quick-start

A local research prototype for cheque-image quality assessment and a confidence-aware review workflow. No login is included. The frontend supports light/dark themes.

## Important dataset status
The supplied ZIP contains train/validation/test CSV manifests and signature metadata, but **does not contain the referenced cheque image files** under `datasets/raw/` or `datasets/synthetic/`. The original manifests are preserved under `dataset/splits/` and `dataset/metadata/`. Do not claim trained accuracy until the referenced images and trustworthy labels are present. This app can still upload images and run the existing image-quality analyzer.

## Quick start (Windows PowerShell)
1. Extract this project folder to `N:\ChequeVerifyAI`.
2. Open PowerShell in `N:\ChequeVerifyAI`.
3. Run `Set-ExecutionPolicy -Scope Process Bypass` then `./start.ps1`.
4. Frontend: `http://localhost:5173`; API health: `http://localhost:5000/api/health`.

The start script creates a Python virtual environment, installs backend dependencies and frontend packages, then opens separate terminal windows.

## Dataset files included
- `dataset/splits/train.csv`
- `dataset/splits/validation.csv`
- `dataset/splits/test.csv`
- `dataset/metadata/idrbt_manifest.csv`
- `dataset/signatures/manifest.csv`

The CSV path values are preserved from the supplied archive. The image files themselves were not in that archive. The dataset audit script will identify missing paths.

## Project layout
- `backend/` Flask API and image quality analyzer
- `frontend/` React + Vite dashboard
- `services/` existing model service integrations
- `dataset/` provided manifests and split reports
- `training/` dataset audit and baseline training preparation
- `reports/` audit output

## Run manually
Backend: `cd backend; ..\.venv\Scripts\python.exe app.py`
Frontend: `cd frontend; npm run dev -- --host 127.0.0.1`

## Limitations
- The quality score assesses image readability, not cheque authenticity.
- OCR/VLM endpoints and trained verification weights are not provided as working model services in the ZIP. The dashboard therefore avoids fabricating an authentic/forged verdict.
- Do not upload real bank account information to a public deployment. This prototype is intended for local research and manual review.
