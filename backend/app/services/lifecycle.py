"""What a freshly started server process does once, before it serves requests.

* removes temporary files that a crashed write left behind (see atomic.py), in the places that hold persistent state
  (instance folder, models, reports, and every folder of stored images);
* closes training / evaluation jobs that a stopped process never finished (they would show as "running" forever).

Only `run.py` calls it. A WSGI runner with several worker processes must not: one worker starting up could close a job
that another worker is still running. (This application is built for one process: one SQLite file, one job lock.)
"""
from __future__ import annotations

import logging
from pathlib import Path

from . import atomic, jobs, storage

log = logging.getLogger("chequeverify.lifecycle")


def on_process_start(app) -> dict:
    cfg = app.config
    folders = [Path(cfg["INSTANCE_DIR"]), Path(cfg["MODELS_DIR"]), Path(cfg["REPORTS_DIR"]) / "final",
               *(Path(cfg["STORAGE_DIR"]) / area for area in storage.AREAS)]
    removed = sum(atomic.sweep(folder) for folder in folders)
    interrupted = jobs.mark_interrupted(str(cfg["DATABASE"]))
    if removed:
        log.warning("removed %d temporary file(s) left by an interrupted write", removed)
    if interrupted:
        log.warning("marked %d training/evaluation job(s) as interrupted by the previous shutdown", interrupted)
    return {"temp_files_removed": removed, "jobs_interrupted": interrupted}
