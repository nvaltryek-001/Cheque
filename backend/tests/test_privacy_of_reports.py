"""The file names of the delivered datasets are cheque numbers and account numbers. Nothing that is trained, saved or reported
may spell them out: not the MICR model (its labels are learned cheque by cheque), not the dataset audit reports, not the
training metadata. Every value used here is invented."""
import csv
import json

import cv2
import numpy as np

from app.ocr.micr import MIX_SEED, MicrModel, train_from_dataset
from app.services import datasets
from compat_cases import MODELS, micr_cheque

NUMBERS = ["246810", "135792", "112358", "314159", "271828", "161803"]


def _noise_png(path, seed):
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), np.random.default_rng(seed).integers(0, 255, (60, 120, 3), dtype=np.uint8))


# ---------------------------------------------------------------- the MICR model
def test_mixing_keeps_every_template_and_changes_only_the_order():
    model = MicrModel(np.random.default_rng(1).random((60, 24, 20)).astype(np.float32), list("123456" * 10), {"version": "x"})
    mixed = model.mixed()
    pairs = lambda m: sorted((label, m.samples[i].tobytes()) for i, label in enumerate(m.labels))
    assert pairs(mixed) == pairs(model)                         # same templates with the same labels
    assert mixed.labels != model.labels                         # in another order
    assert mixed.meta == {"version": "x", "order": "mixed"}
    assert model.mixed().labels == model.mixed(MIX_SEED).labels   # and always the same other order


def test_training_does_not_spell_out_the_cheque_numbers(tmp_path):
    items = []
    for number in NUMBERS:
        path = tmp_path / f"{number}.png"
        cv2.imwrite(str(path), micr_cheque(f"{number} 999888777 135790 31"))
        items.append({"path": path, "filename": path.name})
    (tmp_path / "notanumber.png").write_bytes(b"x")
    items.append({"path": tmp_path / "notanumber.png", "filename": "notanumber.png"})

    model, meta = train_from_dataset(items)
    joined = "".join(model.labels)
    assert meta["train_cheques_used"] == len(NUMBERS) and len(joined) == 6 * len(NUMBERS)
    assert sorted(joined) == sorted("".join(NUMBERS))           # the digits that were learned are all there
    assert not any(number in joined for number in NUMBERS)      # but not in reading order
    assert model.meta["order"] == "mixed"
    # the metadata (saved with the model and shown in reports) counts what was skipped and never names a file
    assert meta["skipped"] == {"filename is not a 6-digit cheque number": 1}
    assert not any(token in json.dumps(meta) for token in NUMBERS + ["notanumber"])


def test_the_model_in_the_repository_is_not_stored_in_training_order():
    model = MicrModel.load(MODELS / "micr_e13b.npz")
    assert model.meta.get("order") == "mixed"
    first_digits = model.labels[0::6]                           # the first digit of every training cheque, if stored in order
    most_common = max(first_digits.count(d) for d in set(first_digits))
    assert most_common / len(first_digits) < 0.5                # in reading order nearly all of them are the same digit


# ---------------------------------------------------------------- the dataset audit reports
def test_ocr_dataset_report_refers_to_images_by_position(tmp_path):
    _noise_png(tmp_path / "train/axis/246810.png", 1)
    _noise_png(tmp_path / "train/axis/135792.png", 2)
    (tmp_path / "train/axis/271828.png").write_bytes(b"this is not an image")                # cannot be decoded
    (tmp_path / "val/axis").mkdir(parents=True)
    (tmp_path / "val/axis/112358.png").write_bytes((tmp_path / "train/axis/246810.png").read_bytes())   # same picture in two splits
    _noise_png(tmp_path / "test/axis/314159.png", 4)
    ok, buf = cv2.imencode(".png", np.zeros((40, 80, 3), np.uint8))
    (tmp_path / "test/axis/161803.jpeg").write_bytes(buf.tobytes())                          # PNG content, .jpeg name

    report = datasets.audit_ocr_dataset(tmp_path)
    text = json.dumps(report)
    assert not any(number in text for number in NUMBERS)
    assert report["corrupted_images"] == ["train/axis/#3"]
    assert report["extension_content_mismatches"] == ["test/axis/#1 (named .jpeg, content png)",
                                                       "train/axis/#3 (named .png, content unknown)"]
    assert report["exact_duplicate_groups"] == [["train/axis/#2", "val/axis/#1"]]
    assert report["cross_split_leakage"]["same_content"] == [["train/axis/#2", "val/axis/#1"]]
    assert report["total_images"] == 6 and report["images_per_split"] == {"test": 2, "train": 3, "val": 1}


def test_signature_dataset_report_refers_to_images_by_position(tmp_path):
    account = "900000000000999"
    for kind in ("signature_dataset", "forged_signatures"):
        (tmp_path / kind / "train" / "axis").mkdir(parents=True)
    _noise_png(tmp_path / "signature_dataset/train/axis/111111111111111.png", 1)           # listed and present
    _noise_png(tmp_path / "forged_signatures/train/axis/111111111111111_forged.png", 2)
    _noise_png(tmp_path / "signature_dataset/train/axis/stray.png", 3)                      # on disk, in no manifest
    with (tmp_path / "genuine_signatures.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["filename", "bank", "signer_id", "split"])
        w.writerow(["111111111111111.png", "axis", "person_001", "train"])
        w.writerow([f"{account}.png", "axis", "person_002", "train"])                       # listed, image missing
    with (tmp_path / "forged_signatures.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["filename", "bank", "target_signer_id", "split"])
        w.writerow(["111111111111111_forged.png", "axis", "person_001", "train"])

    report = datasets.audit_signature_dataset(tmp_path)
    text = json.dumps(report)
    assert account not in text and "111111111111111" not in text and "stray" not in text
    assert report["missing_images"] == ["genuine #2"]
    assert report["files_on_disk_not_in_manifest"] == ["unlisted file #1"]
