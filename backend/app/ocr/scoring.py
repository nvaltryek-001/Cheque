"""Field-level scoring of the OCR against a labels file.

Labels file (CSV, kept OUTSIDE the repository because it holds account numbers and names):
    file, bank_name, branch_name, ifsc_code, cheque_number, micr_code, account_number, date,
    payee_name, amount_value, amount_words            [optional: words_value]
`file` is the image path relative to the images directory; `date` is ISO (YYYY-MM-DD);
`amount_value` is the amount in rupees as an integer. The labels must be verified by a person –
labels written by the same system or model that is being scored measure nothing.

Scoring rules (a field is "correct" when …)
  bank / branch / payee   equal after upper-casing and removing punctuation and extra spaces
  amount in figures       parses to the labelled integer
  amount in words         equal word by word after the same normalisation, ignoring "ONLY"
  everything else         exactly equal
Review status is the application's own (`ok`, `review`, `missing`); nothing is re-derived here.
"""
from __future__ import annotations

import csv
import io
import math
import re
from collections import defaultdict
from pathlib import Path

from . import amounts
from .fields import FIELD_ORDER, extract_cheque

REQUIRED_COLUMNS = ["file", "bank_name", "branch_name", "ifsc_code", "cheque_number", "micr_code",
                    "account_number", "date", "payee_name", "amount_value", "amount_words"]


def _norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9 ]", " ", (s or "").upper().replace("-", " "))).strip()


def _words_norm(s: str | None) -> str:
    return " ".join(t for t in _norm(s).split() if t != "ONLY")


def truth_value(field: str, label: dict) -> str:
    return label["amount_value"] if field == "amount_figures" else label[field]


def is_correct(field: str, predicted: str | None, label: dict) -> bool:
    predicted = predicted or ""
    if field == "amount_figures":
        try:
            return amounts.parse_figures(predicted) == int(label["amount_value"])
        except (TypeError, ValueError):
            return False
    if field == "amount_words":
        return _words_norm(predicted) == _words_norm(label["amount_words"])
    if field in ("payee_name", "branch_name", "bank_name"):
        return _norm(predicted) == _norm(label[field])
    return predicted == label[field]


