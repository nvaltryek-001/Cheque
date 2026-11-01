"""Processing a sample from the server's dataset folder ("demo sample") - POST /api/cheques/from-sample.

These tests build their own small dataset folder from a synthetic image, so they run without the private dataset."""
import hashlib

import cv2
import numpy as np
import pytest

from conftest import png_bytes


def _cheque_like() -> bytes:
    img = np.full((420, 900, 3), 250, np.uint8)
    cv2.putText(img, "SAMPLE BANK", (40, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (20, 20, 20), 2, cv2.LINE_AA)
    cv2.putText(img, "Pay  TEST PAYEE ONLY", (40, 160), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (20, 20, 20), 2, cv2.LINE_AA)
    cv2.putText(img, "Rupees One Thousand Only", (40, 220), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (20, 20, 20), 2, cv2.LINE_AA)
    cv2.putText(img, "1,000.00", (680, 160), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (20, 20, 20), 2, cv2.LINE_AA)
    return png_bytes(img)


@pytest.fixture()
def sample_folder(app, tmp_path):
    root = tmp_path / "dataset"
    bank = root / "test" / "axis"
    bank.mkdir(parents=True)
    data = _cheque_like()
    (bank / "100200.png").write_bytes(data)
    app.config["OCR_DATASET_DIR"] = root
    return root, data


def _post(client, headers, **body):
    return client.post("/api/cheques/from-sample", headers=headers, json=body)


def test_a_sample_is_processed_like_an_upload_and_the_original_is_left_alone(client, operator, sample_folder):
    root, data = sample_folder
    before = hashlib.sha256((root / "test" / "axis" / "100200.png").read_bytes()).hexdigest()
    r = _post(client, operator, split="test", bank="axis", filename="100200.png")
    assert r.status_code == 201, r.json
    cheque = r.json["data"]
    assert cheque["original_filename"] == "100200.png" and cheque["duplicate_of"] is None
    assert cheque["final"] is not None and cheque["processing_ms"] >= 0      # the real pipeline ran
    assert hashlib.sha256((root / "test" / "axis" / "100200.png").read_bytes()).hexdigest() == before
    again = _post(client, operator, split="test", bank="axis", filename="100200.png")
    assert again.status_code == 201 and again.json["data"]["duplicate_of"] == cheque["id"]
    assert client.get(f"/api/cheques/{cheque['id']}", headers=operator).status_code == 200


def test_a_sample_is_named_never_addressed_by_a_path(client, operator, sample_folder):
    for bad in ("../100200.png", "test/axis/100200.png", "..%2f100200.png", "", " ", "a b.png", "100200.png\x00"):
        assert _post(client, operator, split="test", bank="axis", filename=bad).status_code == 400, bad
    assert _post(client, operator, split="test", bank="axis", filename="999.png").status_code == 404
    assert _post(client, operator, split="test", bank="icici", filename="100200.png").status_code == 404
    assert _post(client, operator, split="train", bank="axis", filename="100200.png").status_code == 404
    assert client.post("/api/cheques/from-sample", headers=operator, data="not json",
                       content_type="text/plain").status_code in (400, 415)


def test_a_link_leading_out_of_the_dataset_folder_is_not_read(client, operator, sample_folder, tmp_path):
    root, data = sample_folder
    outside = tmp_path / "outside.png"
    outside.write_bytes(data)
    link = root / "test" / "axis" / "linked.png"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("this system cannot create symbolic links")
    assert _post(client, operator, split="test", bank="axis", filename="linked.png").status_code == 404


def test_only_signed_in_operators_and_administrators_may_process_samples(client, admin, operator, auditor, sample_folder):
    body = dict(split="test", bank="axis", filename="100200.png")
    assert client.post("/api/cheques/from-sample", json=body).status_code == 401
    assert _post(client, auditor, **body).status_code == 403
    assert _post(client, admin, **body).status_code == 201


def test_a_missing_dataset_folder_gives_a_clear_not_found(client, operator, app, tmp_path):
    app.config["OCR_DATASET_DIR"] = tmp_path / "no-such-folder"
    r = _post(client, operator, split="test", bank="axis", filename="100200.png")
    assert r.status_code == 404 and r.json["error"]["code"] == "not_found"


def test_a_sample_over_the_size_limit_is_refused(client, operator, sample_folder, app):
    root, _ = sample_folder
    big = root / "test" / "axis" / "huge.png"
    big.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * (app.config["MAX_CONTENT_LENGTH"] + 10))
    r = _post(client, operator, split="test", bank="axis", filename="huge.png")
    assert r.status_code == 413 and r.json["error"]["code"] == "file_too_large"


def test_a_sample_that_is_not_an_image_is_refused_like_an_upload(client, operator, sample_folder):
    root, _ = sample_folder
    (root / "test" / "axis" / "fake.png").write_bytes(b"this is not an image at all")
    r = _post(client, operator, split="test", bank="axis", filename="fake.png")
    assert r.status_code == 415 and r.json["error"]["code"] == "unsupported_type"
