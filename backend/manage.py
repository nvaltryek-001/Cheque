"""Command-line tasks for ChequeVerify AI.

    python manage.py audit                    # audit both datasets -> reports/final/*_AUDIT.json
    python manage.py train-micr               # learn MICR templates (train split) + evaluate on val/test
    python manage.py train-signature          # train fusion (train), thresholds (val), evaluate once (test)
    python manage.py eval-ocr --split test    # OCR field evaluation on a split
    python manage.py score-ocr --images DIR --labels CSV [--private-csv OUT] [--masked-report OUT.md]
                                              # score against human-verified labels kept outside git
    python manage.py import-collection --manifest M.csv --images DIR [--create-signers] [--dry-run]
                                              # enroll a collected genuine-signature set, one signing session per day
    python manage.py eval-signatures --dataset
                                              # what the delivered signature data can and cannot measure (counts only)
    python manage.py eval-signatures --manifest M.csv --images DIR --split val [--out-dir DIR]
    python manage.py eval-signatures --manifest M.csv --images DIR --split test --final --thresholds VAL_REPORT.json
                                              # validity-checked evaluation on independently collected signatures
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


def cmd_eval_signatures(args) -> None:
    """Valid signature evaluation (docs/SIGNATURE_EVALUATION_PROTOCOL.md). Reports contain counts and rates only."""
    from app.signature import protocol
    from app.signature.compare import FusionModel
    if args.dataset:
        manifests = load_signature_manifests(Path(Config.SIGNATURE_DATASET_DIR))
        if manifests is None:
            sys.exit("signature dataset not found")
        report = protocol.describe_signature_dataset(manifests)
        stem, out_dir = "SIGNATURE_EVALUATION_STATUS", Path(args.out_dir) if args.out_dir else REPORTS
    else:
        if not (args.manifest and args.images):
            sys.exit("give --dataset, or --manifest and --images (an independently collected evaluation set)")
        if args.split == "test" and not args.final:
            sys.exit("the test split is for the final run only: add --final (use --split val while developing)")
        model = FusionModel.load(Path(args.model) if args.model else MODELS / "signature_fusion.json")
        if model is None:
            sys.exit("no trained signature model found (use --model, or run train-signature)")
        try:
            items, problems = protocol.load_manifest(Path(args.manifest), Path(args.images))
        except ValueError as exc:
            sys.exit(str(exc))
        for p in problems[:20]:
            print(f"  manifest line {p['line']}: {p['message']}")
        if problems:
            print(f"{len(problems)} manifest row(s) were skipped")
        thresholds, version = None, model.version
        if args.thresholds:
            try:
                prior = json.loads(Path(args.thresholds).read_text())
            except (OSError, ValueError):
                sys.exit("cannot read the --thresholds report")
            if prior.get("split") != "val" or not prior.get("suggested_thresholds"):
                sys.exit("--thresholds must be a validation-split report from eval-signatures that has suggested_thresholds")
            th = prior["suggested_thresholds"]
            thresholds = {"match": th["match"], "mismatch": th["mismatch"]}
            version = f"{model.version}|match={th['match']}|mismatch={th['mismatch']}"
        cfg = {}
        if args.ref_specimens:
            cfg["reference_min_specimens"] = args.ref_specimens
        if args.ref_days:
            cfg["reference_min_sessions"] = args.ref_days
        hashes = model.meta.get("fitted_signer_hashes")
        ledger = Path(args.ledger) if args.ledger else Path(Config.INSTANCE_DIR) / "signature_eval_ledger.json"
        report = protocol.evaluate(items, split=args.split, decider=protocol.default_decider(model, thresholds),
                                   model_version=version, final=args.final, cfg=cfg, ledger_path=ledger,
                                   model_signer_hashes=set(hashes) if hashes is not None else None,
                                   progress=lambda m: print("  " + m))
        stem = f"signature_eval_{args.split}" + ("_final" if report["final"] else "")
        out_dir = Path(args.out_dir) if args.out_dir else None
    text = protocol.render_markdown(report)
    print(text)
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{stem}.json").write_text(json.dumps(report, indent=2, default=str))
        (out_dir / f"{stem}.md").write_text(text, encoding="utf-8")
        print(f"wrote {out_dir / (stem + '.json')} and {out_dir / (stem + '.md')}")
    if not args.dataset and not report["validity"]["valid"]:
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
    ev = sub.add_parser("eval-signatures", help="valid signature evaluation (see docs/SIGNATURE_EVALUATION_PROTOCOL.md)")
    ev.add_argument("--dataset", action="store_true", help="describe what the delivered signature dataset can measure (no images are read)")
    ev.add_argument("--manifest", help="evaluation manifest CSV (kept outside git)")
    ev.add_argument("--images", help="folder the manifest's 'file' column is relative to (kept outside git)")
    ev.add_argument("--split", default="val", choices=["val", "test"])
    ev.add_argument("--final", action="store_true", help="final run on the test split (recorded in the ledger)")
    ev.add_argument("--model", help="fusion model JSON (default: the deployed model)")
    ev.add_argument("--thresholds", help="validation-split report whose suggested thresholds are frozen for this run")
    ev.add_argument("--ledger", help="test-use ledger (default: instance/signature_eval_ledger.json)")
    ev.add_argument("--out-dir", help="write the JSON and markdown report here (counts and rates only)")
    ev.add_argument("--ref-specimens", type=int, help="reference specimens per signer (default: the deployed policy, 5)")
    ev.add_argument("--ref-days", type=int, help="signing days among the references (default: the deployed policy, 2)")
    ev.set_defaults(fn=cmd_eval_signatures)
    sub.add_parser("unlock-logins").set_defaults(fn=cmd_unlock_logins)
    r = sub.add_parser("reset-password")
    r.add_argument("username")
    r.set_defaults(fn=cmd_reset_password)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
