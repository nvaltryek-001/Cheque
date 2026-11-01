"""The persistence checker (tests/persistence_check.py) must notice every way a deployment can lose data.

A checker that always says PASS is worse than none. Each scenario below starts a real HTTP server on this machine,
fills it with records, takes a fingerprint, then "redeploys": the same data folders (must PASS) or damaged / empty ones
(must FAIL for the right reason)."""
import importlib.util
import json
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from werkzeug.serving import make_server

import persistence_check as pc
from app import create_app
from conftest import ADMIN_PW, MODELS, png_bytes, synthetic_signature, upload
from test_enrollment import enroll, new_session, new_signer


class Live:
    """A real server (HTTP, threaded) over data folders that survive a restart."""

    def __init__(self, root: Path):
        self.root = root
        self.server = self.thread = None
        self.dirs = self._dirs("disk1")

    def _dirs(self, name):
        base = self.root / name
        models = base / "models"
        if MODELS.is_dir():
            shutil.copytree(MODELS, models)
        else:
            models.mkdir(parents=True)
        (base / "reports" / "final").mkdir(parents=True)
        (base / "reports" / "final" / "MODEL_EVALUATION.md").write_text("shipped report")
        (base / "instance").mkdir(parents=True)
        return base

    def config(self):
        b = self.dirs
        return {"TESTING": True, "INSTANCE_DIR": b / "instance", "DATABASE": b / "instance" / "chequeverify.db",
                "STORAGE_DIR": b / "instance" / "storage", "MODELS_DIR": b / "models", "REPORTS_DIR": b / "reports",
                "SECRET_KEY": "", "RATE_LIMIT_PER_MIN": 0, "MAX_CONTENT_LENGTH": 4 * 1024 * 1024,
                "FRONTEND_DIST": self.root / "nodist", "OCR_DATASET_DIR": self.root / "none", "SIGNATURE_DATASET_DIR": self.root / "none2"}

    def start(self):
        self.app = create_app(self.config())
        self.server = make_server("127.0.0.1", 0, self.app, threaded=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.api = pc.Api(f"http://127.0.0.1:{self.server.server_port}")

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.thread.join(10)
            self.server.server_close()
            self.server = None

    def restart(self):
        self.stop()
        self.start()

    def new_disk(self):                          # the "free plan" case: the redeploy comes up on an empty disk
        self.stop()
        self.dirs = self._dirs("disk2")
        self.start()

    # --- helpers for damage
    @property
    def instance(self):
        return self.dirs / "instance"

    def fill(self):
        """Records of every kind the fingerprint covers, made through the application itself."""
        client = self.app.test_client()
        token = client.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PW}).json["data"]["token"]
        admin = {"Authorization": "Bearer " + token}
        sid = new_signer(client, admin, "S-PERSIST")
        s1, s2 = new_session(client, admin, sid, "2026-09-12"), new_session(client, admin, sid, "2026-10-03")
        for seed, sess in ((11, s1), (12, s1), (13, s1), (14, s2), (15, s2)):
            assert enroll(client, admin, sid, seed, sess).status_code == 201
        r = client.post("/api/cheques", headers=admin, data=upload(png_bytes(synthetic_signature(3)), "cheque.png"),
                        content_type="multipart/form-data")
        assert r.status_code in (200, 201), r.get_json()
        r = client.post("/api/verifications", headers=admin, data=upload(png_bytes(synthetic_signature(11)), signer_id=str(sid)),
                        content_type="multipart/form-data")
        assert r.status_code in (200, 201), r.get_json()


@pytest.fixture()
def live(tmp_path, monkeypatch):
    monkeypatch.setenv("CV_ADMIN_PASSWORD", ADMIN_PW)
    lv = Live(tmp_path)
    lv.start()
    lv.fill()
    yield lv
    lv.stop()


def snapshot_then(lv, damage, allow=()):
    """Snapshot, apply `damage` (a redeploy of some kind), verify. Returns (problems, notes)."""
    state = pc.snapshot(lv.api, ADMIN_PW)
    damage(lv)
    return pc.verify(lv.api, ADMIN_PW, state, set(allow), lock_window=300)


