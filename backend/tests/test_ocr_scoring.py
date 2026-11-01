"""Scoring rules and summaries (app/ocr/scoring.py) – synthetic labels only."""
import csv

import pytest

from app.ocr import scoring

LABEL = {"file": "a/1.png", "bank_name": "Axis Bank", "branch_name": "Kompally - 500014", "ifsc_code": "UTIB0000425",
         "cheque_number": "123456", "micr_code": "110000123", "account_number": "700123456789098",
         "date": "2026-07-21", "payee_name": "Meera Kulkarni", "amount_value": "1825000",
         "amount_words": "Eighteen Lakh Twenty-Five Thousand Only"}


@pytest.mark.parametrize("field,pred,expected", [
    ("payee_name", "MEERA  KULKARNI", True), ("payee_name", "Meera Kulkarn", False),
    ("branch_name", "kompally 500014", True), ("branch_name", "Kompaily - 500014", False),
    ("amount_figures", "18,25,000/-", True), ("amount_figures", "18/-", False), ("amount_figures", "", False),
    ("amount_words", "Eighteen Lakh Twenty Five Thousand", True), ("amount_words", "Eighteen Lakh Twenty Thousand", False),
    ("ifsc_code", "UTIB0000425", True), ("ifsc_code", "UTIB0000428", False),
    ("date", "2026-07-21", True), ("date", "2026-04-21", False), ("account_number", "", False),
])
def test_is_correct(field, pred, expected):
    assert scoring.is_correct(field, pred, LABEL) is expected


def _out(values, status="ok", conf=0.9):
    return {"fields": {k: {"value": values.get(k, ""), "confidence": conf, "status": status if values.get(k) else "missing", "issues": []}
                       for k in scoring.FIELD_ORDER}}


def test_summary_counts_silent_errors_and_review_routing():
    good = _out({"bank_name": "Axis Bank", "branch_name": "Kompally - 500014", "ifsc_code": "UTIB0000425", "cheque_number": "123456",
                 "micr_code": "110000123", "account_number": "700123456789098", "date": "2026-07-21",
                 "payee_name": "Meera Kulkarni", "amount_figures": "18,25,000/-",
                 "amount_words": "Eighteen Lakh Twenty-Five Thousand Only"})
    bad = _out({k: f["value"] for k, f in good["fields"].items()})
    bad["fields"]["payee_name"]["value"] = "Meera Kulkarn"                      # wrong and marked ok → silent error
    bad["fields"]["date"].update(value="2026-04-21", status="review")         # wrong but flagged
    bad["fields"]["account_number"].update(value="", status="missing")        # missing counts as wrong, flagged
    rows = scoring.score_extraction("C01", good, LABEL) + scoring.score_extraction("C02", bad, LABEL)
    s = scoring.summarise(rows)
    assert s["cheques"] == 2 and s["fields"] == 20
    assert s["field_accuracy"]["correct"] == 17
    assert s["cheque_accuracy_all_fields_correct"]["correct"] == 1
    assert s["wrong_values"] == 3 and s["wrong_values_marked_ok"] == 1 and s["wrong_values_flagged_or_missing"] == 2
    assert s["cheques_with_wrong_value_marked_ok"] == 1
    assert s["per_field"]["payee_name"]["wrong_but_ok"] == 1
    assert s["fields_marked_ok"] == 18 and s["fields_marked_ok_that_are_correct"] == 17   # 10 + 8 ok; only the payee is wrong


def test_masked_table_has_no_values():
    out = _out({"payee_name": "Meera Kulkarni", "account_number": "700123456789098"})
    table = scoring.masked_table(scoring.score_extraction("C01", out, LABEL))
    assert "Meera" not in table and "7001" not in table and "C01" in table and "✓" in table and "✗" in table


def test_wilson_interval_bounds():
    lo, hi = scoring.wilson(196, 200)
    assert 0 < lo < 98 < hi <= 100
    assert scoring.wilson(0, 0) is None
    assert scoring.wilson(0, 20)[0] == 0.0


def test_read_labels_validates_columns(tmp_path):
    p = tmp_path / "l.csv"
    p.write_text("file,bank_name\na,b\n", encoding="utf-8")
    with pytest.raises(ValueError, match="lacks the column"):
        scoring.read_labels(p)
    p.write_text("", encoding="utf-8")
    with pytest.raises(ValueError):
        scoring.read_labels(p)
    with open(p, "w", newline="", encoding="utf-8-sig") as fh:      # Excel-style BOM is accepted
        w = csv.DictWriter(fh, fieldnames=list(LABEL)); w.writeheader(); w.writerow(LABEL)
    assert scoring.read_labels(p)[0]["file"] == "a/1.png"
