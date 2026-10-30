$ErrorActionPreference = "Stop"

$Root = "N:\Cheque\ChequeA"
Set-Location $Root

Write-Host "`n=== CHEQUEVERIFY AI: STRUCTURE AUDIT ===" -ForegroundColor Cyan

# Require the project root and its primary components.
foreach ($p in @("backend", "Frontend", "new_datset")) {
    if (-not (Test-Path (Join-Path $Root $p))) {
        throw "Required directory missing: $p. No changes made."
    }
}

# Avoid moving data blindly. First normalize dataset nesting only when the inner
# directory contains the image files and the outer directory has no other files.
$datasetRoot = Join-Path $Root "new_datset"
$nestedDataset = Join-Path $datasetRoot "new_datset"

if ((Test-Path $nestedDataset) -and
    (Get-ChildItem $nestedDataset -File -Recurse -ErrorAction Stop | Measure-Object).Count -gt 0) {

    $outerFiles = @(Get-ChildItem $datasetRoot -File -Force)
    $innerDirs = @(Get-ChildItem $nestedDataset -Directory -Force)
    if ($outerFiles.Count -eq 0 -and $innerDirs.Count -eq 0) {
        Write-Host "Flattening nested dataset folder into new_datset\..." -ForegroundColor Yellow
        Get-ChildItem $nestedDataset -Force | ForEach-Object {
            $dest = Join-Path $datasetRoot $_.Name
            if (Test-Path $dest) {
                throw "Dataset name collision at $dest. Stop and inspect manually."
            }
            Move-Item $_.FullName $dest
        }
        Remove-Item $nestedDataset -Force
    } else {
        Write-Host "Dataset has mixed/nested contents; leaving it unchanged for safe manual review." -ForegroundColor Yellow
    }
}

# Put root-level helper scripts in categorized folders, preserving their names.
$categories = @{
    "apply_extraction_fix.py" = "scripts\maintenance"
    "audit_cheque_dataset.py" = "scripts\dataset"
    "benchmark_ocr.py" = "scripts\evaluation"
    "benchmark_ocr_fast.py" = "scripts\evaluation"
    "diagnose_ocr.py" = "scripts\diagnostics"
    "download_dataset.py" = "scripts\dataset"
    "fix_patch_script.py" = "scripts\maintenance"
    "ChequeVerifyAI_AutoAudit.ps1" = "scripts\audit"
    "ChequeVerifyAI_MasterFrontend.ps1" = "scripts\maintenance"
}

foreach ($name in $categories.Keys) {
    $source = Join-Path $Root $name
    if (Test-Path $source) {
        $destDir = Join-Path $Root $categories[$name]
        New-Item -ItemType Directory -Path $destDir -Force | Out-Null
        $dest = Join-Path $destDir $name
        if (Test-Path $dest) {
            Write-Host "Already organized: $name" -ForegroundColor DarkGray
        } else {
            Move-Item $source $dest
            Write-Host "Moved $name -> $($categories[$name])" -ForegroundColor Green
        }
    }
}

# Move training/report/service folders only if not already present elsewhere.
# Keep existing folder names and their contents intact.
foreach ($folder in @("training", "reports", "services")) {
    if (Test-Path (Join-Path $Root $folder)) {
        Write-Host "Preserving existing component folder: $folder" -ForegroundColor Green
    }
}

# Create standard top-level documentation describing the actual layout.
$guide = @'
# ChequeVerify AI — Project Layout

```text
ChequeA/
├── Frontend/                 # React + Vite UI and frontend tests
├── backend/                  # Flask API and application services
│   ├── api/                  # HTTP routes
│   ├── models/               # OCR and verification models
│   ├── utils/                # Field extraction and helper functions
│   ├── uploads/              # Uploaded runtime images
│   └── requirements.txt      # Python dependencies
├── new_datset/               # Current dataset; image files should be directly here
├── training/                 # Model training code
├── services/                 # Supporting services
├── reports/                  # Audit and evaluation reports
├── scripts/
│   ├── audit/
│   ├── dataset/
│   ├── diagnostics/
│   ├── evaluation/
│   └── maintenance/
├── .venv/                    # Existing Python environment
├── .venv312/                 # Existing Python 3.12 environment
├── README.md
├── GUIDE.md
└── PROJECT_STRUCTURE.md
```

## Notes
- Dataset folder name retained as `new_datset` to avoid breaking existing paths.
- Check `backend/config.py` and training scripts for dataset path references before changing dataset names.
- Keep uploads and trained model assets if referenced by the backend.
- Runtime caches and build outputs may be regenerated.
- Backend API routes are defined in `backend/api/routes.py`.
'@
Set-Content -Path (Join-Path $Root "PROJECT_STRUCTURE.md") -Value $guide -Encoding UTF8

# Remove generated Python caches and bytecode only, excluding virtual environments.
Get-ChildItem $Root -Directory -Recurse -Force -ErrorAction SilentlyContinue |
    Where-Object {
        $_.Name -in @("__pycache__", ".pytest_cache", ".mypy_cache") -and
        $_.FullName -notmatch '\\\.venv\\|\\\.venv312\\|\\node_modules\\'
    } |
    ForEach-Object {
        Write-Host "Removing cache $($_.FullName)" -ForegroundColor DarkYellow
        Remove-Item $_.FullName -Recurse -Force
    }

Get-ChildItem (Join-Path $Root "backend") -Filter "*.pyc" -File -Recurse -Force -ErrorAction SilentlyContinue |
    Remove-Item -Force

Write-Host "`n=== FINAL TOP-LEVEL STRUCTURE ===" -ForegroundColor Cyan
Get-ChildItem $Root -Force |
    Select-Object Mode, Name |
    Format-Table -AutoSize

Write-Host "`n=== DATASET IMAGE COUNTS ===" -ForegroundColor Cyan
Get-ChildItem $datasetRoot -File -Recurse |
    Group-Object Extension |
    Select-Object Name, Count |
    Format-Table -AutoSize

Write-Host "`nOrganization pass completed. Source/model files were not deleted." -ForegroundColor Green
