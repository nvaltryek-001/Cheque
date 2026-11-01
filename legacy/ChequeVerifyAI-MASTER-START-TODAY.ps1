#requires -Version 5.1
<#
ChequeVerify AI - Master Signature Work Bootstrap
- Preserves existing OCR/backend/frontend source.
- Imports the supplied signature dataset into an isolated dataset/signature tree.
- Creates README-aligned signature development folders.
- Clones an MIT Siamese implementation as READ-ONLY reference code only.
- Audits manifests, split counts, image counts, and signer leakage.
- Does NOT train automatically or claim model accuracy.
#>

[CmdletBinding()]
param(
    [string]$ProjectRoot = "N:\Cheque\ChequeA",
    [string]$DatasetZip = "",
    [switch]$SkipReferenceClone
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Write-Step([string]$Message) { Write-Host "`n=== $Message ===" -ForegroundColor Cyan }
function Write-Ok([string]$Message)   { Write-Host "[OK] $Message" -ForegroundColor Green }
function Write-Warn([string]$Message) { Write-Host "[WARN] $Message" -ForegroundColor Yellow }
function Write-Fail([string]$Message) { Write-Host "[ERROR] $Message" -ForegroundColor Red }

function Ensure-Directory([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
        Write-Host "Created: $Path" -ForegroundColor DarkGray
    }
}

function Count-Images([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return 0 }
    return @(Get-ChildItem -LiteralPath $Path -File -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.Extension -match '^\.(jpg|jpeg|png|bmp|tif|tiff)$' }).Count
}

function Get-RelativeImageRows([string]$Root, [string]$Kind) {
    $rows = @()
    if (-not (Test-Path -LiteralPath $Root)) { return $rows }
    foreach ($splitDir in Get-ChildItem -LiteralPath $Root -Directory -ErrorAction SilentlyContinue) {
        foreach ($bankDir in Get-ChildItem -LiteralPath $splitDir.FullName -Directory -ErrorAction SilentlyContinue) {
            foreach ($file in Get-ChildItem -LiteralPath $bankDir.FullName -File -ErrorAction SilentlyContinue |
                Where-Object { $_.Extension -match '^\.(jpg|jpeg|png|bmp|tif|tiff)$' }) {
                $rows += [pscustomobject]@{
                    kind = $Kind
                    split = $splitDir.Name
                    bank = $bankDir.Name.ToLowerInvariant()
                    filename = $file.Name
                    path = $file.FullName
                }
            }
        }
    }
    return $rows
}

