"""Bulk import of a collected genuine-signature set (app/services/collection.py, manage.py import-collection)."""
import argparse
import csv
import sys

import cv2
import pytest

from app.db import query
from app.services import collection
from conftest import BACKEND, synthetic_signature
from test_enrollment import crooked

sys.path.insert(0, str(BACKEND))

HEADER = ["signer_code", "session_date", "file", "bank", "method", "instrument", "consent_ref", "source_ref"]


def write_images(folder, names_seeds):
    folder.mkdir(parents=True, exist_ok=True)
    for name, seed in names_seeds:
        cv2.imwrite(str(folder / name), synthetic_signature(seed))


def write_manifest(path, rows, header=HEADER):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow([r.get(h, "") for h in header])


def good_row(code, day, name, **extra):
    return {"signer_code": code, "session_date": day, "file": name, "bank": "axis", "method": "paper_scan",
            "instrument": "pen", "consent_ref": "CF-1", **extra}


def run(app, manifest, images, **kw):
    with app.app_context():
        return collection.import_collection(manifest, images, **kw)


@pytest.fixture()
def folder(tmp_path):
    imgs = tmp_path / "images"
    write_images(imgs, [(f"a{i}.png", 700 + i) for i in range(6)] + [(f"b{i}.png", 800 + i) for i in range(6)])
    return tmp_path, imgs


def full_manifest(path):
    rows = []
    for code, prefix in (("P001", "a"), ("P002", "b")):
        for i in range(6):
            rows.append(good_row(code, "2026-09-10" if i < 3 else "2026-10-12", f"{prefix}{i}.png"))
    write_manifest(path, rows)


def test_a_collection_is_enrolled_as_sessions_and_becomes_ready(app, folder):
    tmp, imgs = folder
    full_manifest(tmp / "m.csv")
    res = run(app, tmp / "m.csv", imgs, create_signers=True)
    assert (res["rows"], res["valid"], res["invalid"], res["enrolled"], res["rejected"]) == (12, 12, 0, 12, 0)
    assert res["signers_created"] == 2 and res["sessions_created"] == 4
    assert all(s["status"] == "ready" and s["independent_days"] == 2 for s in res["signers"].values())
    with app.app_context():
        row = query("SELECT * FROM specimens ORDER BY id LIMIT 1", one=True)
        assert row["session_id"] is not None and row["origin"] == "upload"
        assert query("SELECT COUNT(*) c FROM enrollment_sessions", one=True)["c"] == 4
        entry = query("SELECT * FROM audit_log WHERE action='collection.import'", one=True)
        assert '"enrolled": 12' in entry["detail"] and "P001" not in entry["detail"]


def test_a_second_import_adds_only_new_images(app, folder):
    tmp, imgs = folder
    full_manifest(tmp / "m.csv")
    run(app, tmp / "m.csv", imgs, create_signers=True)
    res = run(app, tmp / "m.csv", imgs, create_signers=True)
    assert res["enrolled"] == 0 and res["rejected"] == 12          # every image is already enrolled (duplicates)
    assert {r["code"] for r in res["results"] if not r["ok"]} == {"duplicate_specimen"}


def test_dry_run_validates_and_writes_nothing(app, folder):
    tmp, imgs = folder
    full_manifest(tmp / "m.csv")
    res = run(app, tmp / "m.csv", imgs, create_signers=True, dry_run=True)
    assert res["dry_run"] and res["valid"] == 12 and res["enrolled"] == 0
    with app.app_context():
        assert query("SELECT COUNT(*) c FROM signers", one=True)["c"] == 0
        assert query("SELECT COUNT(*) c FROM specimens", one=True)["c"] == 0


@pytest.mark.parametrize("change,message", [
    ({"signer_code": "x"}, "signer_code"),
    ({"session_date": "10/09/2026"}, "YYYY-MM-DD"),
    ({"session_date": "2999-01-01"}, "future"),
    ({"method": "fax"}, "method"),
    ({"consent_ref": ""}, "consent_ref is missing"),
    ({"bank": "nowhere"}, "bank is not one of"),
    ({"bank": ""}, "bank is required for a new signer"),
    ({"file": "../outside.png"}, "outside the images folder"),
    ({"file": "/etc/passwd"}, "outside the images folder"),
    ({"file": "missing.png"}, "file not found"),
    ({"file": "notes.txt"}, "unsupported file type"),
])
def test_invalid_rows_are_reported_and_the_rest_is_imported(app, folder, change, message):
    tmp, imgs = folder
    (imgs / "notes.txt").write_text("x")
    (tmp / "outside.png").write_bytes(b"x")
    write_manifest(tmp / "m.csv", [good_row("P001", "2026-09-10", "a0.png"), {**good_row("P002", "2026-09-10", "b0.png"), **change}])
    res = run(app, tmp / "m.csv", imgs, create_signers=True)
    assert res["valid"] == 1 and res["invalid"] == 1 and res["enrolled"] == 1
    bad = [r for r in res["results"] if not r["ok"]]
    assert len(bad) == 1 and message in bad[0]["error"] and bad[0]["line"] == 3


