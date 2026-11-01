"""Crash safety and shutdown: what must hold when the server is stopped, killed or restarted.

* files that matter (models, reports, the secret key, the evaluation ledger) are never left half-written,
* a job that a stopped server never finished does not show as "running" forever,
* SIGTERM - what `docker stop` and a Render redeploy send - ends the server promptly, also as PID 1 of a container.
"""
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import textwrap
import time
import urllib.request
from pathlib import Path

import numpy as np
import pytest

from app.config import Config
from app.db import connect
from app.ocr.micr import MicrModel
from app.services import atomic, lifecycle
from app.signature.compare import FusionModel
from app.signature import protocol

BACKEND = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------------ atomic writes
def test_a_write_replaces_the_file_and_leaves_nothing_behind(tmp_path):
    target = tmp_path / "sub" / "report.json"
    atomic.write_json(target, {"a": 1})
    atomic.write_json(target, {"a": 2})
    assert json.loads(target.read_text()) == {"a": 2}
    assert [p.name for p in target.parent.iterdir()] == ["report.json"]
    assert (target.stat().st_mode & 0o077) == 0                       # private, whatever the umask


def test_an_error_while_writing_keeps_the_old_file_and_removes_the_temporary_one(tmp_path):
    target = tmp_path / "model.json"
    atomic.write_text(target, "old")
    with pytest.raises(RuntimeError):
        with atomic.open_for_write(target) as f:
            f.write(b"new but incompl")
            raise RuntimeError("boom")
    assert target.read_text() == "old"
    assert [p.name for p in tmp_path.iterdir()] == ["model.json"]


def test_a_process_killed_in_the_middle_of_a_write_leaves_the_old_file_whole(tmp_path):
    """The real thing: SIGKILL (what a platform sends when the grace period is over) while a large file is being written."""
    target = tmp_path / "model.json"
    atomic.write_text(target, "the old, complete content")
    script = textwrap.dedent(f"""
        import os, signal, sys
        sys.path.insert(0, {str(BACKEND)!r})
        from app.services import atomic
        with atomic.open_for_write({str(target)!r}) as f:
            f.write(b"x" * 1_000_000)
            f.flush()
            os.kill(os.getpid(), signal.SIGKILL)          # dies half way through the write
    """)
    proc = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, timeout=60)
    assert proc.returncode == -signal.SIGKILL
    assert target.read_text() == "the old, complete content"          # never truncated, never a mixture
    leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(atomic.TMP_PREFIX)]
    assert len(leftovers) == 1                                        # the crash left only a private temporary file
    assert atomic.sweep(tmp_path) == 0                                # too young to be removed: a write may be running
    old = time.time() - 3600
    os.utime(leftovers[0], (old, old))
    assert atomic.sweep(tmp_path) == 1 and [p.name for p in tmp_path.iterdir()] == ["model.json"]


def test_sweep_touches_only_stale_temporary_files(tmp_path):
    keep = [tmp_path / "models.npz", tmp_path / "report.json", tmp_path / ".gitkeep"]
    for p in keep:
        p.write_text("x")
    young, stale = tmp_path / ".tmp-young", tmp_path / ".tmp-stale"
    young.write_text("x")
    stale.write_text("x")
    old = time.time() - 600
    os.utime(stale, (old, old))
    assert atomic.sweep(tmp_path) == 1
    assert stale.exists() is False and young.exists() and all(p.exists() for p in keep)
    assert atomic.sweep(tmp_path / "does-not-exist") == 0


def test_saving_a_model_is_all_or_nothing(tmp_path, monkeypatch):
    model = FusionModel([0.5] * 8, 0.1, [0.0] * 8, [1.0] * 8, {"match": 0.8, "mismatch": 0.4}, {"version": "v-old"})
    path = tmp_path / "signature_fusion.json"
    model.save(path)
    newer = FusionModel([0.9] * 8, 0.2, [0.0] * 8, [1.0] * 8, {"match": 0.7, "mismatch": 0.3}, {"version": "v-new"})
    monkeypatch.setattr(os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("killed before the rename")))
    with pytest.raises(OSError):
        newer.save(path)
    monkeypatch.undo()
    assert FusionModel.load(path).meta["version"] == "v-old"          # the model in use is untouched and still loads
    newer.save(path)
    assert FusionModel.load(path).meta["version"] == "v-new"


def test_saving_the_micr_templates_is_all_or_nothing(tmp_path, monkeypatch):
    from app.ocr.micr import GLYPH_H, GLYPH_W
    glyphs = np.random.default_rng(1).random((4, GLYPH_H * GLYPH_W)).astype("float32")
    old = MicrModel(glyphs, ["0", "1", "2", "3"], {"version": "old"})
    path = tmp_path / "micr_e13b.npz"
    old.save(path)
    new = MicrModel(glyphs, ["0", "1", "2", "3"], {"version": "new"})
    monkeypatch.setattr(os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("killed before the rename")))
    with pytest.raises(OSError):
        new.save(path)
    monkeypatch.undo()
    assert MicrModel.load(path).meta["version"] == "old"
    new.save(path)
    assert MicrModel.load(path).meta["version"] == "new"
    assert [p.name for p in tmp_path.iterdir()] == ["micr_e13b.npz"]


