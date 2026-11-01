"""Regression tests for the field-level OCR fixes (phase 1). Synthetic inputs only – no cheque data."""
import shutil

import cv2
import numpy as np
import pytest

from app.ocr import amounts, fields as F
from app.ocr.engine import Line
from app.ocr.regions import text_rows

HAS_TESSERACT = shutil.which("tesseract") is not None


# ---------------------------------------------------------------- amount in figures
@pytest.mark.parametrize("text,words,expected", [
    ("1, 50, 000 in", None, 150000),          # spaces inside the digit grouping
    ("Tat zg 21,05, 000", 2105000, 2105000),
    ("“% 28.34,000/-", None, 2834000),         # comma read as a dot
    ("= 1,20,000/-", None, 120000),
])
def test_figures_normalisation(text, words, expected):
    value, _ = F._parse_figures_tokens(text, words)
    assert value == expected


def test_figures_tail_of_words_line_is_not_the_amount():
    # "... Forty Only" sits in the figures region on some layouts; it must not beat the real box
    v1, _ = F._parse_figures_tokens("1 Forty Only", None)
    v2, _ = F._parse_figures_tokens("z= 26,740/-", None)
    assert v1 == 1 and v2 == 26740
    best = max([(v1, 0.81), (v2, 0.12)], key=lambda c: (c[0] >= 100, c[1]))
    assert best[0] == 26740


# ---------------------------------------------------------------- account number
@pytest.mark.parametrize("text,expected", [
    ("ACNO =700123456789012", "700123456789012"),         # symbol glued to the number
    ("Alc. No. 4005123456789 0", "40051234567890"),       # letter-spaced last digit
    ("4 0 0 5 1 2 3 4 5 6 7 8 9 1", "40051234567891"),    # fully letter-spaced
    ("255 600123456789", "600123456789"),                # misread label kept apart
    ("cto 4005123456/7892", "40051234567892"),           # stray stroke inside the number
    ("no digits here", ""),
])
def test_digits_of(text, expected):
    assert F._digits_of(text) == expected


class FakeEngine:
    """Returns scripted lines per call, so the decision logic can be tested without Tesseract."""
    available = True
    name = "fake"

    def __init__(self, script):
        self.script = list(script)

    def read(self, img, mode="block"):
        text, conf = self.script.pop(0) if self.script else ("", 0.0)
        if not text:
            return []
        words, x = [], 0
        for tok in text.split():
            words.append((tok, conf, x, 0, 10 * len(tok), 20))
            x += 10 * len(tok) + 10
        return [Line(text, conf, 0, 0, x, 20, words)]


def _account_region():
    img = np.full((120, 700, 3), 255, np.uint8)
    cv2.putText(img, "A/C NO 700123456789012", (20, 70), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 2)
    return img


def test_account_reads_agree_and_disagree():
    agree = FakeEngine([("ACNO 700123456789012", 0.9), ("700123456789012", 0.9)])
    v, conf, _, issues = F._read_account(_account_region(), agree)
    assert v == "700123456789012" and not issues and conf >= 0.75
    differ = FakeEngine([("ACNO 740123456789012", 0.9), ("700123456789012", 0.9)])
    v, conf, _, issues = F._read_account(_account_region(), differ)
    assert v == "700123456789012" and issues and conf <= 0.6   # digits pass wins, but review


def test_account_length_rule_is_known_for_published_formats_only():
    assert F.ACCOUNT_LENGTHS["ICICI Bank"] == {12} and "Canara Bank" not in F.ACCOUNT_LENGTHS


# ---------------------------------------------------------------- payee vote
class ScriptedEngine:
    """Returns the n-th scripted line list per call: each item is (text, [word confidences])."""
    available = True
    name = "scripted"

    def __init__(self, items):
        self.items = list(items)

    def read(self, img, mode="block"):
        text, confs = self.items.pop(0) if self.items else ("", [])
        if not text:
            return []
        toks, x, words = text.split(), 0, []
        for tok, c in zip(toks, confs or [0.9] * len(toks)):
            words.append((tok, c, x, 0, 10 * len(tok), 20))
            x += 10 * len(tok) + 10
        return [Line(text, sum(w[1] for w in words) / len(words), 0, 0, x, 20, words)]


def _payee_region():
    region = np.full((80, 600, 3), 255, np.uint8)
    cv2.putText(region, "Meera Kulkarni", (20, 55), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (0, 0, 0), 3)
    return region


def test_payee_all_renderings_agree_is_ok():
    eng = ScriptedEngine([("Meera Kulkarni", []), ("Meera Kulkarni", []), ("Meera Kulkarni", [])])
    name, conf, _, issues = F._read_payee(_payee_region(), eng)
    assert name == "Meera Kulkarni" and issues == []
    assert F._field(name, conf, "region", "", issues)["status"] == "ok"


def test_payee_misread_in_first_rendering_is_outvoted_and_flagged():
    # the failure seen on real cheques: the ink-row read drops a letter; the grey reads are right
    eng = ScriptedEngine([("Meera Kulkarn", []), ("Meera Kulkarni", []), ("Meera Kulkarni", [])])
    name, conf, _, issues = F._read_payee(_payee_region(), eng)
    assert name == "Meera Kulkarni"                      # plurality wins …
    assert any("differ" in i and "Meera Kulkarn" in i for i in issues)
    assert F._field(name, conf, "region", "", issues)["status"] == "review"   # … but is never silently ok


