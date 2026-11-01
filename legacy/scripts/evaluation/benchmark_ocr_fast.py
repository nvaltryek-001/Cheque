import time
import cv2
from pathlib import Path
from paddleocr import PaddleOCR

path = Path(r"dataset/images/axis_syn_0013.png")
image = cv2.imread(str(path))

if image is None:
    raise RuntimeError(f"Cannot read image: {path}")

h, w = image.shape[:2]
if w > 1600:
    image = cv2.resize(
        image,
        (1600, round(h * 1600 / w)),
        interpolation=cv2.INTER_AREA
    )

print("Test dimensions:", image.shape)
print("Loading PaddleOCR...")
start = time.perf_counter()

ocr = PaddleOCR(
    lang="en",
    enable_mkldnn=False,
    use_doc_orientation_classify=False,
    use_doc_unwarping=False,
    use_textline_orientation=False,
)

print(f"Initialization: {time.perf_counter()-start:.1f}s")

start = time.perf_counter()
result = ocr.predict(image)
elapsed = time.perf_counter() - start

data = result[0] if result else {}
if hasattr(data, "json"):
    data = data.json
    if callable(data):
        data = data()
if isinstance(data, dict):
    data = data.get("res", data)

texts = data.get("rec_texts", []) if isinstance(data, dict) else []

print(f"Prediction time: {elapsed:.1f}s")
print("Recognized entries:", len(texts))
print("Recognized text:")
for text in texts:
    print("-", text)
