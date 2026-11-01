"""Container entrypoint.

Starts as root only long enough to make the persistent volume usable, then drops to the unprivileged `app` user and
starts the production server. Everything else - creating folders, copying the shipped models and reports - is done
after the privileges are dropped, so a symbolic link planted in the volume by a compromised server process cannot
make root write somewhere else.

* /data is the only persistent path (Render disk / Docker volume). Hosted disks are usually mounted root-owned, which
  the non-root app user could not write to, hence the one thing root does: hand /data to the app user.
* The trained models and the evaluation reports that ship with the image are copied into /data (see `seed`), so models
  retrained from the UI and reports generated later survive redeploys, and a new release can still bring improved
  models to a disk that has only ever held the shipped ones.
* umask 077: database, uploads and specimens are readable by the app user only.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import pwd
import shutil
import sys
from pathlib import Path

DATA = Path(os.environ.get("CV_DATA_ROOT", "/data"))
APP = Path(__file__).resolve().parent
USER = os.environ.get("CV_RUN_AS", "app")
MANIFEST_NAME = ".seed_manifest.json"


def _atomic():
    """backend/app/services/atomic.py needs nothing but the standard library. Load it by path, so that starting up does
    not import the whole application (Flask, OpenCV, ...) as root."""
    spec = importlib.util.spec_from_file_location("cv_atomic", Path(__file__).resolve().parent / "app" / "services" / "atomic.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def read_manifest(path: Path) -> dict[str, str]:
    """sha-256 of every file this entrypoint copied into the volume, as it was when copied.

    Missing or damaged means nothing is known about earlier copies, and then nothing that differs from the shipped file
    is ever overwritten."""
    try:
        files = json.loads(path.read_text(encoding="utf-8"))["files"]
        return {str(k): str(v) for k, v in files.items()}
    except (OSError, ValueError, KeyError, AttributeError, TypeError):
        return {}


def _copy(src: Path, dst: Path, atomic) -> None:
    with src.open("rb") as r, atomic.open_for_write(dst) as w:      # temp file + rename: never a half-copied model
        shutil.copyfileobj(r, w)


def seed(src: Path, dst: Path, label: str, manifest: dict[str, str]) -> list[tuple[str, str]]:
    """Bring the files shipped in `src` into the persistent folder `dst`. Returns [(label/name, what happened)].

    * not there yet                              -> copied
    * identical to the shipped file              -> left alone
    * different, and still byte-for-byte the copy this entrypoint made earlier (so nobody has changed it)
                                                 -> updated to the shipped file of this release
    * different, changed since it was copied, or of unknown origin
                                                 -> kept: a model retrained on this disk is never overwritten
    `manifest` is updated in place; the caller saves it."""
    dst.mkdir(parents=True, exist_ok=True)
    actions: list[tuple[str, str]] = []
    if not src.is_dir():
        return actions
    atomic = _atomic()
    for shipped_file in sorted(src.iterdir()):
        if not shipped_file.is_file():
            continue
        key = f"{label}/{shipped_file.name}"
        target = dst / shipped_file.name
        shipped = _sha256(shipped_file)
        if not target.exists() and not target.is_symlink():
            _copy(shipped_file, target, atomic)
            manifest[key] = shipped
            actions.append((key, "copied"))
        elif target.is_symlink() or not target.is_file():
            actions.append((key, "kept: not a regular file"))
        else:
            current = _sha256(target)
            if current == shipped:
                manifest[key] = shipped
                actions.append((key, "current"))
            elif manifest.get(key) == current:
                _copy(shipped_file, target, atomic)
                manifest[key] = shipped
                actions.append((key, "updated"))
            elif key in manifest:
                actions.append((key, "kept: changed on this disk after it was copied (for example retrained)"))
            else:
                actions.append((key, "kept: differs from the shipped file and its origin is unknown"))
    return actions


def seed_all(models: Path, reports: Path, data_root: Path = DATA, app: Path = APP) -> list[tuple[str, str]]:
    manifest_path = data_root / MANIFEST_NAME
    manifest = read_manifest(manifest_path)
    before = dict(manifest)
    actions = seed(app / "models_store", models, "models", manifest)
    actions += seed(app.parent / "reports" / "final", reports / "final", "reports", manifest)
    if manifest != before:
        _atomic().write_json(manifest_path, {"version": 1, "files": manifest}, sort_keys=True)
    return actions


def report(actions: list[tuple[str, str]]) -> None:
    counts: dict[str, int] = {}
    for key, what in actions:
        kind = what.split(":", 1)[0]
        counts[kind] = counts.get(kind, 0) + 1
        if kind in ("updated", "kept"):
            print(f"seed: {key} {what}", flush=True)
    summary = ", ".join(f"{n} {kind}" for kind, n in sorted(counts.items())) or "nothing to copy"
    print(f"seed: {summary}", flush=True)


def drop_privileges() -> None:
    """Root only: hand the volume to the app user, then become that user."""
    pw = pwd.getpwnam(USER)
    DATA.mkdir(parents=True, exist_ok=True)
    for root, dirs, files in os.walk(DATA):                          # followlinks=False: links are never entered
        for name in [root, *[os.path.join(root, d) for d in dirs], *[os.path.join(root, f) for f in files]]:
            st = os.lstat(name)
            if st.st_uid != pw.pw_uid:
                os.lchown(name, pw.pw_uid, pw.pw_gid)                # lchown: the link itself, never its target
    os.setgroups([])
    os.setgid(pw.pw_gid)
    os.setuid(pw.pw_uid)
    os.environ["HOME"] = pw.pw_dir


def main() -> None:
    os.umask(0o077)
    models = Path(os.environ.setdefault("CV_MODELS_DIR", str(DATA / "models")))
    reports = Path(os.environ.setdefault("CV_REPORTS_DIR", str(DATA / "reports")))
    instance = Path(os.environ.setdefault("CV_INSTANCE_DIR", str(DATA / "instance")))
    if os.geteuid() == 0:
        drop_privileges()
    instance.mkdir(parents=True, exist_ok=True)
    report(seed_all(models, reports))
    port = os.environ.get("PORT", "8000")
    os.chdir(APP)
    os.execv(sys.executable, [sys.executable, "run.py", "--prod", "--host", "0.0.0.0", "--port", port])


if __name__ == "__main__":
    main()