def test_the_evaluation_ledger_is_written_atomically(tmp_path, monkeypatch):
    path = tmp_path / "ledger.json"
    protocol.write_ledger(path, [{"use": 1}])
    monkeypatch.setattr(os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("killed before the rename")))
    with pytest.raises(OSError):
        protocol.write_ledger(path, [{"use": 1}, {"use": 2}])
    monkeypatch.undo()
    assert json.loads(path.read_text()) == [{"use": 1}]


# ------------------------------------------------------------------ the secret key
def test_a_missing_or_damaged_secret_key_is_replaced_by_a_strong_one(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(Config, "SECRET_KEY", "")
    monkeypatch.setattr(Config, "INSTANCE_DIR", tmp_path)
    first = Config.resolve_secret()
    assert len(first) == 64 and (tmp_path / "secret_key").read_text() == first
    assert Config.resolve_secret() == first                           # stable across restarts
    for damaged in ("", "   \n", "abc123"):                           # a crash while the file was first written
        (tmp_path / "secret_key").write_text(damaged)
        replaced = Config.resolve_secret()
        assert len(replaced) == 64 and replaced != first
        assert (tmp_path / "secret_key").read_text() == replaced
        assert Config.resolve_secret() == replaced
    assert "too short" in caplog.text


# ------------------------------------------------------------------ a damaged model file
def test_a_damaged_model_file_means_no_model_not_a_server_error(app, client, admin):
    from app.services import models
    models_dir = Path(app.config["MODELS_DIR"])
    models_dir.mkdir(parents=True, exist_ok=True)
    (models_dir / models.MICR_FILE).write_bytes(b"PK\x03\x04 this is not a real archive")
    (models_dir / models.FUSION_FILE).write_text('{"weights": [0.1, 0.2')               # cut off
    with app.app_context():
        assert models.micr_model() is None and models.fusion_model() is None
    r = client.get("/api/models/status", headers=admin)
    assert r.status_code == 200
    data = r.get_json()["data"]
    assert data["micr_model"]["available"] is False and data["micr_model"]["error"]
    assert data["signature_model"]["available"] is False and data["signature_model"]["error"]
    assert data["signature_model"]["version"] == "baseline-mean-v1"                     # the untrained baseline, said openly
    assert "Traceback" not in json.dumps(data)


def test_a_repaired_model_file_is_picked_up_and_the_warning_clears(app):
    from app.services import models
    path = Path(app.config["MODELS_DIR"]) / models.FUSION_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json")
    with app.app_context():
        assert models.fusion_model() is None and models.status()["signature_model"]["error"]
    FusionModel([0.5] * 8, 0.1, [0.0] * 8, [1.0] * 8, {"match": 0.8, "mismatch": 0.4}, {"version": "v-restored"}).save(path)
    later = time.time() + 30
    os.utime(path, (later, later))
    with app.app_context():
        assert models.fusion_model().meta["version"] == "v-restored"
        assert models.status()["signature_model"]["error"] is None


# ------------------------------------------------------------------ cached dataset audit
def test_a_damaged_audit_cache_is_rebuilt_instead_of_failing_every_request(app, client, admin):
    first = client.get("/api/datasets/signature/audit", headers=admin)
    assert first.status_code == 200
    cache = Path(app.config["REPORTS_DIR"]) / "final" / "SIGNATURE_DATASET_AUDIT.json"
    assert cache.is_file()
    cache.write_text('{"cut off in the mid')                           # what an interrupted in-place write used to leave
    again = client.get("/api/datasets/signature/audit", headers=admin)
    assert again.status_code == 200 and again.get_json()["data"] == first.get_json()["data"]
    assert json.loads(cache.read_text()) == first.get_json()["data"]  # and the cache file is whole again
    cache.write_bytes(b"\xff\xfe\x00 not even text")                   # unreadable as text
    assert client.get("/api/datasets/signature/audit", headers=admin).status_code == 200


# ------------------------------------------------------------------ jobs closed at start-up
def _insert_run(app, status):
    conn = connect(str(app.config["DATABASE"]))
    try:
        cur = conn.execute("INSERT INTO model_runs(kind, status, config_json, started_at) VALUES('micr_train', ?, '{}', '2026-11-01T00:00:00')", (status,))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _run(app, run_id):
    conn = connect(str(app.config["DATABASE"]))
    try:
        return dict(conn.execute("SELECT * FROM model_runs WHERE id=?", (run_id,)).fetchone())
    finally:
        conn.close()


def test_jobs_a_stopped_server_never_finished_are_closed_at_start_up(app):
    running, finished = _insert_run(app, "running"), _insert_run(app, "completed")
    result = lifecycle.on_process_start(app)
    assert result["jobs_interrupted"] == 1
    row = _run(app, running)
    assert row["status"] == "failed" and row["finished_at"] and "interrupted" in row["message"]
    assert _run(app, finished)["status"] == "completed"
    assert lifecycle.on_process_start(app)["jobs_interrupted"] == 0     # idempotent


def test_start_up_removes_only_stale_temporary_files_in_the_persistent_folders(app):
    from app.services import storage
    models = Path(app.config["MODELS_DIR"])
    reports = Path(app.config["REPORTS_DIR"]) / "final"
    reports.mkdir(parents=True, exist_ok=True)
    with app.app_context():
        images = storage.area_dir("specimens")
    stale = [models / ".tmp-abc", reports / ".tmp-def", images / ".tmp-ghi", Path(app.config["INSTANCE_DIR"]) / ".tmp-jkl"]
    for p in stale:
        p.write_text("half")
        old = time.time() - 3600
        os.utime(p, (old, old))
    young = images / ".tmp-being-written-right-now"
    young.write_text("half")
    (models / "fusion.json").write_text("{}")
    (images / "0123456789abcdef.png").write_bytes(b"\x89PNG")
    assert lifecycle.on_process_start(app)["temp_files_removed"] == 4
    assert not any(p.exists() for p in stale)
    assert young.exists() and (models / "fusion.json").exists() and (images / "0123456789abcdef.png").exists()


def test_a_stored_image_is_written_whole_or_not_at_all(app, monkeypatch):
    from app.services import storage
    png = b"\x89PNG\r\n\x1a\n" + b"x" * 5000
    with app.app_context():
        name = storage.save_bytes("cheques", png, "png")
        folder = storage.area_dir("cheques")
        assert (folder / name).read_bytes() == png and ((folder / name).stat().st_mode & 0o077) == 0
        monkeypatch.setattr(os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("killed before the rename")))
        with pytest.raises(OSError):
            storage.save_bytes("cheques", png, "png")
        monkeypatch.undo()
        assert [p.name for p in folder.iterdir()] == [name]               # no partial image, no temporary file


