<#
  ChequeVerify AI – Windows launcher
  .\start.ps1            build the frontend (if needed) and serve everything on http://127.0.0.1:5000
  .\start.ps1 -Dev       backend on :5000 + Vite dev server on :5173 (hot reload) in two windows
  .\start.ps1 -Setup     create/refresh the Python venv and install npm packages, then exit
  .\start.ps1 -Test      run backend tests and the frontend type-check + build
#>
param([switch]$Dev, [switch]$Setup, [switch]$Test, [int]$Port = 5000)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Venv = if (Test-Path "$Root\.venv312\Scripts\python.exe") { "$Root\.venv312" } else { "$Root\.venv" }
$Py = "$Venv\Scripts\python.exe"

function Ensure-Python {
  if (-not (Test-Path $Py)) {
    Write-Host "Creating virtual environment in $Venv"
    try { py -3.12 -m venv $Venv } catch { }
    if (-not (Test-Path $Py)) { python -m venv $Venv }
    if (-not (Test-Path $Py)) { throw "Python 3.10+ not found - install it from https://www.python.org" }
  }
  & $Py -m pip install --disable-pip-version-check -q -r "$Root\backend\requirements.txt"
}
function Ensure-Node {
  if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { throw "Node.js/npm not found - install Node 18+ from https://nodejs.org" }
  if (-not (Test-Path "$Root\Frontend\node_modules")) { Push-Location "$Root\Frontend"; npm install; Pop-Location }
}

if ($Setup) { Ensure-Python; Ensure-Node; Write-Host "Setup complete."; exit 0 }

if ($Test) {
  Ensure-Python; Ensure-Node
  Push-Location "$Root\backend"; & $Py -m pytest -q; $b = $LASTEXITCODE; Pop-Location
  Push-Location "$Root\Frontend"; npm run build; $f = $LASTEXITCODE; Pop-Location
  if ($b -ne 0 -or $f -ne 0) { Write-Host "TESTS FAILED (backend=$b frontend=$f)" -ForegroundColor Red; exit 1 }
  Write-Host "All tests passed." -ForegroundColor Green; exit 0
}

Ensure-Python
if ($Dev) {
  Ensure-Node
  Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$Root\backend'; & '$Py' run.py --port $Port"
  Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$Root\frontend'; npm run dev"
  Write-Host "Backend: http://127.0.0.1:$Port   Frontend (dev): http://localhost:5173"
  exit 0
}

if (-not (Test-Path "$Root\Frontend\dist\index.html")) {
  Ensure-Node
  Push-Location "$Root\Frontend"; npm run build; Pop-Location
}
Push-Location "$Root\backend"
Write-Host "Open http://127.0.0.1:$Port  (Ctrl+C to stop)"
& $Py run.py --prod --port $Port
Pop-Location
