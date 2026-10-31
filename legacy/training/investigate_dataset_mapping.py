from pathlib import Path
import csv, json, re

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "dataset"
REPORTS = ROOT / "reports"
REPORTS.mkdir(exist_ok=True)

image_ext = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
images = []

for split in ("train", "validation", "test"):
    folder = DATA / split
    if folder.exists():
        for p in folder.rglob("*"):
            if p.is_file() and p.suffix.lower() in image_ext:
                images.append({
                    "split": split,
                    "filename": p.name,
                    "stem": p.stem.lower(),
                    "path": str(p),
                })

manifest_files = [
    DATA / "metadata" / "final_synthetic_forgery_manifest.csv",
    DATA / "metadata" / "idrbt_manifest.csv",
    DATA / "splits" / "train.csv",
    DATA / "splits" / "validation.csv",
    DATA / "splits" / "test.csv",
]

def normalize(s):
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())

manifest_summaries = []
examples = []

for file in manifest_files:
    if not file.exists():
        continue

    with file.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    exact_stems = {x["stem"] for x in images}
    exact = 0
    identifier_hits = 0

    for row in rows:
        path = row.get("image_path", "")
        stem = Path(path.replace("\\", "/")).stem.lower()

        if stem in exact_stems:
            exact += 1

        # Report identifier overlap only; do not automatically assign labels.
        ids = re.findall(r"\d{5,}", stem)
        if any(
            any(x in img["stem"] for x in ids)
            for img in images
        ):
            identifier_hits += 1

        if len(examples) < 30:
            examples.append({
                "manifest": file.name,
                "image_path": path,
                "label": row.get("label", ""),
                "source_image": row.get("source_image", ""),
                "filename_exact_match": stem in exact_stems,
            })

    manifest_summaries.append({
        "manifest": str(file),
        "rows": len(rows),
        "exact_filename_matches": exact,
        "identifier_overlap_candidates_not_verified": identifier_hits,
        "columns": list(rows[0].keys()) if rows else [],
    })

report = {
    "downloaded_image_count": len(images),
    "image_counts_by_split": {
        s: sum(i["split"] == s for i in images)
        for s in ("train", "validation", "test")
    },
    "manifest_results": manifest_summaries,
    "sample_downloaded_filenames": [i["filename"] for i in images[:30]],
    "sample_manifest_records": examples,
    "conclusion": (
        "Filename matches are investigative evidence only. "
        "Verify image provenance and label semantics before training."
    ),
}

out = REPORTS / "dataset_mapping_investigation.json"
out.write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report, indent=2))
print(f"\nSaved report: {out}")