def test_the_private_score_table_keeps_its_format_and_is_written_atomically(tmp_path):
    import csv as csvmod
    from app.ocr import scoring
    rows = [{"cheque_id": "c1", "file": "a.png", "field": "payee", "truth": "A, B", "predicted": 'say "x"', "correct": False,
             "confidence": 0.5, "status": "REVIEW", "issues": ["low", "check"]}]
    out = tmp_path / "table.csv"
    scoring.full_table_csv(rows, out)
    raw = out.read_bytes()
    assert raw.startswith(b"cheque_id,file,field,ground_truth,predicted,correct,confidence,review_status,issues\r\n")
    parsed = list(csvmod.reader(raw.decode("utf-8").splitlines()))
    assert parsed[1] == ["c1", "a.png", "payee", "A, B", 'say "x"', "incorrect", "0.5", "REVIEW", "low; check"]
    assert [p.name for p in tmp_path.iterdir()] == ["table.csv"] and (out.stat().st_mode & 0o077) == 0


# ------------------------------------------------------------------ SIGTERM
def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _server_env(tmp_path):
    env = dict(os.environ)
    env.update(CV_INSTANCE_DIR=str(tmp_path / "instance"), CV_MODELS_DIR=str(tmp_path / "models"),
               CV_REPORTS_DIR=str(tmp_path / "reports"), CV_ADMIN_PASSWORD="Stop-test-12345", CV_RATE_LIMIT_PER_MIN="0",
               PYTHONDONTWRITEBYTECODE="1")
    env.pop("CV_SECRET_KEY", None)
    return env


def _wait_healthy(port, proc, seconds=60):
    end = time.time() + seconds
    while time.time() < end:
        if proc.poll() is not None:
            raise AssertionError("the server exited while starting: " + (proc.stdout.read().decode(errors="replace") if proc.stdout else ""))
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1).read()
            return
        except OSError:
            time.sleep(0.3)
    raise AssertionError("the server did not become healthy")


