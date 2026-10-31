# ChequeVerify AI

## Main folders

- Frontend/     React + Vite user interface and frontend tests
- backend/      Flask API, OCR, quality analysis and verification logic
- new_datset/   Current cheque image dataset
- .venv/        Existing Python environment
- .venv312/     Existing Python 3.12 environment

## Main integration routes

- GET  /api/health
- POST /api/upload
- POST /api/process
- GET  /api/status/<upload_id>
- GET  /api/quality/<upload_id>
- GET  /api/preview/<upload_id>

Check backend/api/routes.py for the exact request and response contracts.
Keep dataset paths configured consistently with the actual folder layout.
