import io
import os
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
ROOT = BACKEND.parent

from app import create_app  # noqa: E402

OCR_DS = Path(os.environ.get("CV_OCR_DATASET", ROOT / "dataset"))
SIG_DS = Path(os.environ.get("CV_SIGNATURE_DATASET", ROOT / "signature"))
MODELS = BACKEND / "models_store"
ADMIN_PW = "Admin-pass-123"


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("CV_ADMIN_PASSWORD", ADMIN_PW)
    models_dir = tmp_path / "models"
    if MODELS.is_dir():
        shutil.copytree(MODELS, models_dir)
    a = create_app({
        "TESTING": True,
        "INSTANCE_DIR": tmp_path, "DATABASE": tmp_path / "test.db", "STORAGE_DIR": tmp_path / "storage",
        "MODELS_DIR": models_dir, "REPORTS_DIR": tmp_path / "reports", "SECRET_KEY": "test-secret",
        "OCR_DATASET_DIR": OCR_DS, "SIGNATURE_DATASET_DIR": SIG_DS, "FRONTEND_DIST": tmp_path / "nodist",
        "MAX_CONTENT_LENGTH": 4 * 1024 * 1024, "LOGIN_MAX_ATTEMPTS": 3,
    })
    yield a


@pytest.fixture()
def client(app):
    return app.test_client()


def login(client, username="admin", password=ADMIN_PW):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.json
    return {"Authorization": "Bearer " + r.json["data"]["token"]}


@pytest.fixture()
def admin(client):
    return login(client)


@pytest.fixture()
def operator(client, admin):
    client.post("/api/users", headers=admin, json={"username": "op1", "password": "Operator-123", "role": "operator"})
    return login(client, "op1", "Operator-123")


@pytest.fixture()
def auditor(client, admin):
    client.post("/api/users", headers=admin, json={"username": "aud1", "password": "Auditor-123", "role": "auditor"})
    return login(client, "aud1", "Auditor-123")


def png_bytes(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()


def synthetic_signature(seed: int = 0, w: int = 320, h: int = 100) -> np.ndarray:
    rng = np.random.default_rng(seed)
    img = np.full((h, w, 3), 245, np.uint8)
    pts = np.cumsum(rng.normal(0, 6, (40, 2)), axis=0)
    pts = pts - pts.min(0)
    pts = pts / max(pts.max(), 1) * [w - 40, h - 30] + [20, 15]
    cv2.polylines(img, [pts.astype(np.int32)], False, (30, 30, 120), 2, cv2.LINE_AA)
    cv2.line(img, (25, h - 20), (w - 30, h - 25), (30, 30, 120), 2)
    return img


def upload(data: bytes, name: str = "img.png", field: str = "file", **extra):
    d = {field: (io.BytesIO(data), name)}
    d.update(extra)
    return d


def dataset_cheque(split="test", bank="axis") -> Path | None:
    for s in (split, "vaildate", "validate", "val"):
        d = OCR_DS / s / bank
        if d.is_dir():
            files = sorted(d.iterdir())
            if files:
                return files[0]
    return None