def test_the_snapshot_really_covers_every_kind_of_record(live):
    state = pc.snapshot(live.api, ADMIN_PW)
    assert state["signers"]["S-PERSIST"]["enrollment"]["independent_samples"] == 5
    assert len(state["signers"]["S-PERSIST"]["specimens"]) == 5
    assert all(len(h["original"]) == 64 and len(h["processed"]) == 64 for h in state["signers"]["S-PERSIST"]["specimens"].values())
    assert len(state["cheques"]) == 1 and all(len(c["image"]) == 64 for c in state["cheques"].values())
    assert len(state["verifications"]) == 1 and all(len(v["query_image"]) == 64 for v in state["verifications"].values())
    assert state["settings"]["retention.days"] == pc.SETTING_VALUE
    assert f"{pc.LOCK_USER}:operator:1" in state["users"]
    assert "MODEL_EVALUATION.md" in state["reports"] and state["audit_log_total"] > 10


def test_a_restart_on_the_same_disk_passes(live):
    problems, notes = snapshot_then(live, lambda lv: lv.restart())
    assert problems == [] and notes == []


def test_two_restarts_and_a_newer_release_that_ships_new_reports_pass_when_allowed(live):
    def redeploy(lv):
        lv.restart()
        (lv.dirs / "reports" / "final" / "MODEL_EVALUATION.md").write_text("a newer shipped report")
        lv.restart()
    problems, notes = snapshot_then(live, redeploy, allow=("reports",))
    assert problems == [] and any("MODEL_EVALUATION.md" in n for n in notes)


def test_an_empty_disk_fails_loudly(live):
    problems, _ = snapshot_then(live, lambda lv: lv.new_disk())
    text = "\n".join(problems)
    assert "the secret key did not persist" in text
    assert "S-PERSIST" in text and "signers" in text and "cheques" in text and "verifications" in text
    assert "settings/retention.days" in text and f"users: {pc.LOCK_USER}" in text
    assert "sign-in throttle was forgotten" in text


def test_a_lost_secret_key_is_noticed(live):
    problems, _ = snapshot_then(live, lambda lv: ((lv.instance / "secret_key").unlink(), lv.restart()))
    assert any("secret key did not persist" in p for p in problems)
    assert not any("signers" in p or "cheques" in p for p in problems)           # and nothing else was blamed


def test_a_lost_cheque_image_is_noticed(live):
    def damage(lv):
        images = sorted((lv.instance / "storage" / "cheques").iterdir())
        assert images
        images[0].unlink()
        lv.restart()
    problems, _ = snapshot_then(live, damage)
    assert any(p.startswith("cheques/") and "image" in p for p in problems)


def test_a_lost_specimen_image_is_noticed(live):
    def damage(lv):
        sorted((lv.instance / "storage" / "specimens").iterdir())[0].unlink()
        lv.restart()
    problems, _ = snapshot_then(live, damage)
    assert any(p.startswith("signers/S-PERSIST/specimens/") for p in problems)


def test_an_altered_image_is_noticed_not_just_a_missing_one(live):
    def damage(lv):
        victim = sorted((lv.instance / "storage" / "queries").iterdir())[0]
        victim.write_bytes(victim.read_bytes()[:-20])                            # cut short
        lv.restart()
    problems, _ = snapshot_then(live, damage)
    assert any(p.startswith("verifications/") and "query_image" in p for p in problems)


def test_a_reset_setting_is_noticed(live):
    import sqlite3

    def damage(lv):
        lv.stop()
        conn = sqlite3.connect(str(lv.instance / "chequeverify.db"))
        conn.execute("DELETE FROM settings WHERE key=?", (pc.SETTING_KEY,))
        conn.commit()
        conn.close()
        lv.start()
    problems, _ = snapshot_then(live, damage)
    assert any("settings" in p and pc.SETTING_KEY in p for p in problems)


def test_a_forgotten_sign_in_throttle_is_noticed(live):
    import sqlite3

    def damage(lv):
        lv.stop()
        conn = sqlite3.connect(str(lv.instance / "chequeverify.db"))
        conn.execute("DELETE FROM login_attempts")
        conn.commit()
        conn.close()
        lv.start()
    problems, _ = snapshot_then(live, damage)
    assert any("throttle was forgotten" in p for p in problems)


def _need_shipped_model(live):
    if not (live.dirs / "models" / "signature_fusion.json").is_file():
        pytest.skip("no shipped signature model in this checkout")


