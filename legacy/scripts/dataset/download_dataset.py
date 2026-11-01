from datasets import load_dataset
from pathlib import Path

root = Path(r"N:\Cheque\ChequeA\dataset")
ds = load_dataset("jaganadhg/cheque-synthetic-images")

count = 0

for split in ds:
    out = root / split
    out.mkdir(parents=True, exist_ok=True)

    for i, item in enumerate(ds[split]):
        img = item["image"]
        if img is None:
            continue

        filename = item.get("filename") or f"cheque_{i:04d}.png"
        img.convert("RGB").save(out / Path(filename).name, format="PNG")
        count += 1

    print(f"{split}: saved images to {out}")

print(f"\nTOTAL IMAGES SAVED: {count}")
print(f"Dataset location: {root}")
