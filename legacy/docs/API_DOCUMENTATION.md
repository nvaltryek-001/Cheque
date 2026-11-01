# API Documentation

The following endpoints have been observed in the project workflow:

- GET /api/health
- POST /api/upload
- POST /api/process

The processing request has used:
- upload_id
- run_ocr
- run_vlm
- run_verification

Example processing options:
- run_ocr: true
- run_vlm: false
- run_verification: false

Inspect backend/api/routes.py for the authoritative schemas, validation rules and response formats before publishing detailed API contracts.
