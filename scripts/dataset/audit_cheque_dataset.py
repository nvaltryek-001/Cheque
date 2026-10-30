from pathlib import Path
from collections import Counter
import cv2

root = Path(r"N:\Cheque\ChequeA\dataset")
image_dir = root / "images"
extensions = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}

if not image_dir.exists():
    raise SystemExit(f"Image directory not found: {image_dir}")

files = sorted(
    p for p in image_dir.iterdir()
    if p.is_file() and p.suffix.lower() in extensions
)

print("=" * 60)
print("CHEQUEVERIFY AI - DATASET AUDIT")
print("=" * 60)
print("Image directory:", image_dir)
print("Total images:", len(files))
print("Formats:", dict(Counter(p.suffix.lower() for p in files)))

dimensions = Counter()
unreadable = []
total_bytes = 0

for path in files:
    total_bytes += path.stat().st_size
    image = cv2.imread(str(path))
    if image is None:
        unreadable.append(path.name)
        continue
    h, w = image.shape[:2]
    dimensions[f"{w}x{h}"] += 1

print("Total image size: {:.2f} MB".format(total_bytes / (1024 * 1024)))
print("Image dimensions:", dict(dimensions))
print("Unreadable images:", unreadable or "None")

print("\nPossible annotation/label files:")
for p in root.rglob("*"):
    if p.is_file() and p.suffix.lower() in {
        ".csv", ".json", ".xml", ".txt", ".yaml", ".yml"
    }:
        print("-", p.relative_to(root))

print("\nFirst 15 image filenames:")
for p in files[:15]:
    print("-", p.name)

print("\nAudit complete. No dataset files were modified.")
