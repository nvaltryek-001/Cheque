# ChequeVerify AI - Safe Automated Progress Audit
# Run from PowerShell. This script does not modify dataset images or CSV splits.
# It checks project structure, dataset availability, Python dependencies, frontend build,
# Git status, model artifacts, and backend health if the server is already running.

$ErrorActionPreference = "Continue"
$Project = "N:\Cheque\ChequeA"
$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$ReportDir = Join-Path $Project "reports\automated_audit"
$ReportJson = Join-Path $ReportDir "audit_$Timestamp.json"
$ReportTxt = Join-Path $ReportDir "audit_$Timestamp.txt"

if (-not (Test-Path $Project)) {
    Write-Host "Project folder not found: $Project" -ForegroundColor Red
    Write-Host 'Edit the $Project variable at the top of this script if your project is elsewhere.'
    exit 1
}

New-Item -ItemType Directory -Path $ReportDir -Force | Out-Null
$Results = [System.Collections.Generic.List[object]]::new()

function Add-Check {
    param([string]$Area, [string]$Name, [string]$Status, [string]$Details)
    $item = [pscustomobject]@{
        Area = $Area
        Check = $Name
        Status = $Status
        Details = $Details
    }
    $Results.Add($item)
    $color = switch ($Status) {
        "PASS" { "Green" }
        "WARN" { "Yellow" }
        "BLOCKED" { "Red" }
        default { "Gray" }
    }
    Write-Host ("[{0}] {1}: {2} - {3}" -f $Status, $Area, $Name, $Details) -ForegroundColor $color
}

Write-Host "`n=== CHEQUEVERIFY AI AUTOMATED AUDIT ===" -ForegroundColor Cyan
Write-Host "Project: $Project"
Write-Host "Started: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')`n"

# 1. Structure
$requiredPaths = @(
    "frontend\package.json",
    "frontend\src\App.jsx",
    "backend\app.py",
    "backend\requirements.txt",
    "training\audit_dataset.py",
    "dataset\splits\train.csv",
    "dataset\splits\validation.csv",
    "dataset\splits\test.csv"
)
foreach ($relative in $requiredPaths) {
    if (Test-Path (Join-Path $Project $relative)) {
        Add-Check "Structure" $relative "PASS" "Found"
    } else {
        Add-Check "Structure" $relative "BLOCKED" "Missing"
    }
}

# 2. Dataset audit: inspect image paths from split CSVs without changing them.
$splitDir = Join-Path $Project "dataset\splits"
$splitFiles = @("train.csv", "validation.csv", "test.csv")
$totalRows = 0
$missingRows = 0
$existingRows = 0
$csvSummaries = @()

foreach ($splitName in $splitFiles) {
    $csvPath = Join-Path $splitDir $splitName
    if (-not (Test-Path $csvPath)) { continue }

    try {
        $rows = @(Import-Csv -LiteralPath $csvPath)
        $totalRows += $rows.Count
        $splitMissing = 0
        $splitExisting = 0
        foreach ($row in $rows) {
            # Prefer common image-path column names; otherwise use the first field that looks path-like.
            $candidate = $null
            foreach ($col in @("image_path","image","filepath","file_path","path","filename","file")) {
                if ($row.PSObject.Properties.Name -contains $col -and -not [string]::IsNullOrWhiteSpace([string]$row.$col)) {
                    $candidate = [string]$row.$col
                    break
                }
            }
            if (-not $candidate) {
                foreach ($prop in $row.PSObject.Properties) {
                    if ([string]$prop.Value -match '\.(tif|tiff|png|jpg|jpeg|bmp|webp)$') {
                        $candidate = [string]$prop.Value
                        break
                    }
                }
            }

            if (-not $candidate) {
                $splitMissing++
                continue
            }

            $candidate = $candidate.Trim().Trim('"')
            $resolved = $candidate
            if (-not [System.IO.Path]::IsPathRooted($resolved)) {
                $resolved = Join-Path $Project $resolved
            }

            if (Test-Path -LiteralPath $resolved -PathType Leaf) {
                $splitExisting++
            } else {
                # Some manifests may include a prefix like major-main/ or dataset/; try only
                # a basename search inside the project as a diagnostic, never copying files.
                $base = [System.IO.Path]::GetFileName($candidate)
                $found = $false
                if ($base) {
                    $hit = Get-ChildItem -LiteralPath $Project -Recurse -File -Filter $base -ErrorAction SilentlyContinue |
                        Where-Object { $_.FullName -notmatch '\\\.venv(312)?\\' } |
                        Select-Object -First 1
                    if ($hit) { $found = $true }
                }
                if ($found) {
                    $splitExisting++
                } else {
                    $splitMissing++
                }
            }
        }

        $missingRows += $splitMissing
        $existingRows += $splitExisting
        $csvSummaries += [pscustomobject]@{
            Split = $splitName
            Rows = $rows.Count
            ImagePathsFound = $splitExisting
            ImagePathsMissingOrUnresolved = $splitMissing
        }
    } catch {
        Add-Check "Dataset" $splitName "WARN" "Could not parse CSV: $($_.Exception.Message)"
    }
}
if ($totalRows -gt 0) {
    if ($missingRows -eq 0) {
        Add-Check "Dataset" "Split image paths" "PASS" "$totalRows rows checked; all referenced images resolved"
    } else {
        Add-Check "Dataset" "Split image paths" "BLOCKED" "$missingRows of $totalRows rows have missing/unresolved images; no files were changed"
    }
} else {
    Add-Check "Dataset" "Split CSVs" "BLOCKED" "No dataset split rows could be read"
}

