"""
Local PaddleOCR implementation for printed cheque text extraction.
Compatible with PaddleOCR 3.x.
"""
import json
import logging
from typing import Dict, Any

import cv2

logger = logging.getLogger(__name__)


class OCRExtractorLocal:
    """PaddleOCR for printed text extraction."""

    def __init__(self, config):
        self.config = config
        self.enabled = config.ENABLE_PADDLE_OCR
        self.ocr = None

        if self.enabled:
            self._initialize_ocr()

    def _initialize_ocr(self):
        """Initialize PaddleOCR 3.x."""
        try:
            from paddleocr import PaddleOCR

            logger.info("Initializing PaddleOCR 3.x for printed text...")
            self.ocr = PaddleOCR(
                lang="en",
                enable_mkldnn=False,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
            )
            logger.info("PaddleOCR initialized successfully.")

        except Exception:
            logger.exception("PaddleOCR initialization failed.")
            self.enabled = False
            self.ocr = None

    def extract(self, image_path: str) -> Dict[str, Any]:
        """Extract printed text and confidence scores from an image."""
        if not self.enabled or self.ocr is None:
            return {
                "status": "DISABLED",
                "text": "",
                "lines": [],
                "confidence": 0.0,
            }

        try:
            logger.info("OCR: Extracting printed text from %s", image_path)

            image = cv2.imread(image_path)
            if image is None:
                return {
                    "status": "ERROR",
                    "error": "Could not read image",
                    "text": "",
                    "lines": [],
                    "confidence": 0.0,
                }

            # PaddleOCR 3.x API verified against the local cheque image.
            # Resize large cheque images to reduce CPU inference time.
            original_height, original_width = image.shape[:2]
            max_width = 1600

            if original_width > max_width:
                resized_height = round(
                    original_height * max_width / original_width
                )
                image = cv2.resize(
                    image,
                    (max_width, resized_height),
                    interpolation=cv2.INTER_AREA,
                )

            logger.info(
                "OCR input dimensions after optimization: %s",
                image.shape,
            )

            results = self.ocr.predict(image)

            lines = []

            for page in results or []:
                data = page.json if hasattr(page, "json") else page

                if callable(data):
                    data = data()

                if isinstance(data, str):
                    data = json.loads(data)

                if not isinstance(data, dict):
                    continue

                data = data.get("res", data)
                if not isinstance(data, dict):
                    continue

                texts = data.get("rec_texts", []) or []
                scores = data.get("rec_scores", []) or []

                for index, text in enumerate(texts):
                    if text is None:
                        continue

                    text = str(text).strip()
                    if not text:
                        continue

                    try:
                        score = float(scores[index]) if index < len(scores) else 0.0
                    except (TypeError, ValueError):
                        score = 0.0

                    lines.append({
                        "text": text,
                        "confidence": score,
                    })

            combined_text = "\n".join(line["text"] for line in lines)
            average_confidence = (
                sum(line["confidence"] for line in lines) / len(lines)
                if lines else 0.0
            )

            logger.info(
                "OCR completed: %d lines, %d characters",
                len(lines),
                len(combined_text),
            )

            return {
                "status": "OK",
                "text": combined_text,
                "lines": lines,
                "confidence": average_confidence,
            }

        except Exception as exc:
            logger.exception("OCR extraction failed.")
            return {
                "status": "ERROR",
                "error": str(exc),
                "text": "",
                "lines": [],
                "confidence": 0.0,
            }
