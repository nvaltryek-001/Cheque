"""scripts/live_check.py is what the owner runs against the hosted service, so it has to work the first time and it has to be
safe to paste its output anywhere: no password, no sign-in token, no file name, no value read from a cheque.

Each test starts a real HTTP server on this machine (the same way the persistence-check tests do) and runs the script against it."""
import csv
import importlib.util
import sys
import threading
import time
import types
from pathlib import Path

import cv2
import numpy as np
import pytest
from werkzeug.serving import make_server

from app import create_app
from conftest import ADMIN_PW, MODELS, png_bytes, synthetic_signature

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "live_check.py"


def load_script():
    spec = importlib.util.spec_from_file_location("live_check", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)               # a fresh module each time: its result lists start empty
    return module


@pytest.fixture()
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("CV_ADMIN_PASSWORD", ADMIN_PW)
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text('<!doctype html><div id="root"></div><script type="module" src="/assets/app.js"></script>'
                                     '<link rel="stylesheet" href="/assets/app.css">')
    (dist / "assets" / "app.js").write_text("console.log(1)")
    (dist / "assets" / "app.css").write_text("body{margin:0}")
    models = tmp_path / "models"
    if MODELS.is_dir():
        import shutil
        shutil.copytree(MODELS, models)
    app = create_app({"TESTING": True, "INSTANCE_DIR": tmp_path / "instance", "DATABASE": tmp_path / "instance" / "t.db",
                      "STORAGE_DIR": tmp_path / "instance" / "storage", "MODELS_DIR": models, "REPORTS_DIR": tmp_path / "reports",
                      "SECRET_KEY": "test-secret", "RATE_LIMIT_PER_MIN": 0, "MAX_CONTENT_LENGTH": 4 * 1024 * 1024,
                      "FRONTEND_DIST": dist, "OCR_DATASET_DIR": tmp_path / "none", "SIGNATURE_DATASET_DIR": tmp_path / "none2",
                      "STORAGE_TEMPORARY": True})
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield app, f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()
    thread.join(10)
    srv.server_close()


def make_cheques(root: Path):
    for bank, name in (("axis", "private-looking-name-1.png"), ("canara", "private-looking-name-2.png")):
        page = np.full((470, 1024, 3), 248, np.uint8)
        cv2.putText(page, "CANARA BANK  IFSC: CNRB0002854", (40, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (60, 60, 60), 2)
        cv2.putText(page, "Pay  Test Payee", (40, 120), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (20, 20, 20), 2)
        page[290:390, 690:1010] = synthetic_signature(7)
        (root / bank).mkdir(parents=True, exist_ok=True)
        (root / bank / name).write_bytes(png_bytes(page))


def make_signatures(root: Path):
    for kind in ("signature_dataset", "forged_signatures"):
        (root / kind / "train" / "axis").mkdir(parents=True)
    genuine, forged = [], []
    for n in (1, 2):
        sig = synthetic_signature(100 + n)
        gname, fname = f"900000000000{n:03d}.png", f"900000000000{n:03d}_forged.png"
        (root / "signature_dataset/train/axis" / gname).write_bytes(png_bytes(sig))
        edited = sig.copy()
        cv2.line(edited, (60, 20), (200, 80), (40, 40, 40), 2)
        (root / "forged_signatures/train/axis" / fname).write_bytes(png_bytes(edited))
        genuine.append([gname, "axis", f"person_00{n}", "train"])
        forged.append([fname, "axis", f"person_00{n}", "train"])
    for name, header, rows in (("genuine_signatures.csv", ["filename", "bank", "signer_id", "split"], genuine),
                               ("forged_signatures.csv", ["filename", "bank", "target_signer_id", "split"], forged)):
        with (root / name).open("w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(header)
            writer.writerows(rows)


def run(mod, argv, monkeypatch, password=ADMIN_PW):
    monkeypatch.setattr(sys, "argv", ["live_check.py", *argv])
    monkeypatch.setenv("CV_LIVE_PASSWORD", password)
    return mod.main()


def test_a_full_run_passes_prints_nothing_private_and_cleans_up(server, tmp_path, monkeypatch, capsys):
    app, base = server
    cheques, sigs = tmp_path / "cheques", tmp_path / "sigs"
    make_cheques(cheques)
    make_signatures(sigs)

    code = run(load_script(), [base, "--cheques", str(cheques), "--signatures", str(sigs), "--limit", "2", "--signers", "2", "--wait", "20"],
               monkeypatch)
    out = capsys.readouterr().out
    assert code == 0, out
    assert "0 failed" in out
    for expected in ("health: /api/health answers ok", "its script and style files load", "anonymous visitor refused: GET /api/cheques",
                     "a wrong password is refused", "sign-in as 'admin'", "cheque 2/2", "signer 2/2: altered copy 1 verified",
                     "batch: two cheques sent one by one share one batch id", "storage: TEMPORARY"):
        assert expected in out, expected
    # nothing private in the output: not the password, not a file name, not a signer or account identifier
    for secret in (ADMIN_PW, "Bearer", "private-looking-name", "9000000000", "person_00", "Test Payee", "CNRB0002854"):
        assert secret not in out, secret
    # and the records it created are gone again
    client = app.test_client()
    token = client.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PW}).json["data"]["token"]
    headers = {"Authorization": "Bearer " + token}
    assert client.get("/api/cheques", headers=headers).json["meta"]["total"] == 0
    assert client.get("/api/signers", headers=headers).json["meta"]["total"] == 0
    assert client.get("/api/verifications", headers=headers).json["meta"]["total"] == 0


def test_keep_leaves_the_records_for_the_application(server, tmp_path, monkeypatch, capsys):
    app, base = server
    cheques = tmp_path / "cheques"
    make_cheques(cheques)
    assert run(load_script(), [base, "--cheques", str(cheques), "--limit", "1", "--keep"], monkeypatch) == 0
    capsys.readouterr()
    client = app.test_client()
    token = client.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PW}).json["data"]["token"]
    assert client.get("/api/cheques", headers={"Authorization": "Bearer " + token}).json["meta"]["total"] == 1


def test_a_wrong_password_stops_after_the_public_checks_and_is_not_echoed(server, monkeypatch, capsys):
    app, base = server
    wrong = "not-the-password-0000"
    code = run(load_script(), [base, "--wait", "20"], monkeypatch, password=wrong)
    out = capsys.readouterr().out
    assert code == 2
    assert "Sign-in failed" in out and "FAIL  sign-in as 'admin'" in out
    assert wrong not in out and ADMIN_PW not in out


def test_a_server_that_does_not_answer_gives_exit_status_2(monkeypatch, capsys):
    mod = load_script()
    monkeypatch.setattr(mod, "time", types.SimpleNamespace(monotonic=time.monotonic, sleep=lambda _s: None))   # do not really wait
    sock_port = 9                                                    # the "discard" port: nothing listens there
    code = run(mod, [f"http://127.0.0.1:{sock_port}", "--wait", "1"], monkeypatch)
    out = capsys.readouterr().out
    assert code == 2 and "FAIL  health" in out


def test_plain_http_to_another_host_is_refused(monkeypatch, capsys):
    code = run(load_script(), ["http://example.com"], monkeypatch)
    assert code == 2 and "https://" in capsys.readouterr().out
