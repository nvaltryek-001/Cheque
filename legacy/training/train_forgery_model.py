"""
ChequeVerify AI - supervised forgery-model training precheck.

This script deliberately refuses to train when image paths are unresolved.
It uses the supplied train/validation/test CSVs and never invents labels.
It does not treat split folder names or bank names as authenticity labels.
"""

from pathlib import Path
import csv
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "dataset"
SPLITS = DATASET / "splits"
REPORTS = ROOT / "reports"
REPORTS.mkdir(exist_ok=True)

SPLIT_FILES = {
    "train": SPLITS / "train.csv",
    "validation": SPLITS / "validation.csv",
    "test": SPLITS / "test.csv",
}
VALID_LABELS = {"reference", "synthetic_forgery"}

def resolve_image(raw):
    raw = (raw or "").strip().replace("\\", "/")
    if not raw:
        return None

    candidates = [
        ROOT / raw,
        DATASET / raw,
    ]

    if raw.lower().startswith("datasets/"):
        suffix = raw[len("datasets/"):]
        candidates.extend([
            DATASET / suffix,
            DATASET / "datasets" / suffix,
        ])

    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()

    return None

report = {
    "project": "ChequeVerify AI",
    "task": "supervised cheque authenticity classification",
    "status": "PRECHECK",
    "splits": {},
    "issues": [],
    "training_started": False,
    "accuracy": None,
}

all_ok = True
seen_paths = set()

for split, csv_path in SPLIT_FILES.items():
    if not csv_path.is_file():
        report["issues"].append(f"Missing split file: {csv_path}")
        report["splits"][split] = {"rows": 0, "resolved": 0, "missing": 0}
        all_ok = False
        continue

    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    resolved = 0
    missing = []
    labels = {}
    split_paths = []

    for row in rows:
        label = (row.get("label") or "").strip()
        labels[label] = labels.get(label, 0) + 1

        image = resolve_image(row.get("image_path"))
        if image is None:
            missing.append(row.get("image_path", ""))
            continue

        resolved += 1
        split_paths.append(str(image))

    report["splits"][split] = {
        "rows": len(rows),
        "resolved": resolved,
        "missing": len(missing),
        "labels": labels,
        "missing_examples": missing[:10],
    }

    if missing:
        all_ok = False
        report["issues"].append(
            f"{split}: {len(missing)} image paths cannot be resolved"
        )

    if not rows:
        all_ok = False
        report["issues"].append(f"{split}: no records")

    if any(label not in VALID_LABELS for label in labels):
        all_ok = False
        report["issues"].append(
            f"{split}: unexpected labels present; review before training"
        )

    # Prevent one physical image from appearing across multiple splits.
    for image_path in split_paths:
        if image_path in seen_paths:
            all_ok = False
            report["issues"].append(
                f"Image occurs in multiple splits: {image_path}"
            )
        seen_paths.add(image_path)

    if len(labels) < 2:
        all_ok = False
        report["issues"].append(
            f"{split}: both classes are required for supervised evaluation"
        )

if all_ok:
    report["status"] = "DATA_VALIDATION_PASSED"
    report["issues"].append(
        "Image paths and split labels passed basic checks. "
        "Inspect image content and provenance before training."
    )
else:
    report["status"] = "BLOCKED_DATA_VALIDATION"
    report["issues"].append(
        "No model was trained. Fix image mapping and validate label provenance."
    )

report_path = REPORTS / "training_precheck.json"
report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report, indent=2))
print(f"\nReport saved: {report_path}")

sys.exit(0 if all_ok else 2)