try {
    Write-Step "ChequeVerify AI - preflight"
    if (-not (Test-Path -LiteralPath $ProjectRoot -PathType Container)) {
        throw "Project root not found: $ProjectRoot. Check the path before running."
    }
    $ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
    if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot "README.md"))) {
        throw "README.md was not found in $ProjectRoot. Refusing to scaffold the wrong folder."
    }
    Write-Ok "Project root verified: $ProjectRoot"

    $gitExe = Get-Command git -ErrorAction SilentlyContinue
    if ($gitExe) {
        Push-Location $ProjectRoot
        try {
            $branch = (& git branch --show-current 2>$null | Out-String).Trim()
            $commit = (& git rev-parse --short HEAD 2>$null | Out-String).Trim()
            $status = @(& git status --short 2>$null)
            Write-Host "Git branch: $branch | HEAD: $commit"
            if ($status.Count -gt 0) {
                Write-Warn "Existing Git changes detected ($($status.Count) status lines). They will NOT be reset or overwritten."
            } else {
                Write-Ok "Git working tree clean."
            }
        } finally { Pop-Location }
    } else {
        Write-Warn "Git was not found on PATH. Existing project files will still be preserved."
    }

    Write-Step "Create README-aligned signature development structure"
    $directories = @(
        "dataset\signature\raw\genuine\train",
        "dataset\signature\raw\genuine\validate",
        "dataset\signature\raw\genuine\test",
        "dataset\signature\raw\synthetic_forged\train",
        "dataset\signature\raw\synthetic_forged\validate",
        "dataset\signature\raw\synthetic_forged\test",
        "dataset\signature\manifests",
        "dataset\signature\processed\genuine",
        "dataset\signature\processed\synthetic_forged",
        "dataset\signature\splits",
        "training\signature\configs",
        "training\signature\checkpoints",
        "training\signature\logs",
        "backend\models\signature",
        "backend\services\signature",
        "backend\utils\signature",
        "backend\api",
        "scripts\signature\audit",
        "scripts\signature\preprocess",
        "scripts\signature\training",
        "scripts\signature\evaluation",
        "reports\signature",
        "docs\signature",
        "references\signature"
    )
    foreach ($relative in $directories) {
        Ensure-Directory (Join-Path $ProjectRoot $relative)
    }
    Write-Ok "Signature folders created without deleting or renaming existing project folders."

    Write-Step "Locate and validate supplied dataset ZIP"
    if ([string]::IsNullOrWhiteSpace($DatasetZip)) {
        $candidate = Join-Path $ProjectRoot "Signature dataset.zip"
        if (Test-Path -LiteralPath $candidate) {
            $DatasetZip = $candidate
        } else {
            $DatasetZip = Read-Host "Enter the full path to Signature dataset.zip (copy it into the project root or provide its path)"
        }
    }
    if (-not (Test-Path -LiteralPath $DatasetZip -PathType Leaf)) {
        throw "Dataset ZIP not found: $DatasetZip. Copy the uploaded ZIP to the project root, then rerun this script."
    }
    $DatasetZip = (Resolve-Path -LiteralPath $DatasetZip).Path
    if ([IO.Path]::GetExtension($DatasetZip).ToLowerInvariant() -ne ".zip") {
        throw "The dataset input must be a .zip file."
    }
    Write-Ok "Dataset archive found: $DatasetZip"

    $stage = Join-Path $env:TEMP ("ChequeVerify_Signature_Stage_" + [guid]::NewGuid().ToString("N"))
    Ensure-Directory $stage
    try {
        Expand-Archive -LiteralPath $DatasetZip -DestinationPath $stage -Force

        $genuineSource = Join-Path $stage "New folder\signature_dataset"
        $forgedSource = Join-Path $stage "New folder\forged_signatures"
        $genuineManifest = Join-Path $stage "New folder\genuine_signatures.csv"
        $forgedManifest = Join-Path $stage "New folder\forged_signatures.csv"

        if (-not (Test-Path -LiteralPath $genuineSource -PathType Container) -or
            -not (Test-Path -LiteralPath $forgedSource -PathType Container) -or
            -not (Test-Path -LiteralPath $genuineManifest -PathType Leaf) -or
            -not (Test-Path -LiteralPath $forgedManifest -PathType Leaf)) {
            throw "ZIP layout does not match the inspected archive. Expected New folder\signature_dataset, forged_signatures and both CSV manifests."
        }

        $targetGenuine = Join-Path $ProjectRoot "dataset\signature\raw\genuine"
        $targetForged = Join-Path $ProjectRoot "dataset\signature\raw\synthetic_forged"

        # Refuse to overwrite an existing import. The user can archive/remove it manually after review.
        $existingGenuine = Count-Images $targetGenuine
        $existingForged = Count-Images $targetForged
        if (($existingGenuine -gt 0) -or ($existingForged -gt 0)) {
            throw "Signature dataset destination already contains images (genuine=$existingGenuine, synthetic=$existingForged). Nothing was overwritten. Move the old signature import to a backup folder before rerunning."
        }

        foreach ($split in @("train","validate","test")) {
            foreach ($bank in @("axis","canara","icici","syndicate")) {
                $srcG = Join-Path $genuineSource "$split\$bank"
                $srcF = Join-Path $forgedSource "$split\$bank"
                $dstG = Join-Path $targetGenuine "$split\$bank"
                $dstF = Join-Path $targetForged "$split\$bank"
                Ensure-Directory $dstG
                Ensure-Directory $dstF
                if (Test-Path -LiteralPath $srcG) {
                    Get-ChildItem -LiteralPath $srcG -File | Copy-Item -Destination $dstG -Force
                }
                if (Test-Path -LiteralPath $srcF) {
                    Get-ChildItem -LiteralPath $srcF -File | Copy-Item -Destination $dstF -Force
                }
            }
        }

        Copy-Item -LiteralPath $genuineManifest -Destination (Join-Path $ProjectRoot "dataset\signature\manifests\genuine_signatures.csv") -Force
        Copy-Item -LiteralPath $forgedManifest -Destination (Join-Path $ProjectRoot "dataset\signature\manifests\synthetic_forged_signatures.csv") -Force
        Write-Ok "Genuine and synthetic-forgery images imported into isolated signature dataset folders."
    } finally {
        if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
    }

    Write-Step "Audit imported signature dataset"
    $genuineRoot = Join-Path $ProjectRoot "dataset\signature\raw\genuine"
    $forgedRoot = Join-Path $ProjectRoot "dataset\signature\raw\synthetic_forged"
    $genuineRows = @(Get-RelativeImageRows $genuineRoot "genuine")
    $forgedRows = @(Get-RelativeImageRows $forgedRoot "synthetic_forged")
    $expectedBanks = @("axis","canara","icici","syndicate")
    $expectedSplits = @("train","validate","test")
    $countRows = @()

    foreach ($split in $expectedSplits) {
        foreach ($bank in $expectedBanks) {
            $gc = @($genuineRows | Where-Object { $_.split -eq $split -and $_.bank -eq $bank }).Count
            $fc = @($forgedRows | Where-Object { $_.split -eq $split -and $_.bank -eq $bank }).Count
            $countRows += [pscustomobject]@{
                split = $split; bank = $bank; genuine_images = $gc; synthetic_forged_images = $fc
                expected_genuine = $(if ($split -eq "train") {20} else {5})
                expected_synthetic_forged = $(if ($split -eq "train") {40} else {10})
                counts_match_expected = (($gc -eq $(if ($split -eq "train") {20} else {5})) -and
                                         ($fc -eq $(if ($split -eq "train") {40} else {10})))
            }
        }
    }

    $genuineManifestRows = @(Import-Csv -LiteralPath (Join-Path $ProjectRoot "dataset\signature\manifests\genuine_signatures.csv"))
    $forgedManifestRows = @(Import-Csv -LiteralPath (Join-Path $ProjectRoot "dataset\signature\manifests\synthetic_forged_signatures.csv"))
    $splitSignerGroups = $genuineManifestRows | Group-Object signer_id
    $signerLeakage = @($splitSignerGroups | Where-Object {
        (@($_.Group | Select-Object -ExpandProperty split -Unique)).Count -gt 1
    })

    $totalGenuine = $genuineRows.Count
    $totalForged = $forgedRows.Count
    $allCountsMatch = (@($countRows | Where-Object { -not $_.counts_match_expected }).Count -eq 0)
    $audit = [ordered]@{
        generated_at = (Get-Date).ToString("o")
        project_root = $ProjectRoot
        archive = $DatasetZip
        genuine_image_count = $totalGenuine
        synthetic_forged_image_count = $totalForged
        genuine_manifest_rows = $genuineManifestRows.Count
        synthetic_forged_manifest_rows = $forgedManifestRows.Count
        banks = $expectedBanks
        splits = $expectedSplits
        per_bank_split_counts = $countRows
        signer_split_leakage_count = $signerLeakage.Count
        counts_match_expected = $allCountsMatch
        important_limitation = "Forged images are algorithmically generated alterations of genuine source images. This dataset supports an initial genuine-vs-synthetic-alteration experiment, not a claim of performance on independently collected real-world forgeries. The manifest has one genuine source image per signer_id; it does not provide multiple independent genuine specimens per signer for conventional writer-specific positive-pair training."
    }
    $auditPath = Join-Path $ProjectRoot "reports\signature\signature_dataset_audit.json"
    $audit | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $auditPath -Encoding UTF8
    $countRows | Export-Csv -LiteralPath (Join-Path $ProjectRoot "reports\signature\signature_dataset_counts.csv") -NoTypeInformation -Encoding UTF8

    $countRows | Format-Table split,bank,genuine_images,synthetic_forged_images,counts_match_expected -AutoSize
    Write-Host "Genuine images: $totalGenuine | Synthetic forged images: $totalForged"
    Write-Host "Genuine manifest rows: $($genuineManifestRows.Count) | Forged manifest rows: $($forgedManifestRows.Count)"
    Write-Host "Signer IDs crossing splits: $($signerLeakage.Count)"
    if (-not $allCountsMatch -or $totalGenuine -ne 120 -or $totalForged -ne 240) {
        Write-Warn "Dataset counts differ from the uploaded archive's expected 120 genuine / 240 synthetic images. Inspect the audit report."
    } else {
        Write-Ok "Dataset counts match the inspected archive."
    }
    if ($signerLeakage.Count -gt 0) {
        Write-Warn "Signer leakage across splits detected. Review manifest before training."
    } else {
        Write-Ok "No signer_id appears across multiple splits in the genuine manifest."
    }

    Write-Step "Clone external Siamese implementation as reference only"
    $referencePath = Join-Path $ProjectRoot "references\signature\siamese_net_reference"
    if (-not $SkipReferenceClone) {
        if (Test-Path -LiteralPath $referencePath) {
            Write-Warn "Reference folder already exists; preserving it: $referencePath"
        } elseif (-not $gitExe) {
            Write-Warn "Git is unavailable; skipping reference clone."
        } else {
            & git clone --depth 1 "https://github.com/seanbenhur/siamese_net.git" $referencePath
            if ($LASTEXITCODE -ne 0) {
                Write-Warn "Reference clone failed. Main project and imported dataset remain intact."
            } else {
                Write-Ok "Reference repository cloned. Treat it as read-only architecture/code reference."
            }
        }
    } else {
        Write-Host "Reference clone skipped by parameter."
    }

    $referenceNote = @"