# 3. Count images in project excluding virtual environments.
$imageExtensions = @(".tif",".tiff",".png",".jpg",".jpeg",".bmp",".webp")
$projectImages = @(Get-ChildItem -LiteralPath $Project -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object {
        ($imageExtensions -contains $_.Extension.ToLowerInvariant()) -and
        ($_.FullName -notmatch '\\\.venv(312)?\\')
    })
Add-Check "Dataset" "Image files in project (excluding venv)" $(if ($projectImages.Count -gt 0) {"PASS"} else {"BLOCKED"}) "$($projectImages.Count) image files found; includes uploads and app assets, not necessarily dataset images"

# 4. Run dataset audit script if present.
$auditScript = Join-Path $Project "training\audit_dataset.py"
$pythonExe = Join-Path $Project ".venv312\Scripts\python.exe"
if (-not (Test-Path $pythonExe)) { $pythonExe = Join-Path $Project ".venv\Scripts\python.exe" }
if ((Test-Path $auditScript) -and (Test-Path $pythonExe)) {
    Write-Host "`nRunning official dataset audit..." -ForegroundColor Cyan
    Push-Location $Project
    try {
        $auditOutput = & $pythonExe $auditScript 2>&1
        $auditExit = $LASTEXITCODE
        $auditOutput | ForEach-Object { Write-Host $_ }
        Add-Check "Dataset" "training\audit_dataset.py" $(if ($auditExit -eq 0) {"PASS"} else {"WARN"}) "Audit script exit code: $auditExit; review its output above"
    } finally {
        Pop-Location
    }
} else {
    Add-Check "Dataset" "Official dataset audit" "WARN" "Audit script or project virtual-environment Python not found"
}

# 5. Python dependencies and PaddleOCR import.
if (Test-Path $pythonExe) {
    $pythonVersion = & $pythonExe --version 2>&1
    Add-Check "Backend" "Python environment" "PASS" "$pythonVersion ($pythonExe)"
    $importCode = "import flask, cv2, numpy; print('Flask', flask.__version__); print('OpenCV', cv2.__version__); print('NumPy', numpy.__version__); import paddle; print('Paddle', paddle.__version__); import paddleocr; print('PaddleOCR import OK')"
    $importOutput = & $pythonExe -c $importCode 2>&1
    if ($LASTEXITCODE -eq 0) {
        Add-Check "Backend" "Core + PaddleOCR imports" "PASS" (($importOutput | ForEach-Object { "$_" }) -join " | ")
    } else {
        Add-Check "Backend" "Core + PaddleOCR imports" "WARN" (($importOutput | Select-Object -Last 5 | ForEach-Object { "$_" }) -join " | ")
    }
} else {
    Add-Check "Backend" "Python environment" "WARN" "Neither .venv312 nor .venv Python executable found"
}

