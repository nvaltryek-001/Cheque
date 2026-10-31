"""Background training / evaluation jobs (one at a time) recorded in model_runs."""
from __future__ import annotations

import json
import logging
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path

from ..db import connect, now_iso
from . import models as model_store
from .datasets import discover_ocr_images, load_signature_manifests

log = logging.getLogger("chequeverify.jobs")
_running = threading.Lock()


def _update(db_path: str, run_id: int, **cols) -> None:
    conn = connect(db_path)
    try:
        sets = ", ".join(f"{k}=?" for k in cols)
        conn.execute(f"UPDATE model_runs SET {sets} WHERE id=?", (*cols.values(), run_id))
        conn.commit()
    finally:
        conn.close()


def start(app, kind: str, config: dict, user_id: int | None) -> int:
    if not _running.acquire(blocking=False):
        raise RuntimeError("Another training/evaluation job is already running.")
    db_path = str(app.config["DATABASE"])
    conn = connect(db_path)
    try:
        cur = conn.execute("INSERT INTO model_runs(kind, status, config_json, started_by, started_at) VALUES(?,?,?,?,?)",
                           (kind, "running", json.dumps(config), user_id, now_iso()))
        conn.commit()
        run_id = cur.lastrowid
    finally:
        conn.close()

    def work():
        try:
            with app.app_context():
                metrics, version = RUNNERS[kind](app, config, lambda m: _update(db_path, run_id, message=m))
            _update(db_path, run_id, status="completed", metrics_json=json.dumps(metrics, default=str),
                    model_version=version, finished_at=now_iso(), message="completed")
        except Exception as exc:  # noqa: BLE001 - record any failure
            log.error("job %s failed: %s", run_id, traceback.format_exc())
            _update(db_path, run_id, status="failed", message=str(exc)[:500], finished_at=now_iso())
        finally:
            _running.release()

    if app.config.get("TESTING") and config.get("synchronous"):
        work()
    else:
        threading.Thread(target=work, name=f"job-{run_id}", daemon=True).start()
    return run_id


def _reports_dir(app) -> Path:
    d = Path(app.config["REPORTS_DIR"]) / "final"
    d.mkdir(parents=True, exist_ok=True)
    return d


def run_signature(app, config: dict, progress) -> tuple[dict, str]:
    from ..signature.training import train_and_evaluate
    m = load_signature_manifests(app.config["SIGNATURE_DATASET_DIR"])
    if m is None:
        raise RuntimeError("Signature dataset not found.")
    model, report = train_and_evaluate(m, {k: v for k, v in config.items() if k != "synchronous"}, progress)
    model.save(Path(app.config["MODELS_DIR"]) / model_store.FUSION_FILE)
    (_reports_dir(app) / "signature_model_evaluation.json").write_text(json.dumps(report, indent=2))
    return report, model.version


def run_micr(app, config: dict, progress) -> tuple[dict, str]:
    from ..ocr.micr import read_micr, train_from_dataset
    import cv2
    items = discover_ocr_images(Path(app.config["OCR_DATASET_DIR"]))
    train = [i for i in items if i["split"] == "train"]
    if not train:
        raise RuntimeError("OCR dataset train split not found.")
    progress("learning glyph templates from train split")
    model, meta = train_from_dataset(train)
    version = "micr-e13b-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    model.meta["version"] = version
    results = {}
    for split in ("val", "test"):
        progress(f"evaluating on {split}")
        n = hit = 0
        misses = []
        for it in (i for i in items if i["split"] == split):
            stem = Path(it["filename"]).stem
            if not (stem.isdigit() and len(stem) == 6):
                continue
            r = read_micr(cv2.imread(str(it["path"])), model)
            n += 1
            hit += r["cheque_number"] == stem
            if r["cheque_number"] != stem:
                misses.append({"file": it["filename"], "read": r["cheque_number"]})
        results[split] = {"labelled": n, "exact_match": hit, "accuracy_pct": round(100 * hit / n, 1) if n else None,
                          "errors": misses}
    model.save(Path(app.config["MODELS_DIR"]) / model_store.MICR_FILE)
    report = {"model_version": version, "training": meta, "evaluation": results,
              "label_source": "6-digit filename stem = first MICR group (cheque number)"}
    (_reports_dir(app) / "micr_model_evaluation.json").write_text(json.dumps(report, indent=2))
    return report, version


def run_ocr_eval(app, config: dict, progress) -> tuple[dict, str]:
    from ..ocr.evaluate import evaluate
    split = config.get("split", "test")
    items = [i for i in discover_ocr_images(Path(app.config["OCR_DATASET_DIR"])) if i["split"] == split]
    if not items:
        raise RuntimeError(f"No images in split '{split}'.")
    registry = {}
    m = load_signature_manifests(app.config["SIGNATURE_DATASET_DIR"])
    if m:
        for r in m["genuine"]:
            registry.setdefault(r["bank"], set()).add(Path(r["filename"]).stem)
    progress(f"running OCR on {len(items)} {split} images")
    res = evaluate(items, model_store.ocr_engine(), model_store.micr_model(), registry)
    res["split"] = split
    (_reports_dir(app) / f"ocr_evaluation_{split}.json").write_text(json.dumps(res, indent=2))
    return res, f"ocr-{res['engine']}"


RUNNERS = {"signature_train": run_signature, "micr_train": run_micr, "ocr_eval": run_ocr_eval}
