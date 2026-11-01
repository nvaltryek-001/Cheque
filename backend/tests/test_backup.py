"""Backup, verification and restore (app/services/backup.py, `manage.py backup | verify-backup | restore-backup`)."""
import io
import json
import os
import sqlite3
import stat
import subprocess
import sys
import tarfile
import threading
from pathlib import Path

import pytest

from app.db import connect
from app.services import backup, storage

BACKEND = Path(__file__).resolve().parent.parent
SECRET = "ONLY-IN-THE-SECRET-KEY-FILE-0123456789abcdef0123456789abcdef"


@pytest.fixture()
def populated(app, client, admin, tmp_path_factory):
    """An application with a database, stored images, models, reports, a ledger and a secret key on disk."""
    r = client.post("/api/users", headers=admin, json={"username": "bk_operator", "password": "Backup-pass-123", "role": "operator"})
    assert r.status_code in (200, 201), r.get_json()
    with app.app_context():
        png = b"\x89PNG\r\n\x1a\n" + os.urandom(3000)
        names = {"cheques": storage.save_bytes("cheques", png, "png"),
                 "specimens": storage.save_bytes("specimens", png + b"s", "png")}
    models = Path(app.config["MODELS_DIR"])
    models.mkdir(parents=True, exist_ok=True)
    (models / "micr_e13b.npz").write_bytes(b"model bytes " * 50)
    reports = Path(app.config["REPORTS_DIR"]) / "final"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "MODEL_EVALUATION.json").write_text('{"note": "a report"}')
    instance = Path(app.config["INSTANCE_DIR"])
    (instance / "signature_eval_ledger.json").write_text('[{"use": 1}]')
    (instance / "secret_key").write_text(SECRET)
    out = tmp_path_factory.mktemp("backups")                       # outside every data folder
    return type("P", (), {"app": app, "cfg": app.config, "out": out, "names": names, "png": png, "client": client})


def _members(path):
    with tarfile.open(path, "r:gz") as tar:
        return {m.name: tar.extractfile(m).read() for m in tar if m.isreg()}


def _repack(src, dst, *, change=None, drop=(), add=()):
    """Copy an archive, optionally altering a member's content, dropping members or adding raw TarInfo entries."""
    with tarfile.open(src, "r:gz") as r, tarfile.open(dst, "w:gz") as w:
        for m in r:
            if m.name in drop:
                continue
            data = r.extractfile(m).read()
            if change and m.name in change:
                data = change[m.name]
                m.size = len(data)
            w.addfile(m, io.BytesIO(data))
        for info, data in add:
            w.addfile(info, io.BytesIO(data) if data is not None else None)


def test_a_backup_holds_everything_that_matters_and_not_the_secret_key(populated):
    res = backup.create_backup(populated.cfg, populated.out)
    path = Path(res["path"])
    assert path.parent == populated.out and path.name.startswith("chequeverify-backup-") and path.suffix == ".gz"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    members = _members(path)
    assert "instance/test.db" in members and "instance/signature_eval_ledger.json" in members
    assert f"storage/cheques/{populated.names['cheques']}" in members and f"storage/specimens/{populated.names['specimens']}" in members
    assert "models/micr_e13b.npz" in members and "reports/final/MODEL_EVALUATION.json" in members
    assert not any("secret_key" in n for n in members)
    assert not any(SECRET.encode() in data for data in members.values())               # not hidden inside another file either
    assert not any(n.endswith(("-wal", "-shm")) for n in members)
    manifest = json.loads(members["MANIFEST.json"])
    assert manifest["format"] == backup.FORMAT and manifest["database"]["integrity_check"] == "ok"
    assert manifest["database"]["tables"]["users"] >= 2 and "secret_key" in manifest["not_included"][0]
    assert backup.verify_backup(path)["ok"] is True
    assert [p.name for p in populated.out.iterdir()] == [path.name]                    # no scratch or temporary leftovers


def test_restore_gives_back_exactly_what_was_saved_in_the_standard_layout(populated, tmp_path_factory):
    res = backup.create_backup(populated.cfg, populated.out)
    target = tmp_path_factory.mktemp("restore") / "data"
    done = backup.restore_backup(res["path"], target)
    assert done["tables"]["users"] >= 2
    assert (target / "instance" / "test.db").is_file()
    assert (target / "instance" / "storage" / "cheques" / populated.names["cheques"]).read_bytes() == populated.png
    assert (target / "instance" / "storage" / "specimens" / populated.names["specimens"]).read_bytes() == populated.png + b"s"
    assert (target / "models" / "micr_e13b.npz").read_bytes() == b"model bytes " * 50
    assert (target / "reports" / "final" / "MODEL_EVALUATION.json").read_text() == '{"note": "a report"}'
    assert (target / "instance" / "signature_eval_ledger.json").read_text() == '[{"use": 1}]'
    assert not (target / "instance" / "secret_key").exists()
    for p in target.rglob("*"):
        assert stat.S_IMODE(p.stat().st_mode) in (0o600, 0o700), p
    original = sqlite3.connect(populated.cfg["DATABASE"])
    restored = sqlite3.connect(str(target / "instance" / "test.db"))
    try:
        query = "SELECT username, role, password_hash FROM users ORDER BY username"
        assert restored.execute(query).fetchall() == original.execute(query).fetchall()
        assert restored.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    finally:
        original.close()
        restored.close()


