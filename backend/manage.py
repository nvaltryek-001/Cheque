"""Command-line tasks for ChequeVerify AI.

    python manage.py audit                    # audit both datasets -> reports/final/*_AUDIT.json
    python manage.py train-micr               # learn MICR templates (train split) + evaluate on val/test
    python manage.py train-signature          # train fusion (train), thresholds (val), evaluate once (test)
    python manage.py eval-ocr --split test    # OCR field evaluation on a split
    python manage.py score-ocr --images DIR --labels CSV [--private-csv OUT] [--masked-report OUT.md]
                                              # score against human-verified labels kept outside git
    python manage.py import-collection --manifest M.csv --images DIR [--create-signers] [--dry-run]
                                              # enroll a collected genuine-signature set, one signing session per day
    python manage.py create-user NAME --role operator
    python manage.py reset-password NAME
    python manage.py unlock-logins              # clear the sign-in throttle (e.g. your own IP got blocked)
All dataset access is read-only.
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

from run import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))

from app.config import Config  # noqa: E402
from app.services.datasets import (audit_ocr_dataset, audit_signature_dataset, discover_ocr_images,  # noqa: E402
                                   load_signature_manifests)

REPORTS = Path(Config.REPORTS_DIR) / "final"
MODELS = Path(Config.MODELS_DIR)


def _write(name: str, data: dict) -> Path:
    REPORTS.mkdir(parents=True, exist_ok=True)
    p = REPORTS / name
    p.write_text(json.dumps(data, indent=2, default=str))
    print(f"wrote {p}")
    return p


def cmd_audit(_args) -> None:
    ocr = audit_ocr_dataset(Path(Config.OCR_DATASET_DIR))
    sig = audit_signature_dataset(Path(Config.SIGNATURE_DATASET_DIR))
    _write("OCR_DATASET_AUDIT.json", ocr)
    _write("SIGNATURE_DATASET_AUDIT.json", sig)
    print(f"OCR images: {ocr.get('total_images')}  issues: {len(ocr['issues'])}")
    print(f"Signature genuine/altered: {sig.get('genuine_rows')}/{sig.get('altered_rows')}  issues: {len(sig['issues'])}")


def cmd_train_micr(_args) -> None:
    import cv2
    from app.ocr.micr import read_micr, train_from_dataset
    items = discover_ocr_images(Path(Config.OCR_DATASET_DIR))
    model, meta = train_from_dataset([i for i in items if i["split"] == "train"])
    MODELS.mkdir(parents=True, exist_ok=True)
    model.save(MODELS / "micr_e13b.npz")
    out = {"training": meta, "label_source": "6-digit filename stem = first MICR group", "evaluation": {}}
    for split in ("val", "test"):
        n = hit = 0
        errors = []
        for it in (i for i in items if i["split"] == split):
            stem = Path(it["filename"]).stem
            if not (stem.isdigit() and len(stem) == 6):
                continue
            r = read_micr(cv2.imread(str(it["path"])), model)
            n += 1
            hit += r["cheque_number"] == stem
            if r["cheque_number"] != stem:
                errors.append({"file": it["filename"], "read": r["cheque_number"]})
        out["evaluation"][split] = {"labelled": n, "exact_match": hit,
                                    "accuracy_pct": round(100 * hit / n, 1) if n else None, "errors": errors}
        print(f"MICR cheque-number exact match on {split}: {hit}/{n}")
    _write("micr_model_evaluation.json", out)


def cmd_train_signature(_args) -> None:
    from app.signature.training import train_and_evaluate
    m = load_signature_manifests(Path(Config.SIGNATURE_DATASET_DIR))
    if m is None:
        sys.exit("signature dataset not found")
    model, report = train_and_evaluate(m, progress=lambda s: print("  " + s))
    MODELS.mkdir(parents=True, exist_ok=True)
    model.save(MODELS / "signature_fusion.json")
    _write("signature_model_evaluation.json", report)
    t = report["test"]
    print(f"model {report['model_version']}  thresholds {report['thresholds']}")
    print(f"TEST  AUC {t['roc_auc']}  EER {t['eer']}  at match threshold: {t['at_match_threshold']}")


def cmd_eval_ocr(args) -> None:
    from app.ocr.engine import get_engine
    from app.ocr.evaluate import evaluate
    from app.ocr.micr import MicrModel
    items = [i for i in discover_ocr_images(Path(Config.OCR_DATASET_DIR)) if i["split"] == args.split]
    registry = {}
    m = load_signature_manifests(Path(Config.SIGNATURE_DATASET_DIR))
    if m:
        for r in m["genuine"]:
            registry.setdefault(r["bank"], set()).add(Path(r["filename"]).stem)
    engine = get_engine(Config.OCR_ENGINE, Config.TESSERACT_CMD)
    if not engine.available:
        sys.exit("No OCR engine available (install PaddleOCR or Tesseract).")
    print(f"evaluating {len(items)} images from '{args.split}' with {engine.name}…")
    res = evaluate(items, engine, MicrModel.load(MODELS / "micr_e13b.npz"), registry)
    res["split"] = args.split
    _write(f"ocr_evaluation_{args.split}.json", res)
    summary = {k: v for k, v in res.items() if k not in ("predictions", "per_bank")}
    print(json.dumps(summary, indent=2))


def cmd_score_ocr(args) -> None:
    from app.ocr import scoring
    from app.ocr.engine import get_engine
    from app.ocr.micr import MicrModel
    engine = get_engine(Config.OCR_ENGINE, Config.TESSERACT_CMD)
    if not engine.available:
        sys.exit("No OCR engine available (install PaddleOCR or Tesseract).")
    rows = scoring.score_directory(args.images, args.labels, engine, MicrModel.load(MODELS / "micr_e13b.npz"))
    summary = scoring.summarise(rows)
    print(json.dumps(summary, indent=2))
    if args.private_csv:
        scoring.full_table_csv(rows, args.private_csv)
        print(f"private table (real + predicted values – do NOT commit): {args.private_csv}")
    if args.masked_report:
        Path(args.masked_report).write_text(scoring.masked_table(rows), encoding="utf-8")
        print(f"masked table (no values): {args.masked_report}")


def cmd_import_collection(args) -> None:
    from app.services import collection
    app = _db()
    with app.app_context():
        try:
            res = collection.import_collection(
                Path(args.manifest), Path(args.images), create_signers=args.create_signers,
                allow_poor_quality=args.allow_poor_quality, allow_missing_consent=args.allow_missing_consent,
                dry_run=args.dry_run)
        except collection.ManifestError as exc:
            sys.exit(f"import-collection: {exc}")
    for r in res["results"]:
        if not r["ok"]:
            print(f"  line {r['line']} ({r['signer_code'] or '-'}): {r['error']}")
    mode = "DRY RUN - nothing written. " if res["dry_run"] else ""
    print(f"{mode}rows {res['rows']}: valid {res['valid']}, invalid {res['invalid']}"
          + ("" if res["dry_run"] else f", enrolled {res['enrolled']}, refused by the enrollment checks {res['rejected']}, "
             f"signers created {res['signers_created']}, sessions created {res['sessions_created']}"))
    for code, rep in sorted(res["signers"].items()):
        need = ("; ".join(rep["missing"])) or "-"
        print(f"  {code}: {rep['status']} ({rep['independent_samples']} independent specimens, "
              f"{rep['independent_days']} signing days) needs: {need}")
    if res["invalid"] or res["rejected"]:
        sys.exit(1)


def cmd_unlock_logins(_args) -> None:
    app = _db()
    with app.app_context():
        from app.services import throttle
        print(f"cleared {throttle.clear_all()} recorded failed sign-in(s)")


def _db():
    from app import create_app
    return create_app()


def cmd_create_user(args) -> None:
    from app.db import connect, now_iso
    from app.services.auth import ROLES, hash_password
    if args.role not in ROLES:
        sys.exit(f"role must be one of {ROLES}")
    app = _db()
    pw = getpass.getpass("Password: ")
    conn = connect(str(app.config["DATABASE"]))
    conn.execute("INSERT INTO users(username, full_name, password_hash, role, created_at) VALUES(?,?,?,?,?)",
                 (args.username, args.full_name or "", hash_password(pw), args.role, now_iso()))
    conn.commit()
    print(f"created {args.username} ({args.role})")


def cmd_reset_password(args) -> None:
    from app.db import connect
    from app.services.auth import hash_password
    app = _db()
    pw = getpass.getpass("New password: ")
    conn = connect(str(app.config["DATABASE"]))
    cur = conn.execute("UPDATE users SET password_hash=?, token_version=token_version+1, failed_logins=0, "
                       "locked_until=NULL, is_active=1 WHERE username=?", (hash_password(pw), args.username))
    conn.commit()
    print("password reset" if cur.rowcount else "no such user")


def main() -> None:
    ap = argparse.ArgumentParser(description="ChequeVerify AI management")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("audit").set_defaults(fn=cmd_audit)
    sub.add_parser("train-micr").set_defaults(fn=cmd_train_micr)
    sub.add_parser("train-signature").set_defaults(fn=cmd_train_signature)
    e = sub.add_parser("eval-ocr")
    e.add_argument("--split", default="test", choices=["train", "val", "test"])
    e.set_defaults(fn=cmd_eval_ocr)
    sc = sub.add_parser("score-ocr", help="score OCR against a labels CSV (human-verified, kept outside git)")
    sc.add_argument("--images", required=True, help="directory the CSV's 'file' column is relative to")
    sc.add_argument("--labels", required=True, help="labels CSV (see app/ocr/scoring.py for the columns)")
    sc.add_argument("--private-csv", help="write the full table with real values (private)")
    sc.add_argument("--masked-report", help="write a markdown table with no values (safe for a repository)")
    sc.set_defaults(fn=cmd_score_ocr)
    c = sub.add_parser("create-user")
    c.add_argument("username")
    c.add_argument("--role", default="operator")
    c.add_argument("--full-name", default="")
    c.set_defaults(fn=cmd_create_user)
    ic = sub.add_parser("import-collection", help="enroll a collected genuine-signature set (see docs/SIGNATURE_ENROLLMENT.md)")
    ic.add_argument("--manifest", required=True, help="CSV: signer_code, session_date, file, ... (kept outside git)")
    ic.add_argument("--images", required=True, help="folder the manifest's 'file' column is relative to (kept outside git)")
    ic.add_argument("--create-signers", action="store_true", help="create signers that do not exist yet (needs a bank column)")
    ic.add_argument("--allow-poor-quality", action="store_true", help="enroll images below the quality standard")
    ic.add_argument("--allow-missing-consent", action="store_true", help="accept rows without consent_ref (test data only)")
    ic.add_argument("--dry-run", action="store_true", help="validate the manifest and write nothing")
    ic.set_defaults(fn=cmd_import_collection)
    sub.add_parser("unlock-logins").set_defaults(fn=cmd_unlock_logins)
    r = sub.add_parser("reset-password")
    r.add_argument("username")
    r.set_defaults(fn=cmd_reset_password)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
