$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
Write-Host "`n=== ChequeVerify AI quick start ===" -ForegroundColor Cyan
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw "Python 3.10-3.12 is required. Install Python and enable 'Add to PATH', then rerun." }
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw "Node.js LTS is required. Install Node.js and rerun." }
if (-not (Test-Path ".venv\Scripts\python.exe")) { python -m venv .venv }
Write-Host "Installing backend dependencies..." -ForegroundColor Yellow
& ".venv\Scripts\python.exe" -m pip install --upgrade pip
& ".venv\Scripts\python.exe" -m pip install -r "backend\requirements.txt"
Write-Host "Installing frontend dependencies..." -ForegroundColor Yellow
Push-Location "frontend"
if (Test-Path "package-lock.json") { npm ci } else { npm install }
Pop-Location
Write-Host "Auditing included dataset manifests..." -ForegroundColor Yellow
& ".venv\Scripts\python.exe" "training\audit_dataset.py"
Write-Host "Starting backend and frontend in separate windows..." -ForegroundColor Green
Start-Process powershell -ArgumentList @("-NoExit", "-Command", "Set-Location '$Root\backend'; & '$Root\.venv\Scripts\python.exe' app.py")
Start-Process powershell -ArgumentList @("-NoExit", "-Command", "Set-Location '$Root\frontend'; npm run dev -- --host 127.0.0.1")
Start-Sleep -Seconds 3
Write-Host "Frontend: http://localhost:5173" -ForegroundColor Cyan
Write-Host "API health: http://localhost:5000/api/health" -ForegroundColor Cyan
Start-Process "http://localhost:5173"
