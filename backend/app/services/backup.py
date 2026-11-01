"""Backup and restore of everything the application keeps on its persistent disk.

What a backup holds
    instance/   the database - a consistent snapshot taken with SQLite's online backup, safe while the server is running -
                and the other files kept next to it (for example the signature test-use ledger)
    storage/    cheque images, crops, signature specimens and their processed copies, query images
    models/     the trained MICR templates and signature model
    reports/    generated evaluation and audit reports

What it deliberately does not hold
    * the secret key. It signs session tokens: a backup that leaks must not hand out the means to forge sign-ins for the
      live server. After a restore everybody signs in again.
    * environment settings and passwords (CV_ADMIN_PASSWORD, the SMTP password): they live in the hosting dashboard.

A backup is personal data (user addresses, cheque images, signature specimens). This tool does not encrypt it: keep it on
an encrypted volume, or encrypt the file before it leaves the server. It is one `.tar.gz` file with a MANIFEST.json that
lists every file with its size and SHA-256, so `verify_backup` can prove that nothing was cut short or altered, and
`restore_backup` refuses to extract anything that fails that check.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import tarfile
import tempfile
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from . import atomic

FORMAT = "chequeverify-backup/1"
MANIFEST = "MANIFEST.json"
AREAS = ("instance", "storage", "models", "reports")
EXCLUDED_NOTE = "instance/secret_key is never backed up (see app/services/backup.py)"
_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")          # one path component: no dots-only names, no hidden files, no spaces
_MAX_MANIFEST_BYTES = 20_000_000


class BackupError(Exception):
    """A backup or restore that cannot proceed; the message is meant for the operator."""


# ------------------------------------------------------------------ names
def safe_member_name(name: str) -> bool:
    """A name that may appear in a backup: <area>/<one or more plain components>. Nothing absolute, nothing with `..`."""
    parts = name.split("/")
    return len(parts) >= 2 and parts[0] in AREAS and all(_PART.match(p) for p in parts[1:])


def restore_path(name: str) -> str:
    """Where a member lands in the standard data layout used by the Docker image and the Render blueprint:
    instance/ and models/ and reports/ as they are, and storage/ inside instance/ (the default STORAGE_DIR)."""
    area, rest = name.split("/", 1)
    return f"instance/storage/{rest}" if area == "storage" else name


# ------------------------------------------------------------------ creating
def _walk(root: Path, recursive: bool) -> Iterator[Path]:
    if not root.is_dir():
        return
    for entry in sorted(root.iterdir()):
        if entry.is_symlink() or entry.name.startswith("."):    # links are never followed; hidden files are temp files
            continue
        if entry.is_dir():
            if recursive:
                yield from _walk(entry, True)
        elif entry.is_file():
            yield entry


class _Hashing:
    """File wrapper that hashes what tarfile reads, so each file is read once."""

    def __init__(self, fh):
        self.fh, self.digest, self.size = fh, hashlib.sha256(), 0

    def read(self, n: int = -1) -> bytes:
        block = self.fh.read(n)
        self.digest.update(block)
        self.size += len(block)
        return block


def _add_file(tar: tarfile.TarFile, name: str, path: Path, entries: list[dict]) -> None:
    try:
        fh = path.open("rb")
    except FileNotFoundError:                                    # removed while the backup ran (for example by retention)
        return
    with fh:
        st = os.fstat(fh.fileno())
        info = tarfile.TarInfo(name)
        info.size, info.mtime, info.mode = st.st_size, int(st.st_mtime), 0o600
        wrapped = _Hashing(fh)
        tar.addfile(info, wrapped)
    entries.append({"path": name, "size": wrapped.size, "sha256": wrapped.digest.hexdigest()})


def _snapshot_database(db: Path, dest: Path) -> dict:
    """Copy the live database with SQLite's online backup API: one consistent state, writers are not blocked."""
    if not db.is_file():
        raise BackupError(f"there is no database at {db}")
    from ..db import connect
    source = connect(str(db))
    try:
        target = sqlite3.connect(str(dest))
        try:
            source.backup(target)
            target.execute("PRAGMA journal_mode=DELETE")         # a single self-contained file
            verdict = [row[0] for row in target.execute("PRAGMA integrity_check")]
            names = [r[0] for r in target.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
            tables = {n: target.execute('SELECT COUNT(*) FROM "%s"' % n.replace('"', '""')).fetchone()[0] for n in names}
        finally:
            target.close()
    finally:
        source.close()
    if verdict != ["ok"]:
        raise BackupError("the database snapshot failed SQLite's integrity check: " + "; ".join(map(str, verdict[:3])))
    return {"integrity_check": "ok", "tables": tables}


def _private_dirs(path: Path) -> None:
    """Create `path` and any missing parents with mode 700, whatever the caller's umask (mkdir's mode= only covers the
    last component)."""
    missing = []
    probe = path
    while not probe.exists():
        missing.append(probe)
        probe = probe.parent
    for folder in reversed(missing):
        folder.mkdir(mode=0o700, exist_ok=True)
        os.chmod(folder, 0o700)


def _inside(child: Path, parent: Path) -> bool:
    child, parent = child.resolve(), parent.resolve()
    return child == parent or parent in child.parents


def create_backup(cfg, out_dir: str | Path, *, include_images: bool = True) -> dict:
    """Write one backup file into `out_dir`. `cfg` has INSTANCE_DIR, DATABASE, STORAGE_DIR, MODELS_DIR, REPORTS_DIR."""
    instance, storage = Path(cfg["INSTANCE_DIR"]), Path(cfg["STORAGE_DIR"])
    models, reports, db = Path(cfg["MODELS_DIR"]), Path(cfg["REPORTS_DIR"]), Path(cfg["DATABASE"])
    out_dir = Path(out_dir)
    for folder in (instance, storage, models, reports):
        if folder.exists() and _inside(out_dir, folder):
            raise BackupError(f"--out {out_dir} is inside {folder}, which is part of what is being backed up")
    _private_dirs(out_dir)
    created = datetime.now(timezone.utc)
    final = out_dir / f"chequeverify-backup-{created.strftime('%Y%m%dT%H%M%SZ')}.tar.gz"
    if final.exists():
        raise BackupError(f"{final} already exists; wait a second and run it again")
    fd, tmp_name = tempfile.mkstemp(prefix=atomic.TMP_PREFIX, suffix=".tar.gz", dir=str(out_dir))
    tmp = Path(tmp_name)
    entries: list[dict] = []
    database: dict = {}
    try:
        with os.fdopen(fd, "wb") as raw:
            with tarfile.open(fileobj=raw, mode="w:gz", compresslevel=6) as tar:
                with tempfile.TemporaryDirectory(prefix="cv-backup-", dir=str(out_dir)) as scratch:
                    snapshot = Path(scratch) / "db"
                    database = _snapshot_database(db, snapshot)
                    _add_file(tar, f"instance/{db.name}", snapshot, entries)
                for path in _walk(instance, recursive=False):
                    if path.name in ("secret_key", db.name) or path.name.startswith(db.name + "-"):
                        continue                                  # the key stays out; the database, its WAL/SHM and journal are the snapshot
                    _add_file(tar, f"instance/{path.name}", path, entries)
                for area, root, wanted in (("storage", storage, include_images), ("models", models, True), ("reports", reports, True)):
                    if wanted:
                        for path in _walk(root, recursive=True):
                            _add_file(tar, f"{area}/{path.relative_to(root).as_posix()}", path, entries)
                bad = [e["path"] for e in entries if not safe_member_name(e["path"])]
                if bad:
                    raise BackupError("these files have names a backup cannot hold safely: " + ", ".join(map(repr, bad[:5])))
                manifest = {"format": FORMAT, "created_at": created.isoformat(timespec="seconds"),
                            "include_images": include_images, "database": database, "files": entries,
                            "not_included": [EXCLUDED_NOTE]}
                blob = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
                info = tarfile.TarInfo(MANIFEST)
                info.size, info.mtime, info.mode = len(blob), int(created.timestamp()), 0o600
                tar.addfile(info, io.BytesIO(blob))
            raw.flush()
            os.fsync(raw.fileno())
        os.replace(tmp, final)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return {"path": str(final), "files": len(entries), "bytes": sum(e["size"] for e in entries),
            "archive_bytes": final.stat().st_size, "tables": database.get("tables", {}), "include_images": include_images}


# ------------------------------------------------------------------ checking
def verify_backup(path: str | Path) -> dict:
    """Read the whole archive and compare every file with the manifest. Never extracts anything."""
    problems: list[str] = []
    seen: dict[str, tuple[int, str]] = {}
    manifest = None
    try:
        with tarfile.open(path, "r:gz") as tar:
            for member in tar:
                if member.name == MANIFEST:
                    if not member.isreg() or member.size > _MAX_MANIFEST_BYTES:
                        problems.append("the manifest is not a normal file or is implausibly large")
                        continue
                    try:
                        manifest = json.loads(tar.extractfile(member).read().decode("utf-8"))
                    except (ValueError, UnicodeDecodeError):
                        problems.append("the manifest cannot be read")
                    continue
                if not member.isreg() or not safe_member_name(member.name):
                    problems.append(f"unexpected entry {member.name!r} (not a plain file in a known folder)")
                    continue
                if member.name in seen:
                    problems.append(f"{member.name!r} appears twice")
                    continue
                digest, size = hashlib.sha256(), 0
                fh = tar.extractfile(member)
                for block in iter(lambda: fh.read(1 << 20), b""):
                    digest.update(block)
                    size += len(block)
                seen[member.name] = (size, digest.hexdigest())
    except (tarfile.TarError, OSError, EOFError, zlib.error, ValueError) as exc:
        raise BackupError(f"cannot read {path}: {type(exc).__name__}: {exc}") from exc
    files = []
    if manifest is None:
        problems.append("the backup has no manifest")
    elif not isinstance(manifest, dict) or manifest.get("format") != FORMAT or not isinstance(manifest.get("files"), list):
        problems.append("the manifest is not in the format this version understands")
    else:
        files = manifest["files"]
        listed = {}
        for entry in files:
            try:
                listed[entry["path"]] = (int(entry["size"]), str(entry["sha256"]))
            except (KeyError, TypeError, ValueError):
                problems.append("the manifest has a malformed entry")
        for name, expected in listed.items():
            if name not in seen:
                problems.append(f"{name} is listed but missing from the archive")
            elif seen[name] != expected:
                problems.append(f"{name} does not match the manifest (altered or cut short)")
        for name in seen:
            if name not in listed:
                problems.append(f"{name} is in the archive but not in the manifest")
        if not any(n.startswith("instance/") and n.endswith(".db") for n in listed):
            problems.append("the backup holds no database")
    return {"ok": not problems, "problems": problems, "files": len(seen), "bytes": sum(s for s, _ in seen.values()),
            "manifest": manifest if isinstance(manifest, dict) else None}


# ------------------------------------------------------------------ restoring
def restore_backup(path: str | Path, into: str | Path) -> dict:
    """Extract a verified backup into an EMPTY folder, in the layout the Docker image and the Render blueprint use
    (`instance/` with `storage/` inside it, `models/`, `reports/`). Nothing is ever overwritten or deleted."""
    report = verify_backup(path)
    if not report["ok"]:
        raise BackupError("the backup failed verification and was not restored: " + "; ".join(report["problems"][:5]))
    into = Path(into)
    if into.exists() and (not into.is_dir() or any(into.iterdir())):
        raise BackupError(f"{into} is not an empty folder; restore into a new, empty one so that nothing is overwritten")
    _private_dirs(into)
    manifest = report["manifest"]
    try:
        with tarfile.open(path, "r:gz") as tar:
            for member in tar:
                if member.name == MANIFEST:
                    continue
                dest = into / restore_path(member.name)            # the name was validated by verify_backup
                _private_dirs(dest.parent)
                with tar.extractfile(member) as src, open(dest, "xb") as out:
                    shutil.copyfileobj(src, out)
                os.chmod(dest, 0o600)
    except (OSError, tarfile.TarError) as exc:
        raise BackupError(f"restore stopped part way: {exc}. The folder {into} is incomplete; discard it.") from exc
    db_files = sorted((into / "instance").glob("*.db"))
    if len(db_files) != 1:
        raise BackupError("the restored data does not hold exactly one database")
    try:
        conn = sqlite3.connect(str(db_files[0]))
        try:
            verdict = [r[0] for r in conn.execute("PRAGMA integrity_check")]
            counts = {n: conn.execute('SELECT COUNT(*) FROM "%s"' % n.replace('"', '""')).fetchone()[0]
                      for n in manifest["database"]["tables"]}
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise BackupError(f"the restored database cannot be opened ({type(exc).__name__}); discard the restored folder") from exc
    if verdict != ["ok"] or counts != manifest["database"]["tables"]:
        raise BackupError("the restored database does not match the backup's own record of it; discard the restored folder")
    return {"into": str(into), "files": report["files"], "bytes": report["bytes"], "tables": counts,
            "created_at": manifest.get("created_at")}