def read_labels(path: str | Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        raise ValueError("The labels file is empty.")
    missing = [c for c in REQUIRED_COLUMNS if c not in rows[0]]
    if missing:
        raise ValueError("The labels file lacks the column(s): " + ", ".join(missing))
    return rows


def score_extraction(cheque_id: str, out: dict, label: dict) -> list[dict]:
    """One row per field: correctness, confidence and review status of the application's output."""
    rows = []
    for k in FIELD_ORDER:
        f = out["fields"][k]
        rows.append({"cheque_id": cheque_id, "file": label["file"], "field": k,
                     "truth": truth_value(k, label), "predicted": f["value"] or "",
                     "correct": bool(is_correct(k, f["value"], label)),
                     "confidence": round(float(f["confidence"] or 0.0), 3), "status": f["status"],
                     "issues": list(f.get("issues") or [])})
    return rows


def score_directory(images_dir: str | Path, labels_csv: str | Path, engine, micr_model, limit: int | None = None) -> list[dict]:
    import cv2
    labels = read_labels(labels_csv)[:limit]
    rows: list[dict] = []
    for i, label in enumerate(labels, 1):
        img = cv2.imread(str(Path(images_dir) / label["file"]))
        if img is None:
            raise FileNotFoundError(f"Cannot read image {label['file']}")
        rows += score_extraction(f"C{i:02d}", extract_cheque(img, engine, micr_model), label)
    return rows


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """95 % Wilson interval for k successes in n trials (percent). Fields of one cheque are not
    independent, so the interval is optimistic for field-level numbers."""
    if n <= 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    s = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return round(max(0.0, (c - s) / d) * 100, 1), round(min(1.0, (c + s) / d) * 100, 1)


def summarise(rows: list[dict]) -> dict:
    by_cheque: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_cheque[r["cheque_id"]].append(r)
    fields = {}
    for k in FIELD_ORDER:
        rr = [r for r in rows if r["field"] == k]
        fields[k] = {
            "n": len(rr), "correct": sum(r["correct"] for r in rr),
            "ok": sum(r["status"] == "ok" for r in rr), "review": sum(r["status"] == "review" for r in rr),
            "missing": sum(r["status"] == "missing" for r in rr),
            "wrong_but_ok": sum((not r["correct"]) and r["status"] == "ok" for r in rr),
            "correct_but_sent_to_review": sum(r["correct"] and r["status"] != "ok" for r in rr),
            "mean_confidence": round(sum(r["confidence"] for r in rr) / len(rr), 3) if rr else None,
        }
    n_f, n_c = len(rows), len(by_cheque)
    wrong = [r for r in rows if not r["correct"]]
    ok_rows = [r for r in rows if r["status"] == "ok"]
    all_correct = sum(all(x["correct"] for x in v) for v in by_cheque.values())
    return {
        "cheques": n_c, "fields": n_f,
        "field_accuracy": {"correct": n_f - len(wrong), "of": n_f, "wilson95_percent": wilson(n_f - len(wrong), n_f)},
        "cheque_accuracy_all_fields_correct": {"correct": all_correct, "of": n_c, "wilson95_percent": wilson(all_correct, n_c)},
        "cheques_without_review": sum(all(x["status"] == "ok" for x in v) for v in by_cheque.values()),
        "cheques_all_correct_and_all_ok": sum(all(x["correct"] and x["status"] == "ok" for x in v) for v in by_cheque.values()),
        "cheques_with_wrong_value_marked_ok": sum(any((not x["correct"]) and x["status"] == "ok" for x in v) for v in by_cheque.values()),
        "wrong_values": len(wrong),
        "wrong_values_flagged_or_missing": sum(r["status"] != "ok" for r in wrong),
        "wrong_values_marked_ok": sum(r["status"] == "ok" for r in wrong),
        "fields_marked_ok": len(ok_rows), "fields_marked_ok_that_are_correct": sum(r["correct"] for r in ok_rows),
        "correct_values_sent_to_review": sum(r["correct"] and r["status"] != "ok" for r in rows),
        "per_field": fields,
    }


_ABBR = {"bank_name": "Bank", "branch_name": "Branch", "ifsc_code": "IFSC", "cheque_number": "Chq no.",
         "micr_code": "MICR", "account_number": "Account", "date": "Date", "payee_name": "Payee",
         "amount_figures": "Amt (fig)", "amount_words": "Amt (words)"}
_STATUS = {"ok": "ok", "review": "rev", "missing": "miss"}


def masked_table(rows: list[dict]) -> str:
    """Markdown table safe for a public repository: ✓/✗, confidence and review status only."""
    by_cheque: dict[str, dict] = defaultdict(dict)
    for r in rows:
        by_cheque[r["cheque_id"]][r["field"]] = r
    head = "| Cheque | " + " | ".join(_ABBR[k] for k in FIELD_ORDER) + " | Correct |"
    lines = [head, "|---|" + "---|" * (len(FIELD_ORDER) + 1)]
    for cid in sorted(by_cheque):
        cells = [f"{'✓' if by_cheque[cid][k]['correct'] else '✗'} {by_cheque[cid][k]['confidence']:.2f} "
                 f"{_STATUS[by_cheque[cid][k]['status']]}" for k in FIELD_ORDER]
        lines.append(f"| {cid} | " + " | ".join(cells) + f" | {sum(by_cheque[cid][k]['correct'] for k in FIELD_ORDER)}/10 |")
    return "\n".join(lines) + "\n"


def full_table_csv(rows: list[dict], path: str | Path) -> None:
    """PRIVATE table with the real and predicted values – never commit it."""
    from ..services import atomic
    buf = io.StringIO(newline="")
    w = csv.writer(buf)
    w.writerow(["cheque_id", "file", "field", "ground_truth", "predicted", "correct", "confidence", "review_status", "issues"])
    for r in rows:
        w.writerow([r["cheque_id"], r["file"], r["field"], r["truth"], r["predicted"],
                    "correct" if r["correct"] else "incorrect", r["confidence"], r["status"], "; ".join(r["issues"])])
    atomic.write_bytes(path, buf.getvalue().encode("utf-8"))