def test_a_missing_model_is_noticed(live):
    _need_shipped_model(live)
    problems, _ = snapshot_then(live, lambda lv: ((lv.dirs / "models" / "signature_fusion.json").unlink(), lv.restart()))
    assert any(p.startswith("models/signature") for p in problems)


def test_a_newer_release_that_ships_a_different_model_fails_unless_it_is_allowed(live):
    from app.signature.compare import FusionModel
    _need_shipped_model(live)
    state = pc.snapshot(live.api, ADMIN_PW)
    live.stop()
    FusionModel([0.5] * 8, 0.1, [0.0] * 8, [1.0] * 8, {"match": 0.8, "mismatch": 0.4},
                {"version": "v-next-release", "trained_at": "2026-11-02T00:00:00"}).save(live.dirs / "models" / "signature_fusion.json")
    live.start()
    problems, _ = pc.verify(live.api, ADMIN_PW, state, set(), 300)
    assert any(p.startswith("models/signature") and "v-next-release" in p for p in problems)
    problems, notes = pc.verify(live.api, ADMIN_PW, state, {"models"}, 300)
    assert problems == [] and any(n.startswith("models/signature") for n in notes)


def test_a_wrong_password_or_unreachable_server_is_exit_2_not_a_pass(live):
    state = pc.snapshot(live.api, ADMIN_PW)
    problems, _ = pc.verify(live.api, "not-the-password", state, set(), 300)
    assert any("admin sign-in failed" in p for p in problems)
    live.stop()
    with pytest.raises(Exception):
        pc.verify(live.api, ADMIN_PW, state, set(), 300)
    live.start()


# ------------------------------------------------------------------ the command line, as CI and the rehearsal use it
TOOL = Path(pc.__file__)


def cli(*args, stdin=None):
    return subprocess.run([sys.executable, str(TOOL), *args], input=stdin, capture_output=True, text=True, timeout=300)


def test_command_line_snapshot_then_verify_from_a_file_and_from_standard_input(live, tmp_path):
    snap = cli("snapshot", live.api.base, ADMIN_PW)
    assert snap.returncode == 0, snap.stderr
    state_file = tmp_path / "state.json"
    state_file.write_text(snap.stdout)
    live.restart()
    from_file = cli("verify", live.api.base, ADMIN_PW, "--state", str(state_file))
    assert from_file.returncode == 0 and "PASS everything in the snapshot survived" in from_file.stdout, from_file.stdout + from_file.stderr
    from_stdin = cli("verify", live.api.base, ADMIN_PW, stdin=snap.stdout)               # docker exec -i ... < state.json
    assert from_stdin.returncode == 0 and "PASS" in from_stdin.stdout


def test_command_line_exit_codes_one_for_data_loss_two_for_could_not_run(live, tmp_path):
    snap = cli("snapshot", live.api.base, ADMIN_PW)
    live.new_disk()
    lost = cli("verify", live.api.base, ADMIN_PW, stdin=snap.stdout)
    assert lost.returncode == 1 and "FAIL" in lost.stdout and "PASS" not in lost.stdout
    wrong = cli("verify", live.api.base, "not-the-password", stdin=snap.stdout)
    assert wrong.returncode == 1 and "admin sign-in failed" in wrong.stdout
    broken = cli("verify", live.api.base, ADMIN_PW, stdin="this is not json")
    assert broken.returncode == 2 and "could not run" in broken.stderr
    live.stop()
    down = cli("verify", live.api.base, ADMIN_PW, stdin=snap.stdout)
    assert down.returncode == 2 and "could not run" in down.stderr
    live.start()


def test_the_memory_probe_passes_on_a_healthy_server_and_fails_when_it_cannot_sign_in(live):
    spec = importlib.util.spec_from_file_location("memory_probe", Path(pc.__file__).resolve().parents[2] / "scripts" / "memory_probe.py")
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    assert probe.main([live.api.base, ADMIN_PW, "--side", "300", "--parallel", "4"]) == 0
    assert probe.main([live.api.base, "wrong-password", "--side", "300", "--parallel", "2"]) == 1
    live.stop()
    assert probe.main([live.api.base, ADMIN_PW, "--side", "300", "--parallel", "2"]) == 1
    live.start()