def test_unknown_signers_need_the_create_flag_and_duplicates_in_the_manifest_are_refused(app, folder):
    tmp, imgs = folder
    write_manifest(tmp / "m.csv", [good_row("P001", "2026-09-10", "a0.png"), good_row("P001", "2026-09-10", "a0.png")])
    res = run(app, tmp / "m.csv", imgs)
    assert res["valid"] == 0 and "unknown signer" in res["results"][0]["error"]
    res = run(app, tmp / "m.csv", imgs, create_signers=True)
    assert res["valid"] == 1 and "listed twice" in res["results"][1]["error"]


def test_missing_consent_is_allowed_only_on_request(app, folder):
    tmp, imgs = folder
    write_manifest(tmp / "m.csv", [good_row("P001", "2026-09-10", "a0.png", consent_ref="")])
    assert run(app, tmp / "m.csv", imgs, create_signers=True)["invalid"] == 1
    res = run(app, tmp / "m.csv", imgs, create_signers=True, allow_missing_consent=True)
    assert res["enrolled"] == 1


def test_the_enrollment_checks_apply_to_imported_images(app, folder):
    tmp, imgs = folder
    base = synthetic_signature(900)
    cv2.imwrite(str(imgs / "first.png"), base)
    cv2.imwrite(str(imgs / "crooked.png"), crooked(base))
    cv2.imwrite(str(imgs / "other.png"), synthetic_signature(901))
    write_manifest(tmp / "m.csv", [good_row("P001", "2026-09-10", n) for n in ("first.png", "crooked.png", "other.png")])
    res = run(app, tmp / "m.csv", imgs, create_signers=True)
    assert res["enrolled"] == 2 and res["rejected"] == 1
    assert [r["code"] for r in res["results"] if not r["ok"]] == ["same_signing"]


def test_a_symlink_pointing_outside_the_folder_is_refused(app, folder):
    tmp, imgs = folder
    (tmp / "secret.png").write_bytes(open(imgs / "a0.png", "rb").read())
    try:
        (imgs / "link.png").symlink_to(tmp / "secret.png")
    except OSError:
        pytest.skip("symlinks not available")
    write_manifest(tmp / "m.csv", [good_row("P001", "2026-09-10", "link.png")])
    res = run(app, tmp / "m.csv", imgs, create_signers=True)
    assert res["valid"] == 0 and "outside the images folder" in res["results"][0]["error"]


def test_inactive_signers_are_not_enrolled_into(app, folder):
    tmp, imgs = folder
    write_manifest(tmp / "m.csv", [good_row("P001", "2026-09-10", "a0.png")])
    run(app, tmp / "m.csv", imgs, create_signers=True)
    with app.app_context():
        from app.db import execute
        execute("UPDATE signers SET status='inactive'")
    write_manifest(tmp / "m2.csv", [good_row("P001", "2026-09-11", "a1.png")])
    res = run(app, tmp / "m2.csv", imgs, create_signers=True)
    assert res["enrolled"] == 0 and res["rejected"] == 1 and res["results"][-1]["error"] == "signer is inactive"


@pytest.mark.parametrize("content,message", [
    ("signer_code,file\nP001,a0.png\n", "session_date"),
    ("signer_code,session_date,file\n", "no rows"),
    ("", "needs the column"),
])
def test_an_unusable_manifest_is_an_error_of_the_whole_import(app, folder, content, message):
    tmp, imgs = folder
    (tmp / "m.csv").write_text(content)
    with pytest.raises(collection.ManifestError, match=message):
        run(app, tmp / "m.csv", imgs)


def test_missing_files_and_folders(app, folder):
    tmp, imgs = folder
    with pytest.raises(collection.ManifestError, match="cannot read the manifest"):
        run(app, tmp / "nope.csv", imgs)
    write_manifest(tmp / "m.csv", [good_row("P001", "2026-09-10", "a0.png")])
    with pytest.raises(collection.ManifestError, match="images folder"):
        run(app, tmp / "m.csv", tmp / "no-such-folder")


def test_manifest_with_bom_and_blank_lines(app, folder):
    tmp, imgs = folder
    (tmp / "m.csv").write_text("﻿" + ",".join(HEADER) + "\n\nP001,2026-09-10,a0.png,axis,paper_scan,pen,CF-1,\n\n",
                               encoding="utf-8")
    assert run(app, tmp / "m.csv", imgs, create_signers=True)["enrolled"] == 1


def test_command_line_prints_a_summary_without_values_and_exits_non_zero_on_problems(app, folder, monkeypatch, capsys):
    import manage
    tmp, imgs = folder
    monkeypatch.setattr(manage, "_db", lambda: app)
    ns = lambda **kw: argparse.Namespace(**{"manifest": str(tmp / "m.csv"), "images": str(imgs), "create_signers": True,
                                            "allow_poor_quality": False, "allow_missing_consent": False,
                                            "dry_run": False, **kw})
    full_manifest(tmp / "m.csv")
    manage.cmd_import_collection(ns())
    out = capsys.readouterr().out
    assert "enrolled 12" in out and "P001: ready (6 independent specimens, 2 signing days)" in out
    write_manifest(tmp / "m.csv", [good_row("P003", "2026-09-10", "a0.png", bank="nowhere")])
    with pytest.raises(SystemExit) as exc:
        manage.cmd_import_collection(ns(dry_run=True))
    assert exc.value.code == 1 and "DRY RUN" in capsys.readouterr().out
    with pytest.raises(SystemExit) as exc:
        manage.cmd_import_collection(ns(manifest=str(tmp / "absent.csv")))
    assert "cannot read the manifest" in str(exc.value)