def test_payee_empty_reads_abstain_but_a_lone_reading_is_review():
    eng = ScriptedEngine([("Anil Deshmukh", []), ("", []), ("", [])])
    name, conf, _, issues = F._read_payee(_payee_region(), eng)
    assert name == "Anil Deshmukh" and any("one rendering" in i for i in issues)
    eng = ScriptedEngine([("Anil Deshmukh", []), ("Anil Deshmukh", []), ("", [])])
    _, _, _, issues = F._read_payee(_payee_region(), eng)
    assert issues == []                                # two agreeing reads, one abstention


def test_payee_three_different_reads_go_to_review():
    eng = ScriptedEngine([("Meera Kulkarni", [0.9, 0.9]), ("Meera Kulkarn", [0.8, 0.8]), ("Meera Kulkarnl", [0.7, 0.7])])
    name, conf, _, issues = F._read_payee(_payee_region(), eng)
    assert name == "Meera Kulkarni" and issues and F._field(name, conf, "region", "", issues)["status"] == "review"


def test_payee_weak_word_and_short_fragment_flagged_but_initials_are_not():
    eng = ScriptedEngine([("Nirmal wa", [0.95, 0.3])] * 3)
    _, _, _, issues = F._read_payee(_payee_region(), eng)
    assert any("Low-confidence" in i for i in issues) and any("short fragment" in i for i in issues)
    eng = ScriptedEngine([("R. Kavita", [0.95, 0.95])] * 3)
    _, _, _, issues = F._read_payee(_payee_region(), eng)
    assert issues == []


def test_payee_nothing_found_returns_none():
    assert F._read_payee(np.full((80, 600, 3), 255, np.uint8), ScriptedEngine([])) is None


# ---------------------------------------------------------------- amount in words
def test_words_spelling_changes_are_reported():
    text, changed = F._number_words("Rupees Nine lack thirty thousand only")
    assert text == "Nine Lac Thirty Thousand Only" and changed == [("lack", "Lac")]
    text, changed = F._number_words("Twenty-Three Lakh Forty-Five Thousand Only")
    assert changed == []                                # case / hyphen differences are not spelling changes
    assert amounts.words_to_number(text) == 2345000


# ---------------------------------------------------------------- boxed date
def _boxed_date(digits="28072026", hair=1):
    """Eight boxes with hair-thin separators (ICICI style) and digits inside."""
    h, bw, x0, y0 = 70, 46, 30, 30
    img = np.full((140, x0 * 2 + bw * 8, 3), 255, np.uint8)
    for i in range(9):
        cv2.line(img, (x0 + i * bw, y0), (x0 + i * bw, y0 + h), (90, 90, 90), hair)
    cv2.line(img, (x0, y0), (x0 + 8 * bw, y0), (90, 90, 90), hair)
    cv2.line(img, (x0, y0 + h), (x0 + 8 * bw, y0 + h), (90, 90, 90), hair)
    for i, d in enumerate(digits):
        cv2.putText(img, d, (x0 + i * bw + 10, y0 + h - 15), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 0), 3)
    return img


def test_drop_bars_removes_thin_separators_but_keeps_digits():
    img = _boxed_date()
    plain = max(text_rows(img, 0.7), key=lambda r: r.n, default=None)
    clean = max(text_rows(img, 0.7, drop_bars=True), key=lambda r: r.n)
    assert clean.n == 8                       # eight digit glyphs, separators gone
    if plain is not None:
        assert (clean.image < 128).sum() <= (plain.image < 128).sum()


@pytest.mark.skipif(not HAS_TESSERACT, reason="tesseract not installed")
def test_boxed_date_reads_with_thin_separators():
    from app.ocr.engine import get_engine
    value, _, _, _ = F._extract_date(_boxed_date("28072026"), get_engine("tesseract"))
    assert value == "2026-07-28"


def test_implausible_or_invalid_dates_are_rejected():
    eng = FakeEngine([("31139999", 0.9)] * 20)
    value, _, _, issues = F._extract_date(_boxed_date("31139999"), eng)
    assert value == "" and issues


def test_agreement_within_one_rendering_family_is_not_confirmation():
    # read order: box strip, then two renderings of the separator-free row, then plain rows.
    # The box-strip read fails; both separator-free renderings agree – that is not confirmation.
    from app.ocr.regions import boxed_digits
    assert boxed_digits(_boxed_date()) is not None
    eng = FakeEngine([("", 0), ("21042026", 0.93), ("21042026", 0.9)] + [("", 0)] * 20)
    value, conf, _, issues = F._extract_date(_boxed_date(), eng)
    assert value == "2026-04-21" and conf <= 0.7 and issues
    assert F._field(value, conf, "region", "", issues)["status"] == "review"


def test_disagreeing_date_reads_force_review():
    eng = FakeEngine([("28072026", 0.95), ("28072028", 0.95)] + [("", 0)] * 20)
    value, conf, _, issues = F._extract_date(_boxed_date(), eng)
    assert value == "2026-07-28" and conf <= 0.5 and issues


# ---------------------------------------------------------------- branch / IFSC
def test_branch_prefers_clean_pin_line_over_misread_script():
    lines = [Line("Haat - 500014", 0.58, 0, 0, 100, 20, [("Haat", 0.58, 0, 0, 40, 20), ("-", 0.5, 45, 0, 5, 20), ("500014", 0.6, 55, 0, 60, 20)]),
             Line("Kompally - 500014", 0.93, 0, 30, 100, 20, [("Kompally", 0.95, 0, 30, 80, 20), ("-", 0.9, 85, 30, 5, 20), ("500014", 0.94, 95, 30, 60, 20)])]
    branch, _ = F._find_branch(lines, "Canara Bank")
    assert branch == "Kompally - 500014"
