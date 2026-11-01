from pathlib import Path
import csv, json
ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
REPORTS.mkdir(exist_ok=True)
manifest_files = [ROOT/"dataset/splits/train.csv", ROOT/"dataset/splits/validation.csv", ROOT/"dataset/splits/test.csv"]
summary = {"project_root": str(ROOT), "splits": {}, "missing_images": [], "status": "unknown"}
for manifest in manifest_files:
    name = manifest.stem
    rows = []
    if not manifest.exists():
        summary["splits"][name] = {"exists": False, "rows": 0}
        continue
    with manifest.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
            raw = (row.get("image_path") or row.get("path") or "").strip()
            if raw:
                p = Path(raw)
                # Paths in the supplied manifests start at datasets/; check root and preserved dataset folder.
                candidates = [ROOT / raw, ROOT / raw.replace("datasets/", "dataset/")]
                if not any(c.exists() for c in candidates):
                    summary["missing_images"].append({"split": name, "path": raw})
    summary["splits"][name] = {"exists": True, "rows": len(rows), "columns": list(rows[0].keys()) if rows else []}
summary["missing_image_count"] = len(summary["missing_images"])
summary["status"] = "READY_FOR_DATA_VALIDATION" if summary["missing_image_count"] == 0 and all(summary["splits"].get(s, {}).get("rows", 0) for s in ["train", "validation", "test"]) else "IMAGES_MISSING_OR_SPLITS_INCOMPLETE"
out = REPORTS / "dataset_audit.json"
out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
print(json.dumps({"status": summary["status"], "splits": summary["splits"], "missing_image_count": summary["missing_image_count"], "report": str(out)}, indent=2))