def test_sigterm_stops_the_server_promptly_with_a_clean_exit(tmp_path):
    port = _free_port()
    proc = subprocess.Popen([sys.executable, "run.py", "--port", str(port)], cwd=BACKEND, env=_server_env(tmp_path),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        _wait_healthy(port, proc)
        t0 = time.time()
        proc.send_signal(signal.SIGTERM)
        code = proc.wait(timeout=10)
        assert code == 0 and time.time() - t0 < 5
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.stdout.close()


def _can_unshare_pid():
    if shutil.which("unshare") is None or not sys.platform.startswith("linux"):
        return False
    try:
        r = subprocess.run(["unshare", "--pid", "--fork", "--mount-proc", "true"], capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


@pytest.mark.skipif(not _can_unshare_pid(), reason="needs permission to create a PID namespace (root)")
def test_sigterm_also_works_when_the_server_is_pid_1_like_in_a_container(tmp_path):
    """In a container the server is PID 1 and the kernel ignores a SIGTERM that has no handler - the old behaviour was
    to keep running until the platform's grace period ended and then be killed."""
    port = _free_port()
    outer = subprocess.Popen(["unshare", "--pid", "--fork", "--kill-child", "--mount-proc", sys.executable, "run.py",
                              "--port", str(port)], cwd=BACKEND, env=_server_env(tmp_path),
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        _wait_healthy(port, outer)
        inner = int(subprocess.run(["pgrep", "-P", str(outer.pid)], capture_output=True, text=True).stdout.split()[0])
        nspid = [line for line in Path(f"/proc/{inner}/status").read_text().splitlines() if line.startswith("NSpid")][0].split()
        assert nspid[-1] == "1"                                       # really PID 1 of its namespace
        t0 = time.time()
        os.kill(inner, signal.SIGTERM)
        code = outer.wait(timeout=10)
        assert code == 0 and time.time() - t0 < 5
    finally:
        if outer.poll() is None:
            outer.kill()
        outer.stdout.close()


def test_a_failing_stdout_cannot_prevent_the_stop_or_the_watchdog(tmp_path):
    """Found by repeating the test below under CPU load: SIGTERM that lands while the main thread is inside print() made the
    handler's own print raise 'reentrant call' before the watchdog started, and the process then waited for its stuck
    thread forever. Whatever stdout does, the process must still leave within the grace period."""
    script = textwrap.dedent(f"""
        import os, sys, threading, time
        sys.path.insert(0, {str(BACKEND)!r})
        from run import install_stop_handler
        class Broken:
            def write(self, *_): raise RuntimeError("reentrant call")
            def flush(self): raise BrokenPipeError("log reader is gone")
        install_stop_handler(grace_seconds=1)
        threading.Thread(target=lambda: time.sleep(120)).start()     # a non-daemon thread that never ends
        sys.stdout = Broken()
        os.write(1, b"ready\\n")
        while True:
            time.sleep(0.1)
    """)
    proc = subprocess.Popen([sys.executable, "-I", "-c", script], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        assert proc.stdout.readline().strip() == b"ready"
        t0 = time.time()
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=15) == 0
        assert time.time() - t0 < 10
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_the_watchdog_ends_a_shutdown_that_would_otherwise_hang(tmp_path):
    """A request that never finishes must not turn a clean stop into a kill: after the grace period the process leaves."""
    script = textwrap.dedent(f"""
        import os, sys, threading, time
        sys.path.insert(0, {str(BACKEND)!r})
        from run import install_stop_handler
        install_stop_handler(grace_seconds=1)
        threading.Thread(target=lambda: time.sleep(120)).start()     # a non-daemon thread that never ends
        print("ready", flush=True)
        while True:
            time.sleep(0.1)
    """)
    proc = subprocess.Popen([sys.executable, "-I", "-c", script], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        assert proc.stdout.readline().strip() == b"ready"
        t0 = time.time()
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=15) == 0
        assert 0.8 < time.time() - t0 < 8                            # not instantly (the watchdog waited), not never
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.stdout.close()


def test_waitress_leaves_forwarded_headers_to_the_application(monkeypatch):
    """Found by the container check on GitHub: Waitress 3 clears X-Forwarded-* unless told otherwise, which behind one
    proxy (Render) made every client look like the proxy. Waitress is not installable in every environment, so this checks
    the call; the container step 'Behind one reverse proxy' checks the behaviour."""
    import types
    import run
    seen = {}
    fake = types.ModuleType("waitress")
    fake.serve = lambda app, **kw: seen.update(kw)
    monkeypatch.setitem(sys.modules, "waitress", fake)
    monkeypatch.setattr(sys, "argv", ["run.py", "--prod", "--port", "0"])
    monkeypatch.setattr("app.create_app", lambda *a, **k: object())
    monkeypatch.setattr("app.services.lifecycle.on_process_start", lambda app: None)
    monkeypatch.setattr(run, "install_stop_handler", lambda *a, **k: None)
    old_umask = os.umask(0o022)                 # main() sets a private umask; keep it from leaking into other tests
    try:
        run.main()
    finally:
        os.umask(old_umask)
    assert seen.get("clear_untrusted_proxy_headers") is False
    assert "trusted_proxy" not in seen          # whom to trust stays a single setting: CV_PROXY_HOPS