def test_restore_never_overwrites_and_never_deletes(populated, tmp_path_factory):
    res = backup.create_backup(populated.cfg, populated.out)
    target = tmp_path_factory.mktemp("busy")
    (target / "existing.txt").write_text("keep me")
    with pytest.raises(backup.BackupError, match="not an empty folder"):
        backup.restore_backup(res["path"], target)
    assert [p.name for p in target.iterdir()] == ["existing.txt"] and (target / "existing.txt").read_text() == "keep me"
    a_file = target / "existing.txt"
    with pytest.raises(backup.BackupError):
        backup.restore_backup(res["path"], a_file)
    assert a_file.read_text() == "keep me"


def test_a_backup_taken_while_the_server_is_writing_is_consistent_and_includes_recent_commits(populated, tmp_path_factory):
    db = populated.cfg["DATABASE"]
    setup = connect(str(db))
    setup.execute("CREATE TABLE IF NOT EXISTS pair_a(n INTEGER)")
    setup.execute("CREATE TABLE IF NOT EXISTS pair_b(n INTEGER)")
    setup.commit()
    setup.close()
    # a commit that is still only in the write-ahead log (not yet in the main file): a plain file copy would miss it
    holder = connect(str(db))
    holder.execute("PRAGMA wal_autocheckpoint=0")
    holder.execute("INSERT INTO pair_a(n) VALUES (-1)")
    holder.execute("INSERT INTO pair_b(n) VALUES (-1)")
    holder.commit()
    stop = threading.Event()
    errors = []

    def writer():
        conn = connect(str(db))
        n = 0
        try:
            while not stop.is_set():
                n += 1
                try:
                    conn.execute("INSERT INTO pair_a(n) VALUES (?)", (n,))
                    conn.execute("INSERT INTO pair_b(n) VALUES (?)", (n,))      # both or neither
                    conn.commit()
                except sqlite3.OperationalError as exc:                        # a busy moment is allowed to fail; torn pairs are not
                    conn.rollback()
                    errors.append(str(exc))
        finally:
            conn.close()

    t = threading.Thread(target=writer)
    t.start()
    try:
        res = backup.create_backup(populated.cfg, populated.out)
    finally:
        stop.set()
        t.join(10)
        holder.close()
    target = tmp_path_factory.mktemp("restore") / "d"
    backup.restore_backup(res["path"], target)
    conn = sqlite3.connect(str(target / "instance" / "test.db"))
    try:
        assert conn.execute("SELECT COUNT(*) FROM pair_a WHERE n=-1").fetchone()[0] == 1       # the WAL-only commit is in the backup
        a = conn.execute("SELECT COUNT(*) FROM pair_a").fetchone()[0]
        b = conn.execute("SELECT COUNT(*) FROM pair_b").fetchone()[0]
        assert a == b >= 1                                                                      # one point in time, no torn pair
        assert conn.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    finally:
        conn.close()


def test_skip_images_leaves_out_exactly_the_images(populated):
    res = backup.create_backup(populated.cfg, populated.out, include_images=False)
    members = _members(res["path"])
    assert not any(n.startswith("storage/") for n in members)
    assert "instance/test.db" in members and "models/micr_e13b.npz" in members
    assert json.loads(members["MANIFEST.json"])["include_images"] is False


