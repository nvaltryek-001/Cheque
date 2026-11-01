"""Read-only discovery and auditing of the two original datasets.

Nothing in this module writes inside a dataset directory."""
from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
SPLIT_ALIASES = {"train": "train", "training": "train", "val": "val", "valid": "val",
                 "validate": "val", "validation": "val", "vaildate": "val",
                 "test": "test", "testing": "test"}
KNOWN_BANKS = {"axis", "canara", "icici", "syndicate", "sbi", "hdfc", "pnb", "bob", "kotak", "yes", "union"}


def _sniff_format(path: Path) -> str:
    head = path.open("rb").read(12)
    if head.startswith(b"\x89PNG"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"BM"):
        return "bmp"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    return "unknown"


def dhash(img: np.ndarray, size: int = 8) -> str:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    small = cv2.resize(gray, (size + 1, size), interpolation=cv2.INTER_AREA)
    bits = (small[:, 1:] > small[:, :-1]).flatten()
    return "".join("1" if b else "0" for b in bits)


def hamming(a: str, b: str) -> int:
    return sum(x != y for x, y in zip(a, b))


def _image_info(path: Path) -> dict:
    data = path.read_bytes()
    info = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
            "format": _sniff_format(path), "decodable": False}
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
    if img is not None:
        info["decodable"] = True
        info["height"], info["width"] = img.shape[:2]
        info["channels"] = 1 if img.ndim == 2 else img.shape[2]
        bgr = img if img.ndim == 2 else img[:, :, :3]
        info["dhash"] = dhash(bgr)
    return info


# ---------------------------------------------------------------- OCR dataset
def discover_ocr_images(root: Path) -> list[dict]:
    """Return [{path, split, bank, filename}] for <root>/<split>/<bank>/<image>."""
    items = []
    if not root.is_dir():
        return items
    for split_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        split = SPLIT_ALIASES.get(split_dir.name.lower())
        if split is None:
            continue
        for bank_dir in sorted(p for p in split_dir.iterdir() if p.is_dir()):
            for f in sorted(bank_dir.iterdir()):
                if f.is_file() and f.suffix.lower() in IMAGE_EXTS:
                    items.append({"path": f, "split": split, "split_dir": split_dir.name,
                                  "bank": bank_dir.name.lower(), "filename": f.name})
    return items


