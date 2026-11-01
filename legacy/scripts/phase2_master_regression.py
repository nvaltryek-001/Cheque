import csv
import json
import time
import requests
from pathlib import Path
from datetime import datetime

ROOT = Path(r"N:\Cheque\ChequeA")
TEST_DIR = ROOT / "new_datset" / "test"
REPORT_DIR = ROOT / "reports"
BASELINE = REPORT_DIR / "PHASE1_20_IMAGE_20261030_124234.csv"

UPLOAD_URL = "http://127.0.0.1:5000/api/upload"
PROCESS_URL = "http://127.0.0.1:5000/api/process"
HEALTH_URL = "http://127.0.0.1:5000/api/health"

FIELDS = [
    "bank_name", "cheque_number", "micr_code", "account_number",
    "date", "payee_name", "amount_figures", "amount_words",
    "ifsc_code", "branch_name"
]

def main():
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = REPORT_DIR / f"PHASE2_20_IMAGE_{stamp}.csv"
    json_path = REPORT_DIR / f"PHASE2_20_IMAGE_{stamp}.json"

    health = requests.get(HEALTH_URL, timeout=15)
    health.raise_for_status()
    print("BACKEND:", health.json())

    images = sorted(
        p for p in TEST_DIR.rglob("*")
        if p.is_file() and p.suffix.lower() in
        {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}
    )

    if len(images) != 20:
        raise RuntimeError(f"Expected 20 images, found {len(images)}")

    records = []

    for index, image in enumerate(images, 1):
        print(f"\n[{index}/20] {image.parent.name}/{image.name}")

        try:
            with image.open("rb") as stream:
                upload = requests.post(
                    UPLOAD_URL,
                    files={"image": (image.name, stream, "application/octet-stream")},
                    timeout=90
                )

            upload.raise_for_status()
            upload_data = upload.json()

            upload_id = upload_data.get("upload_id")
            if not upload_id:
                raise RuntimeError(
                    "Upload response missing upload_id: " +
                    json.dumps(upload_data)[:500]
                )

            response = requests.post(
                PROCESS_URL,
                json={
                    "upload_id": upload_id,
                    "run_ocr": True,
                    "run_vlm": False,
                    "run_verification": False
                },
                timeout=300
            )
            response.raise_for_status()
            result = response.json()

            extracted = result.get("fields") or {}
            record = {
                "image": image.name,
                "bank_folder": image.parent.name,
                "upload_id": upload_id,
                "status": "OK",
                "processing_time": result.get("processing_time"),
                "ocr_text_length": result.get("ocr_text_length"),
                "has_blanks": result.get("has_blanks"),
            }

            for field in FIELDS:
                value = extracted.get(field)
                record[field] = value if value not in ("", None) else ""

            record["populated_fields"] = sum(
                bool(record[field]) for field in FIELDS
            )
            records.append(record)

            print(
                f"  OK | {record['populated_fields']}/10 fields | "
                f"{record['processing_time']} sec"
            )

        except Exception as exc:
            print("  ERROR:", str(exc))
            record = {
                "image": image.name,
                "bank_folder": image.parent.name,
                "status": "ERROR",
                "error": str(exc)
            }
            for field in FIELDS:
                record[field] = ""
            record["populated_fields"] = 0
            records.append(record)

    columns = [
        "image", "bank_folder", "upload_id", "status",
        "processing_time", "ocr_text_length", "has_blanks",
        *FIELDS, "populated_fields", "error"
    ]

    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)

    summary = {
        "timestamp": stamp,
        "total_images": len(images),
        "successful": sum(r["status"] == "OK" for r in records),
        "failed": sum(r["status"] != "OK" for r in records),
        "populated_field_count": sum(
            r["populated_fields"] for r in records
        ),
        "possible_fields": len(images) * len(FIELDS),
        "field_completeness_percent": round(
            100 * sum(r["populated_fields"] for r in records)
            / (len(images) * len(FIELDS)), 2
        ),
        "per_field": {
            field: sum(bool(r.get(field)) for r in records)
            for field in FIELDS
        },
        "per_bank": {},
        "records": records,
        "csv_report": str(csv_path)
    }

    for bank in sorted({r["bank_folder"] for r in records}):
        bank_records = [r for r in records if r["bank_folder"] == bank]
        summary["per_bank"][bank] = {
            "images": len(bank_records),
            "successful": sum(r["status"] == "OK" for r in bank_records),
            "populated_fields": sum(r["populated_fields"] for r in bank_records),
            "possible_fields": len(bank_records) * 10
        }

    if BASELINE.exists():
        with BASELINE.open(encoding="utf-8-sig", newline="") as f:
            old = list(csv.DictReader(f))

        summary["phase1_baseline_rows"] = len(old)
        summary["phase1_baseline_csv"] = str(BASELINE)

        old_total = sum(
            sum(bool(row.get(field, "").strip()) for field in FIELDS)
            for row in old
        )
        summary["phase1_populated_fields"] = old_total
        summary["phase1_completeness_percent"] = round(
            100 * old_total / (len(old) * 10), 2
        ) if old else None

    json_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

    print("\n========== PHASE 2 RESULTS ==========")
    print("Successful:", summary["successful"], "/", len(images))
    print("Failed:", summary["failed"])
    print(
        "Field completeness:",
        summary["populated_field_count"], "/",
        summary["possible_fields"],
        f"({summary['field_completeness_percent']}%)"
    )

    if "phase1_completeness_percent" in summary:
        print("Phase 1 completeness:", summary["phase1_completeness_percent"], "%")
        print("Phase 2 completeness:", summary["field_completeness_percent"], "%")
        print(
            "Change:",
            round(
                summary["field_completeness_percent"]
                - summary["phase1_completeness_percent"], 2
            ),
            "percentage points"
        )

    print("\nPER-FIELD:")
    for field, count in summary["per_field"].items():
        print(f"{field:20s} {count}/20")

    print("\nPER-BANK:")
    for bank, data in summary["per_bank"].items():
        print(bank, data)

    print("\nCSV:", csv_path)
    print("JSON:", json_path)

if __name__ == "__main__":
    main()
