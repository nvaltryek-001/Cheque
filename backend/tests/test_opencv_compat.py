"""OpenCV compatibility: the reported accuracy was measured with OpenCV 5.0 (contrib) while the
deployment pins opencv-python-headless 4.x. These golden values were produced on 5.0; any OpenCV
build that changes preprocessing, similarity features, fusion scores or the MICR reader beyond the
tolerances below fails here instead of silently shifting results."""
import json
from pathlib import Path

import cv2
import pytest

from app.ocr.micr import MicrModel, read_micr
from app.signature.compare import FusionModel, features
from app.signature.preprocess import preprocess
from compat_cases import MODELS, micr_cheque, signature_pairs

GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "opencv_golden.json").read_text())
# keypoint detectors are the most version-sensitive; the rest are deterministic pixel operations
TOL = {"orb": 0.10, "sift": 0.10}
DEFAULT_TOL = 0.02


def test_required_opencv_features_present():
    assert hasattr(cv2, "SIFT_create"), "SIFT missing from this OpenCV build"
    assert hasattr(cv2, "HOGDescriptor"), (
        f"cv2.HOGDescriptor missing in OpenCV {cv2.__version__}; install opencv-python-headless<5 "
        "(see backend/requirements.txt)")


@pytest.mark.parametrize("name", sorted(GOLDEN["pairs"]))
def test_signature_features_match_golden(name):
    q, r = signature_pairs()[name]
    pq, pr = preprocess(q), preprocess(r)
    exp = GOLDEN["pairs"][name]
    assert abs(pq.mask.mean() / 255 - exp["query_ink"]) < 0.01
    assert all(abs(a - b) <= 2 for a, b in zip(pq.bbox, exp["query_bbox"]))
    f, _ = features(pq, pr)
    for k, v in exp["features"].items():
        assert abs(f[k] - v) <= TOL.get(k, DEFAULT_TOL), f"{name}.{k}: {f[k]:.4f} vs golden {v:.4f} (OpenCV {cv2.__version__})"
    fusion = FusionModel.load(MODELS / "signature_fusion.json")
    s = fusion.score(f)
    assert abs(s - exp["fusion_score"]) <= 0.05, f"{name} fusion {s:.4f} vs {exp['fusion_score']:.4f}"
    band = lambda x: "MATCH" if x >= fusion.thresholds["match"] else ("MISMATCH" if x <= fusion.thresholds["mismatch"] else "REVIEW")
    assert band(s) == band(exp["fusion_score"])


def test_micr_reader_matches_golden():
    out = read_micr(micr_cheque(), MicrModel.load(MODELS / "micr_e13b.npz"))
    assert out["raw"] == GOLDEN["micr"]
    assert out["cheque_number"] == "123456" and out["micr_code"] == "500211012"