# External signature code reference

Repository: https://github.com/seanbenhur/siamese_net

Purpose: inspect the Siamese-network implementation, pair construction and training organization.

Project-specific restrictions:
- Do not copy the reference repository's dataset into this project.
- Do not use its pretrained weights.
- Do not report its published/demo metrics as ChequeVerify AI results.
- Train project model weights from scratch using only dataset/signature/raw and project-created training pairs.
- Review and preserve the upstream license and attribution before copying/adapting any source file.
- Keep the reference checkout isolated under references/signature/siamese_net_reference.
"@
    $referenceNote | Set-Content -LiteralPath (Join-Path $ProjectRoot "references\signature\README.md") -Encoding UTF8

    Write-Step "Write signature work plan and dataset caveats"
    $plan = @"
# ChequeVerify AI - Signature Work Plan

## Scope
Implement the README's signature workflow: cheque signature region detection/cropping, image preprocessing, authorized specimen enrollment, similarity model, threshold calibration, manual-review result, API integration and frontend display.

## Dataset imported
- Genuine: 120 images
- Algorithmically altered/synthetic forged: 240 images
- Banks: Axis, Canara, ICICI, Syndicate
- Per bank: train 20 genuine + 40 synthetic; validate 5 + 10; test 5 + 10
- Source manifests: dataset/signature/manifests/

