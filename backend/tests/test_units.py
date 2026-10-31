"""Unit tests: preprocessing, comparison edge cases, amounts, MICR, validation."""
import math

import cv2
import numpy as np
import pytest

from conftest import synthetic_signature
from app.ocr import amounts
from app.ocr.fields import _parse_figures_tokens, _validate, _field, FIELD_ORDER
from app.signature import compare
from app.signature.compare import _ratio_matches, decide, features
from app.signature.preprocess import CANVAS_H, CANVAS_W, SignatureError, preprocess


# --------------------------------------------------------------- preprocess
def test_preprocess_output_shape_and_quality():
    p = preprocess(synthetic_signature())
    assert p.gray.shape == (CANVAS_H, CANVAS_W) and p.mask.shape == (CANVAS_H, CANVAS_W)
    assert p.mask.any() and p.quality["acceptable"]
    assert p.gray[0, 0] == 255  # background normalised to white


def test_preprocess_preserves_aspect_ratio():
    wide = preprocess(synthetic_signature(w=600, h=80))
    ys, xs = np.nonzero(wide.mask)
    assert (xs.max() - xs.min()) > 3 * (ys.max() - ys.min())


def test_preprocess_empty_image():
    with pytest.raises(SignatureError):
        preprocess(np.full((80, 240, 3), 250, np.uint8))


def test_preprocess_tiny_and_invalid():
    with pytest.raises(SignatureError):
        preprocess(np.zeros((5, 5, 3), np.uint8))
    with pytest.raises(SignatureError):
        preprocess(np.zeros((0, 0), np.uint8))


def test_preprocess_grayscale_and_rgba_inputs():
    img = synthetic_signature()
    preprocess(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
    rgba = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
    preprocess(rgba)


def test_preprocess_uneven_background():
    img = synthetic_signature().astype(np.float32)
    grad = np.linspace(0.45, 1.0, img.shape[1])[None, :, None]
    p = preprocess((img * grad).astype(np.uint8))
    assert p.mask.mean() / 255 < 0.3


# ------------------------------------------------------------- comparison
def test_ratio_matches_handles_empty_and_short_lists():
    assert _ratio_matches(None, None, cv2.NORM_HAMMING) == 0
    d = np.random.default_rng(0).integers(0, 255, (5, 32), dtype=np.uint8)
    assert _ratio_matches(d, np.zeros((0, 32), np.uint8), cv2.NORM_HAMMING) == 0
    assert _ratio_matches(d, d[:1], cv2.NORM_HAMMING) >= 1  # k=1 path


def test_features_in_range_and_identity_high():
    a = preprocess(synthetic_signature(1))
    f, dbg = features(a, a)
    for k, v in f.items():
        assert math.isnan(v) or 0 <= v <= 1, k
    assert f["ssim"] > 0.99 and f["chamfer"] > 0.99


def test_missing_keypoints_do_not_crash():
    from app.signature.preprocess import Processed
    blank = Processed(np.full((CANVAS_H, CANVAS_W), 255, np.uint8), np.zeros((CANVAS_H, CANVAS_W), np.uint8),
                      (0, 0, 10, 10), {"acceptable": True})
    b = preprocess(synthetic_signature(2))
    f, dbg = features(blank, b)
    assert f["orb"] == 0.0 and "too few" in dbg["orb"].get("note", "")
    assert f["chamfer"] == 0.0 and f["density"] == 0.0


def test_decide_without_references_is_review():
    q = preprocess(synthetic_signature())
    res = decide(q, [], None)
    assert res["decision"] == "REVIEW" and res["score"] is None


def test_decide_identical_vs_different_with_baseline():
    a = preprocess(synthetic_signature(1))
    b = preprocess(synthetic_signature(50))
    same = decide(a, [a], None)
    diff = decide(b, [a], None)
    assert same["score"] > diff["score"]
    assert same["decision"] in ("MATCH", "REVIEW")


def test_decide_uses_best_of_multiple_specimens():
    a = preprocess(synthetic_signature(1))
    b = preprocess(synthetic_signature(60))
    res = decide(a, [b, a], None)
    assert res["best_index"] == 1 and len(res["per_specimen"]) == 2


def test_fusion_model_monotone_and_thresholds(tmp_path):
    m = compare.FusionModel({k: 1.0 for k in compare.FEATURES}, 0.0, {k: 0.5 for k in compare.FEATURES},
                            {k: 0.2 for k in compare.FEATURES}, {"match": 0.8, "mismatch": 0.3}, {"version": "t"})
    lo = m.score({k: 0.2 for k in compare.FEATURES})
    hi = m.score({k: 0.9 for k in compare.FEATURES})
    assert hi > lo
    nan = m.score({**{k: 0.9 for k in compare.FEATURES}, "sift": float("nan")})
    assert 0 <= nan <= 1
    m.save(tmp_path / "f.json")
    assert compare.FusionModel.load(tmp_path / "f.json").thresholds["match"] == 0.8
    assert compare.FusionModel.load(tmp_path / "missing.json") is None


# ---------------------------------------------------------------- amounts
@pytest.mark.parametrize("words,value", [
    ("Twenty Three Lakh Fifty Thousand", 2350000),
    ("One Crore Forty Lakhs", 14000000),
    ("Fifty-Five Thousand Four Hundred Ninety Only", 55490),
    ("Thirty Three Lakhs Only", 3300000),
    ("Forty Two Thousand And Twenty", 42020),
    ("Twenty-Six Lakh Twelve Thousan", 2612000),  # OCR typo tolerated
])
def test_words_to_number(words, value):
    assert amounts.words_to_number(words) == value


def test_number_words_roundtrip():
    for n in (1, 19, 105, 42020, 2350000, 14000000, 123456789):
        assert amounts.words_to_number(amounts.number_to_words(n)) == n


def test_indian_format_and_figures():
    assert amounts.format_indian(2350000) == "23,50,000"
    assert amounts.parse_figures("₹ 1,40,00,000/-") == 14000000
    assert amounts.parse_figures("") is None


def test_rupee_symbol_misread_is_corrected_by_words():
    assert _parse_figures_tokens("23,85,000/-", 385000)[0] == 385000
    assert _parse_figures_tokens("329,000/-", 329000)[0] == 329000
    assert _parse_figures_tokens("1,40,00,000/-", None)[0] == 14000000


def test_validation_checks():
    fields = {k: _field() for k in FIELD_ORDER}
    fields["ifsc_code"] = _field("UTIB0000426", 0.9)
    fields["bank_name"] = _field("Axis Bank", 0.9)
    fields["amount_figures"] = _field("23,50,000/-", 0.9)
    fields["amount_words"] = _field("Twenty Three Lakh Fifty Thousand", 0.9)
    v = _validate(fields)
    checks = {c["check"]: c["status"] for c in v["checks"]}
    assert checks["ifsc_format"] == "pass" and checks["ifsc_bank_match"] == "pass"
    assert checks["amount_consistency"] == "pass" and checks["completeness"] == "warn"
    fields["amount_words"] = _field("Two Lakh", 0.9)
    assert {c["check"]: c["status"] for c in _validate(fields)["checks"]}["amount_consistency"] == "fail"