# ------------------------------------------------------------------ verification catches damage
def test_verification_catches_every_kind_of_damage(populated, tmp_path):
    good = backup.create_backup(populated.cfg, populated.out)["path"]
    first_image = f"storage/cheques/{populated.names['cheques']}"

    changed = tmp_path / "changed.tar.gz"
    _repack(good, changed, change={first_image: populated.png[:-1] + b"X"})
    r = backup.verify_backup(changed)
    assert not r["ok"] and any("does not match" in p for p in r["problems"])

    shortened = tmp_path / "shortened.tar.gz"
    _repack(good, shortened, change={first_image: populated.png[:100]})
    assert not backup.verify_backup(shortened)["ok"]

    missing = tmp_path / "missing.tar.gz"
    _repack(good, missing, drop={first_image})
    r = backup.verify_backup(missing)
    assert not r["ok"] and any("missing from the archive" in p for p in r["problems"])

    extra = tmp_path / "extra.tar.gz"
    info = tarfile.TarInfo("models/extra.bin")
    info.size = 3
    _repack(good, extra, add=[(info, b"abc")])
    r = backup.verify_backup(extra)
    assert not r["ok"] and any("not in the manifest" in p for p in r["problems"])

    no_manifest = tmp_path / "nomanifest.tar.gz"
    _repack(good, no_manifest, drop={"MANIFEST.json"})
    assert not backup.verify_backup(no_manifest)["ok"]

    raw = Path(good).read_bytes()
    cut = tmp_path / "cut.tar.gz"
    cut.write_bytes(raw[: len(raw) // 2])
    with pytest.raises(backup.BackupError):
        backup.verify_backup(cut)
    garbage = tmp_path / "garbage.tar.gz"
    garbage.write_bytes(b"this is not an archive at all")
    with pytest.raises(backup.BackupError):
        backup.verify_backup(garbage)
    with pytest.raises(backup.BackupError):
        backup.verify_backup(tmp_path / "nothing-here.tar.gz")


@pytest.mark.parametrize("name,kind", [
    ("../escape.txt", "file"), ("/etc/cron.d/evil", "file"), ("instance/../../escape.txt", "file"),
    ("models/.hidden", "file"), ("elsewhere/file.txt", "file"), ("models/link", "symlink"), ("models/dir", "dir"),
])
def test_dangerous_entries_are_refused_and_nothing_is_extracted(populated, tmp_path, tmp_path_factory, name, kind):
    good = backup.create_backup(populated.cfg, populated.out)["path"]
    bad = tmp_path / "bad.tar.gz"
    info = tarfile.TarInfo(name)
    if kind == "symlink":
        info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
        payload = None
    elif kind == "dir":
        info.type = tarfile.DIRTYPE
        payload = None
    else:
        info.size = 4
        payload = b"evil"
    _repack(good, bad, add=[(info, payload)])
    r = backup.verify_backup(bad)
    assert not r["ok"] and any("unexpected entry" in p or "not in the manifest" in p for p in r["problems"])
    target = tmp_path_factory.mktemp("never") / "data"
    with pytest.raises(backup.BackupError, match="failed verification"):
        backup.restore_backup(bad, target)
    assert not target.exists()                                                  # nothing was created, let alone extracted
    assert not (tmp_path.parent / "escape.txt").exists()


def _forge(good, dst, *, replace=None, extra=None):
    """A self-consistent archive: contents changed or added AND the manifest rewritten to match, as an attacker (or a bug)
    that understands the format would produce. Only the name rules and the database check stand in the way."""
    replace, extra = replace or {}, extra or {}
    with tarfile.open(good, "r:gz") as r:
        data = {m.name: r.extractfile(m).read() for m in r if m.isreg()}
    manifest = json.loads(data.pop("MANIFEST.json"))
    data.update(replace)
    data.update(extra)
    import hashlib
    manifest["files"] = [{"path": n, "size": len(b), "sha256": hashlib.sha256(b).hexdigest()} for n, b in sorted(data.items())]
    with tarfile.open(dst, "w:gz") as w:
        for n, b in list(sorted(data.items())) + [("MANIFEST.json", json.dumps(manifest).encode())]:
            info = tarfile.TarInfo(n)
            info.size = len(b)
            w.addfile(info, io.BytesIO(b))


@pytest.mark.parametrize("name", ["instance/../../escape.txt", "models/.hidden", "storage/cheques/..", "models/a b",
                                  "models/\u00fcn\u00ef.bin", "storage//double.png", "/etc/cron.d/x", "models/x\\y"])
def test_a_forged_archive_with_a_matching_manifest_is_still_refused(populated, tmp_path, tmp_path_factory, name):
    good = backup.create_backup(populated.cfg, populated.out)["path"]
    forged = tmp_path / "forged.tar.gz"
    _forge(good, forged, extra={name: b"evil"})
    r = backup.verify_backup(forged)
    assert not r["ok"] and any("unexpected entry" in p for p in r["problems"])
    target = tmp_path_factory.mktemp("forged") / "data"
    with pytest.raises(backup.BackupError, match="failed verification"):
        backup.restore_backup(forged, target)
    assert not target.exists() and not (tmp_path.parent / "escape.txt").exists()


def test_a_database_that_is_damaged_or_differs_from_its_record_is_refused_after_extraction(populated, tmp_path, tmp_path_factory):
    good = backup.create_backup(populated.cfg, populated.out)["path"]
    garbage = tmp_path / "garbage-db.tar.gz"
    _forge(good, garbage, replace={"instance/test.db": b"SQLite format 3\x00" + os.urandom(5000)})
    assert backup.verify_backup(garbage)["ok"]                                   # every hash matches: only opening the file shows it
    with pytest.raises(backup.BackupError, match="cannot be opened|does not match"):
        backup.restore_backup(garbage, tmp_path_factory.mktemp("g") / "d")

    members = _members(good)
    scratch = tmp_path / "fewer-rows.db"
    scratch.write_bytes(members["instance/test.db"])
    conn = sqlite3.connect(str(scratch))
    conn.execute("DELETE FROM audit_log")
    conn.execute("DELETE FROM users WHERE username='bk_operator'")
    conn.commit()
    conn.close()
    fewer = tmp_path / "fewer.tar.gz"
    _forge(good, fewer, replace={"instance/test.db": scratch.read_bytes()})
    assert backup.verify_backup(fewer)["ok"]
    with pytest.raises(backup.BackupError, match="does not match the backup's own record"):
        backup.restore_backup(fewer, tmp_path_factory.mktemp("f") / "d")


def test_a_backup_cannot_be_written_inside_the_data_it_backs_up(populated):
    for inside in (Path(populated.cfg["INSTANCE_DIR"]) / "backups", Path(populated.cfg["MODELS_DIR"]), Path(populated.cfg["STORAGE_DIR"]) / "cheques"):
        with pytest.raises(backup.BackupError, match="inside"):
            backup.create_backup(populated.cfg, inside)
    assert not (Path(populated.cfg["INSTANCE_DIR"]) / "backups").exists()


def test_without_a_database_there_is_nothing_to_back_up(populated, tmp_path_factory):
    cfg = dict(populated.cfg)
    cfg["DATABASE"] = Path(populated.cfg["INSTANCE_DIR"]) / "does-not-exist.db"
    out = tmp_path_factory.mktemp("o")
    with pytest.raises(backup.BackupError, match="no database"):
        backup.create_backup(cfg, out)
    assert list(out.iterdir()) == []                                            # no partial file, no scratch folder


def test_a_failure_part_way_leaves_no_partial_backup(populated, monkeypatch):
    real = backup._add_file

    def explode(tar, name, path, entries):
        if name.startswith("models/"):
            raise OSError("disk full")
        return real(tar, name, path, entries)

    monkeypatch.setattr(backup, "_add_file", explode)
    with pytest.raises(OSError):
        backup.create_backup(populated.cfg, populated.out)
    assert list(populated.out.iterdir()) == []


# ------------------------------------------------------------------ the command line
def _cli(populated, *args):
    env = dict(os.environ)
    env.update(CV_INSTANCE_DIR=str(populated.cfg["INSTANCE_DIR"]), CV_DATABASE=str(populated.cfg["DATABASE"]),
               CV_STORAGE_DIR=str(populated.cfg["STORAGE_DIR"]), CV_MODELS_DIR=str(populated.cfg["MODELS_DIR"]),
               CV_REPORTS_DIR=str(populated.cfg["REPORTS_DIR"]), CV_SECRET_KEY=SECRET)
    return subprocess.run([sys.executable, "manage.py", *args], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120)


def test_command_line_backup_verify_restore(populated, tmp_path_factory):
    done = _cli(populated, "backup", "--out", str(populated.out))
    assert done.returncode == 0, done.stderr
    assert "secret key is not included" in done.stdout and SECRET not in done.stdout + done.stderr
    archive = next(populated.out.glob("chequeverify-backup-*.tar.gz"))
    checked = _cli(populated, "verify-backup", str(archive))
    assert checked.returncode == 0 and checked.stdout.startswith("OK:")
    target = tmp_path_factory.mktemp("cli") / "data"
    restored = _cli(populated, "restore-backup", str(archive), "--into", str(target))
    assert restored.returncode == 0, restored.stderr
    assert "integrity_check ok" in restored.stdout and (target / "instance" / "test.db").is_file()
    again = _cli(populated, "restore-backup", str(archive), "--into", str(target))      # a second restore into the same folder
    assert again.returncode != 0 and "not an empty folder" in again.stderr

    damaged = populated.out / "damaged.tar.gz"
    _repack(archive, damaged, change={"models/micr_e13b.npz": b"tampered"})
    bad = _cli(populated, "verify-backup", str(damaged))
    assert bad.returncode != 0 and "PROBLEM" in bad.stdout and "do not rely" in bad.stderr