## Critical experimental limitation
Each genuine manifest row has a unique signer_id and only one genuine source image per signer. This does not provide multiple independent genuine specimens per signer for normal writer-specific Siamese positive pairs. Synthetic negatives are generated from the same genuine source and may contain detectable editing artifacts. The first experiment must therefore be described as genuine-versus-synthetic-alteration classification/pair testing, not validated real-world forgery detection.

## Required sequence
1. Review current ChequeVerify AI backend and frontend before editing.
2. Inspect cheque image layout and determine signature ROI strategy per bank/layout.
3. Implement signature crop and preprocessing with visual quality checks.
4. Create deterministic pair/label manifests using only the imported project dataset.
5. Train a compact Siamese/CNN baseline from random initialization; no external pretrained weights.
6. Select thresholds on validation only.
7. Evaluate once on the held-out test split; report confusion matrix, precision, recall, F1, ROC-AUC, FAR, FRR and bank-wise results.
8. Add authorized specimen enrollment and similarity comparison; do not infer identity from a single image without a suitable enrollment protocol.
9. Integrate Flask endpoints and React UI only after backend inference tests pass.
10. Add human-review state for uncertain results and secure specimen/image storage.

## Reports
- reports/signature/signature_dataset_audit.json
- reports/signature/signature_dataset_counts.csv

## Reference code
See references/signature/README.md. Reference source is for architecture study only; its dataset and weights are excluded from this project's training.
"@
    $plan | Set-Content -LiteralPath (Join-Path $ProjectRoot "docs\signature\SIGNATURE_IMPLEMENTATION_PLAN.md") -Encoding UTF8

    Write-Step "Final summary"
    Write-Ok "Bootstrap and dataset import finished."
    Write-Host "Project: $ProjectRoot"
    Write-Host "Audit:   $auditPath"
    Write-Host "Plan:    $(Join-Path $ProjectRoot 'docs\signature\SIGNATURE_IMPLEMENTATION_PLAN.md')"
    Write-Host "`nNEXT: review the audit output, then run a source-level backend/frontend audit before implementing or training. This script does not modify existing OCR code or start model training." -ForegroundColor Yellow
}
catch {
    Write-Fail $_.Exception.Message
    Write-Host "No existing OCR source files were intentionally modified." -ForegroundColor Yellow
    exit 1
}
