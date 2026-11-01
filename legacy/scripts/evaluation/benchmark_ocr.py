import time
import cv2
from pathlib import Path
from paddleocr import PaddleOCR

path = Path(r"dataset/images/axis_syn_0013.png")
image = cv2.imread(str(path))

if image is None:
    raise RuntimeError(f"Cannot read image: {path}")

print("Image dimensions:", image.shape)
print("Initializing OCR model...")
start = time.perf_counter()

ocr = PaddleOCR(
    lang="en",
    enable_mkldnn=False,
    use_doc_orientation_classify=False,
    use_doc_unwarping=False,
    use_textline_orientation=False,
)

print(f"Model initialization: {time.perf_counter()-start:.1f}s")

for i in range(1, 3):
    start = time.perf_counter()
    result = ocr.predict(image)
    elapsed = time.perf_counter() - start

    texts = []
    if result:
        data = result[0]
        if hasattr(data, "json"):
            data = data.json
            if callable(data):
                data = data()
        if isinstance(data, dict):
            data = data.get("res", data)
            if isinstance(data, dict):
                texts = data.get("rec_texts", []) or []

    print(f"Prediction {i}: {elapsed:.1f}s")
    print("Recognized entries:", len(texts))
    print("Sample:", texts[:10])