# 6. Frontend production build (may take a minute).
$frontendDir = Join-Path $Project "frontend"
if ((Test-Path (Join-Path $frontendDir "package.json")) -and (Get-Command npm -ErrorAction SilentlyContinue)) {
    Write-Host "`nRunning frontend production build..." -ForegroundColor Cyan
    Push-Location $frontendDir
    try {
        $buildOutput = & npm run build 2>&1
        $buildExit = $LASTEXITCODE
        $buildOutput | ForEach-Object { Write-Host $_ }
        Add-Check "Frontend" "npm run build" $(if ($buildExit -eq 0) {"PASS"} else {"BLOCKED"}) "Exit code: $buildExit"
    } finally {
        Pop-Location
    }
} else {
    Add-Check "Frontend" "npm run build" "WARN" "package.json or npm not found"
}

# 7. API health check if server already running.
try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:5000/api/health" -TimeoutSec 4
    Add-Check "Integration" "Backend /api/health" "PASS" ($health | ConvertTo-Json -Compress -Depth 5)
} catch {
    Add-Check "Integration" "Backend /api/health" "WARN" "Backend did not answer at 127.0.0.1:5000; start backend and rerun to test"
}

# 8. Model artifacts search.
$modelFiles = @(Get-ChildItem -LiteralPath $Project -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object {
        $_.FullName -notmatch '\\\.venv(312)?\\' -and
        $_.Extension.ToLowerInvariant() -in @(".pt",".pth",".onnx",".h5",".keras",".pkl",".joblib",".safetensors",".bin")
    })
if ($modelFiles.Count -gt 0) {
    $modelNames = ($modelFiles | Select-Object -First 12 | ForEach-Object { $_.FullName }) -join "; "
    Add-Check "Models" "Model artifact files" "PASS" "$($modelFiles.Count) possible model artifacts found: $modelNames"
} else {
    Add-Check "Models" "Model artifact files" "BLOCKED" "No model-weight files found outside virtual environments; inspect model integration before claiming trained verification"
}

# 9. Git status.
try {
    $gitRoot = & git -C $Project rev-parse --show-toplevel 2>$null
    if ($LASTEXITCODE -eq 0 -and $gitRoot) {
        $remote = & git -C $Project remote -v 2>$null
        Add-Check "Git" "Repository" "PASS" "Root: $gitRoot; remotes: $(($remote -join ' ') -replace '\s+',' ')"
    } else {
        Add-Check "Git" "Repository" "WARN" "No Git repository found at $Project; script did not initialize or push anything"
    }
} catch {
    Add-Check "Git" "Repository" "WARN" "Git unavailable or repository check failed"
}

# 10. Summary and reports.
$passed = @($Results | Where-Object Status -eq "PASS").Count
$warned = @($Results | Where-Object Status -eq "WARN").Count
$blocked = @($Results | Where-Object Status -eq "BLOCKED").Count

$summary = [pscustomobject]@{
    Project = $Project
    GeneratedAt = (Get-Date).ToString("o")
    PassedChecks = $passed
    Warnings = $warned
    BlockedChecks = $blocked
    DatasetSplits = $csvSummaries
    Checks = @($Results)
    Note = "Diagnostic report only. Does not alter dataset images, split CSVs, train models, or push to Git."
}
$summary | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $ReportJson -Encoding UTF8

$reportLines = @(
    "CHEQUEVERIFY AI AUTOMATED AUDIT",
    "Generated: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')",
    "Project: $Project",
    "",
    "SUMMARY: PASS=$passed WARN=$warned BLOCKED=$blocked",
    "",
    "DATASET SPLITS:"
)
foreach ($s in $csvSummaries) {
    $reportLines += "$($s.Split): rows=$($s.Rows), found=$($s.ImagePathsFound), missing/unresolved=$($s.ImagePathsMissingOrUnresolved)"
}
$reportLines += ""
$reportLines += "CHECK DETAILS:"
foreach ($r in $Results) {
    $reportLines += "[$($r.Status)] $($r.Area) / $($r.Check): $($r.Details)"
}
$reportLines += ""
$reportLines += "This script is diagnostic only. It does not alter dataset images or split CSVs, train models, or push to Git."
$reportLines | Set-Content -LiteralPath $ReportTxt -Encoding UTF8

Write-Host "`n===== AUDIT SUMMARY =====" -ForegroundColor Cyan
Write-Host "PASS: $passed | WARN: $warned | BLOCKED: $blocked"
Write-Host "JSON report: $ReportJson"
Write-Host "Text report: $ReportTxt"
Write-Host "`nSend the final summary and any BLOCKED lines back for the next targeted fix." -ForegroundColor Yellow
