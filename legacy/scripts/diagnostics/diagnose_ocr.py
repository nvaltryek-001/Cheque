import cv2
import traceback
from pathlib import Path

image_path = Path(r"dataset/images/axis_syn_0021.png")
print("Image exists:", image_path.exists())
print("Image size:", image_path.stat().st_size if image_path.exists() else 0)

image = cv2.imread(str(image_path))
print("OpenCV image:", None if image is None else image.shape)

try:
    from paddleocr import PaddleOCR

    print("Initializing PaddleOCR...")
    ocr = PaddleOCR(
        lang="en",
        enable_mkldnn=False,
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
    )
    print("Initialization OK. Running prediction...")
    result = ocr.predict(image)
    print("Prediction returned:", type(result).__name__)
    print("Result preview:", repr(result)[:5000])

except Exception as exc:
    print("OCR FAILED:", repr(exc))
    traceback.print_exc()
