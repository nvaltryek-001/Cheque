"""Container entrypoint.

Runs as root only long enough to prepare the persistent volume, then drops to the unprivileged
`app` user and starts the production server.

* /data is the only persistent path (Render disk / Docker volume). Hosted disks are usually
  mounted root-owned, which the non-root app user could not write to.
* Bundled trained models and the audit/evaluation reports are copied into /data on first start,
  so retraining from the UI and newly generated reports survive redeploys.
* umask 077: database, uploads and specimens are readable by the app user only.
"""
from __future__ import annotations

import os
import pwd
import shutil
import sys
from pathlib import Path

DATA = Path(os.environ.get("CV_DATA_ROOT", "/data"))
APP = Path(__file__).resolve().parent
USER = os.environ.get("CV_RUN_AS", "app")


def seed(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    if not src.is_dir():
        return
    for f in src.iterdir():
        if f.is_file() and not (dst / f.name).exists():
            shutil.copy2(f, dst / f.name)


def main() -> None:
    os.umask(0o077)
    models = Path(os.environ.setdefault("CV_MODELS_DIR", str(DATA / "models")))
    reports = Path(os.environ.setdefault("CV_REPORTS_DIR", str(DATA / "reports")))
    os.environ.setdefault("CV_INSTANCE_DIR", str(DATA / "instance"))
    Path(os.environ["CV_INSTANCE_DIR"]).mkdir(parents=True, exist_ok=True)
    seed(APP / "models_store", models)
    seed(APP.parent / "reports" / "final", reports / "final")
    if os.geteuid() == 0:
        pw = pwd.getpwnam(USER)
        for root, dirs, files in os.walk(DATA):
            for name in [root, *[os.path.join(root, d) for d in dirs], *[os.path.join(root, f) for f in files]]:
                st = os.lstat(name)
                if st.st_uid != pw.pw_uid:
                    os.lchown(name, pw.pw_uid, pw.pw_gid)
        os.setgroups([])
        os.setgid(pw.pw_gid)
        os.setuid(pw.pw_uid)
        os.environ["HOME"] = pw.pw_dir
    port = os.environ.get("PORT", "8000")
    os.chdir(APP)
    os.execv(sys.executable, [sys.executable, "run.py", "--prod", "--host", "0.0.0.0", "--port", port])


if __name__ == "__main__":
    main()