def audit_ocr_dataset(root: Path) -> dict:
    root = Path(root)
    report: dict = {"dataset": "ocr", "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "root_exists": root.is_dir(), "issues": []}
    if not root.is_dir():
        report["issues"].append({"severity": "error", "message": "OCR dataset directory not found."})
        return report
    items = discover_ocr_images(root)
    all_files = [p for p in root.rglob("*") if p.is_file()]
    label_like = [p for p in all_files if p.suffix.lower() in {".json", ".txt", ".csv", ".xml", ".tsv", ".label"}]
    other = [p for p in all_files if p.suffix.lower() not in IMAGE_EXTS and p not in label_like]
    split_dirs = sorted({i["split_dir"] for i in items})

    per_split = Counter(i["split"] for i in items)
    per_bank_split: dict = defaultdict(Counter)
    ext_counter, fmt_counter, dims = Counter(), Counter(), Counter()
    mismatched_ext, corrupted, records = [], [], []
    for it in items:
        info = _image_info(it["path"])
        ext = it["path"].suffix.lower().lstrip(".")
        ext = "jpeg" if ext == "jpg" else ext
        ext_counter[ext] += 1
        fmt_counter[info["format"]] += 1
        if info["format"] != ext:
            mismatched_ext.append(f'{it["split_dir"]}/{it["bank"]}/{it["filename"]} (named .{ext}, content {info["format"]})')
        if not info["decodable"]:
            corrupted.append(f'{it["split_dir"]}/{it["bank"]}/{it["filename"]}')
        else:
            dims[f'{info["width"]}x{info["height"]}'] += 1
        per_bank_split[it["bank"]][it["split"]] += 1
        stem = it["path"].stem
        records.append({**info, "split": it["split"], "bank": it["bank"], "filename": it["filename"],
                        "stem_is_cheque_number": stem.isdigit() and len(stem) == 6})

    # exact + near duplicates and cross-split leakage
    by_hash = defaultdict(list)
    for r in records:
        by_hash[r["sha256"]].append(r)
    exact_dups = [[f'{x["split"]}/{x["bank"]}/{x["filename"]}' for x in v] for v in by_hash.values() if len(v) > 1]
    near_dups = []
    rs = [r for r in records if r.get("dhash")]
    for i in range(len(rs)):
        for j in range(i + 1, len(rs)):
            if rs[i]["sha256"] != rs[j]["sha256"] and hamming(rs[i]["dhash"], rs[j]["dhash"]) <= 2:
                near_dups.append([f'{rs[i]["split"]}/{rs[i]["bank"]}/{rs[i]["filename"]}',
                                  f'{rs[j]["split"]}/{rs[j]["bank"]}/{rs[j]["filename"]}'])
    stem_splits = defaultdict(set)
    for r in records:
        stem_splits[(r["bank"], Path(r["filename"]).stem)].add(r["split"])
    leaked_names = [f"{b}/{s}" for (b, s), sp in stem_splits.items() if len(sp) > 1]
    leaked_hash = [v for v in exact_dups if len({p.split("/")[0] for p in v}) > 1]

    report.update({
        "total_images": len(items),
        "split_directories": split_dirs,
        "images_per_split": dict(per_split),
        "images_per_bank_and_split": {b: dict(c) for b, c in sorted(per_bank_split.items())},
        "file_extensions": dict(ext_counter),
        "actual_formats": dict(fmt_counter),
        "extension_content_mismatches": mismatched_ext,
        "dimensions": dict(dims.most_common()),
        "corrupted_images": corrupted,
        "exact_duplicate_groups": exact_dups,
        "same_template_pairs": near_dups,
        "same_template_note": ("Pairs whose 8x8 difference-hash is within 2 bits. Inspection shows these are the same "
                               "blank cheque template filled with different typed content - not duplicated samples - "
                               "but it means test cheques share layouts with training cheques, so layout-based "
                               "extraction results are optimistic for unseen templates."),
        "cross_split_leakage": {"same_filename": leaked_names, "same_content": leaked_hash},
        "annotation_files": [str(p.relative_to(root)) for p in label_like],
        "other_files": [str(p.relative_to(root)) for p in other],
        "filename_is_cheque_number": sum(r["stem_is_cheque_number"] for r in records),
        "labels": {
            "transcription_labels_present": bool(label_like),
            "label_format": None if not label_like else "unknown – inspect files",
            "derivable_weak_labels": ["bank (from folder name)", "split (from folder name)",
                                      "cheque_number (6-digit filename stem, confirmed visually on samples "
                                      "to equal the first MICR group)"],
        },
        "handwriting_assessment": (
            "No transcription/annotation files exist. Visual inspection of samples from every bank shows the "
            "payee, amount-in-words, amount-in-figures, date and account fields are rendered in machine fonts "
            "(typed onto cheque templates); the only handwritten content is the signature. The dataset therefore "
            "cannot be used to train or evaluate a handwritten-text recognizer."),
        "paddleocr_finetune_compatible": False,
        "paddleocr_finetune_reason": ("PaddleOCR recognition fine-tuning needs line crops with text transcriptions "
                                      "(rec_gt_*.txt) and detection needs polygon labels; neither exists."),
    })
    if mismatched_ext:
        report["issues"].append({"severity": "warning",
                                 "message": f"{len(mismatched_ext)} files have an extension that does not match their content."})
    if corrupted:
        report["issues"].append({"severity": "error", "message": f"{len(corrupted)} images cannot be decoded."})
    if leaked_names or leaked_hash:
        report["issues"].append({"severity": "error", "message": "Images shared between splits (leakage)."})
    if exact_dups:
        report["issues"].append({"severity": "warning", "message": f"{len(exact_dups)} exact duplicate groups."})
    if not label_like:
        report["issues"].append({"severity": "info", "message": "No OCR transcription labels in the dataset."})
    return report


# ---------------------------------------------------------- signature dataset
def find_signature_root(root: Path) -> Path | None:
    """The folder containing genuine_signatures.csv (searched up to 3 levels deep)."""
    root = Path(root)
    if not root.is_dir():
        return None
    if (root / "genuine_signatures.csv").is_file():
        return root
    for depth_glob in ("*/genuine_signatures.csv", "*/*/genuine_signatures.csv", "*/*/*/genuine_signatures.csv"):
        hits = sorted(root.glob(depth_glob))
        if hits:
            return hits[0].parent
    return None


def _read_manifest(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return [{k.strip(): (v or "").strip() for k, v in row.items()} for row in csv.DictReader(fh)]


def load_signature_manifests(root: Path) -> dict | None:
    base = find_signature_root(root)
    if base is None:
        return None
    genuine = _read_manifest(base / "genuine_signatures.csv")
    forged_path = base / "forged_signatures.csv"
    altered = _read_manifest(forged_path) if forged_path.is_file() else []
    for r in genuine:
        r["split_norm"] = SPLIT_ALIASES.get(r.get("split", "").lower(), r.get("split", ""))
        r["path"] = base / "signature_dataset" / r.get("split", "") / r.get("bank", "") / r.get("filename", "")
    for r in altered:
        r["split_norm"] = SPLIT_ALIASES.get(r.get("split", "").lower(), r.get("split", ""))
        r["path"] = base / "forged_signatures" / r.get("split", "") / r.get("bank", "") / r.get("filename", "")
    return {"base": base, "genuine": genuine, "altered": altered}


def audit_signature_dataset(root: Path) -> dict:
    report: dict = {"dataset": "signature", "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "issues": []}
    m = load_signature_manifests(Path(root))
    if m is None:
        report["root_exists"] = Path(root).is_dir()
        report["issues"].append({"severity": "error", "message": "genuine_signatures.csv not found under the signature dataset directory."})
        return report
    base, genuine, altered = m["base"], m["genuine"], m["altered"]
    report["root_exists"] = True
    report["manifest_columns"] = {
        "genuine": list(genuine[0].keys())[:4] if genuine else [],
        "altered": list(altered[0].keys())[:4] if altered else [],
    }

    def check_rows(rows, kind):
        missing, corrupted, dims, fmts, hashes = [], [], Counter(), Counter(), {}
        for r in rows:
            p: Path = r["path"]
            if not p.is_file():
                missing.append(f'{r.get("split")}/{r.get("bank")}/{r.get("filename")}')
                continue
            info = _image_info(p)
            fmts[info["format"]] += 1
            if not info["decodable"]:
                corrupted.append(r["filename"])
                continue
            dims[(info["width"], info["height"])] += 1
            r["_sha"], r["_dhash"], r["_w"], r["_h"] = info["sha256"], info["dhash"], info["width"], info["height"]
            hashes.setdefault(info["sha256"], []).append(f'{kind}:{r["filename"]}')
        ws = [r["_w"] for r in rows if "_w" in r]
        hs = [r["_h"] for r in rows if "_h" in r]
        size = {"min_w": min(ws), "max_w": max(ws), "median_w": int(np.median(ws)),
                "min_h": min(hs), "max_h": max(hs), "median_h": int(np.median(hs))} if ws else {}
        return missing, corrupted, fmts, size, hashes

    g_missing, g_corrupt, g_fmt, g_size, g_hash = check_rows(genuine, "genuine")
    a_missing, a_corrupt, a_fmt, a_size, a_hash = check_rows(altered, "altered")

    signer_counts = Counter(r.get("signer_id") for r in genuine)
    signer_splits = defaultdict(set)
    signer_banks = defaultdict(set)
    for r in genuine:
        signer_splits[r.get("signer_id")].add(r["split_norm"])
        signer_banks[r.get("signer_id")].add(r.get("bank"))
    genuine_by_signer = {r.get("signer_id"): r for r in genuine}
    bad_split = [r["filename"] for r in genuine + altered if r["split_norm"] not in ("train", "val", "test")]
    bad_bank = [r["filename"] for r in genuine + altered if r.get("bank", "").lower() not in KNOWN_BANKS]
    dup_filenames = [f for f, c in Counter(r["filename"] for r in genuine + altered).items() if c > 1]

    # altered -> target mapping checks
    unmapped, split_mismatch, bank_mismatch, name_mismatch = [], [], [], []
    for r in altered:
        tgt = genuine_by_signer.get(r.get("target_signer_id"))
        if tgt is None:
            unmapped.append(r["filename"])
            continue
        if tgt["split_norm"] != r["split_norm"]:
            split_mismatch.append(r["filename"])
        if tgt.get("bank") != r.get("bank"):
            bank_mismatch.append(r["filename"])
        stem = r["filename"].rsplit("_forged", 1)[0]
        if stem != Path(tgt["filename"]).stem:
            name_mismatch.append({"altered": r["filename"], "target_genuine": tgt["filename"]})
    per_target = Counter(r.get("target_signer_id") for r in altered)

    all_hash = defaultdict(list)
    for h in (g_hash, a_hash):
        for k, v in h.items():
            all_hash[k].extend(v)
    dup_hash = [v for v in all_hash.values() if len(v) > 1]

    # near-duplicate genuine images belonging to different signers
    near = []
    gl = [r for r in genuine if "_dhash" in r]
    for i in range(len(gl)):
        for j in range(i + 1, len(gl)):
            if gl[i].get("signer_id") != gl[j].get("signer_id") and hamming(gl[i]["_dhash"], gl[j]["_dhash"]) <= 3:
                near.append([gl[i]["filename"], gl[j]["filename"]])

    on_disk_g = {p.name for p in (base / "signature_dataset").rglob("*") if p.is_file()} if (base / "signature_dataset").is_dir() else set()
    on_disk_a = {p.name for p in (base / "forged_signatures").rglob("*") if p.is_file()} if (base / "forged_signatures").is_dir() else set()
    unlisted = sorted((on_disk_g - {r["filename"] for r in genuine}) | (on_disk_a - {r["filename"] for r in altered}))

    split_signers = defaultdict(set)
    for r in genuine:
        split_signers[r["split_norm"]].add(r.get("signer_id"))
    bank_split = defaultdict(Counter)
    for r in genuine:
        bank_split[r.get("bank")][r["split_norm"]] += 1

    report.update({
        "manifest_dir_name": base.name,
        "genuine_rows": len(genuine), "altered_rows": len(altered),
        "unique_signers": len(signer_counts),
        "specimens_per_signer": dict(Counter(signer_counts.values())),
        "altered_per_target": dict(Counter(per_target.values())),
        "signers_per_split": {k: len(v) for k, v in sorted(split_signers.items())},
        "genuine_per_bank_and_split": {b: dict(c) for b, c in sorted(bank_split.items())},
        "formats": {"genuine": dict(g_fmt), "altered": dict(a_fmt)},
        "image_size": {"genuine": g_size, "altered": a_size},
        "missing_images": g_missing + a_missing,
        "corrupted_images": g_corrupt + a_corrupt,
        "invalid_split_labels": bad_split,
        "unknown_bank_labels": bad_bank,
        "duplicate_filenames_in_manifests": dup_filenames,
        "duplicate_content_groups": dup_hash,
        "near_duplicate_genuine_across_signers": near,
        "signers_in_multiple_splits": [s for s, v in signer_splits.items() if len(v) > 1],
        "signers_with_multiple_banks": [s for s, v in signer_banks.items() if len(v) > 1],
        "altered_unmapped_target": unmapped,
        "altered_split_differs_from_target": split_mismatch,
        "altered_bank_differs_from_target": bank_mismatch,
        "altered_filename_stem_differs_from_target": name_mismatch,
        "files_on_disk_not_in_manifest": unlisted,
        "feasibility": {
            "binary_genuine_vs_synthetic_alteration": "feasible (with caveats: alterations are synthetic edits of the same image)",
            "signer_specific_trained_model": "BLOCKED – one genuine specimen per signer; no within-signer variation to learn from",
            "siamese_or_metric_learning": "BLOCKED – needs multiple independent genuine specimens per signer and real skilled forgeries",
            "independent_evaluation": "limited – test signers are disjoint from train signers, but positives must be simulated re-captures",
        },
    })
    for key, sev, msg in [
        ("missing_images", "error", "manifest rows whose image file is missing"),
        ("corrupted_images", "error", "images that cannot be decoded"),
        ("signers_in_multiple_splits", "error", "signers appear in more than one split (leakage)"),
        ("altered_split_differs_from_target", "error", "altered images in a different split than their target"),
        ("altered_unmapped_target", "error", "altered rows whose target signer does not exist"),
        ("altered_filename_stem_differs_from_target", "warning",
         "altered filenames whose stem differs from the target's genuine filename (mapping kept from manifest)"),
        ("duplicate_content_groups", "warning", "groups of byte-identical images"),
        ("near_duplicate_genuine_across_signers", "warning", "visually near-identical genuine images assigned to different signers"),
        ("files_on_disk_not_in_manifest", "warning", "image files not listed in any manifest"),
    ]:
        if report[key]:
            report["issues"].append({"severity": sev, "message": f"{len(report[key])} {msg}."})
    if report["specimens_per_signer"] == {1: len(signer_counts)}:
        report["issues"].append({"severity": "warning",
                                 "message": "Only one genuine specimen per signer – signer-specific learning is not possible."})
    return report


def save_report(report: dict, out_dir: Path, name: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / f"{name}.json"
    from . import atomic
    atomic.write_json(p, report, default=str)
    return p
