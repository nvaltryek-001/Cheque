"""Regenerate tests/fixtures/opencv_golden.json (run only when the algorithms change on purpose)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import cv2  # noqa: E402

from app.ocr.micr import MicrModel, read_micr  # noqa: E402
from app.signature.compare import FusionModel, features  # noqa: E402
from app.signature.preprocess import preprocess  # noqa: E402
from compat_cases import MODELS, micr_cheque, signature_pairs  # noqa: E402

fusion = FusionModel.load(MODELS / "signature_fusion.json")
out = {"opencv": cv2.__version__, "pairs": {}}
for name, (q, r) in signature_pairs().items():
    pq, pr = preprocess(q), preprocess(r)
    f, _ = features(pq, pr)
    out["pairs"][name] = {"features": f, "fusion_score": fusion.score(f),
                          "query_ink": float(pq.mask.mean() / 255), "query_bbox": list(pq.bbox)}
out["micr"] = read_micr(micr_cheque(), MicrModel.load(MODELS / "micr_e13b.npz"))["raw"]
Path(__file__).with_name("fixtures").joinpath("opencv_golden.json").write_text(json.dumps(out, indent=1))
print(json.dumps(out, indent=1))
