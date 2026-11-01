"""
OCR Text Extraction Module
Integrates with PaddleOCR for printed text extraction.
"""
import logging
from typing import Dict, Any, Optional
import requests
from config import Config

logger = logging.getLogger(__name__)

class OCRExtractor:
    """
    Wrapper for PaddleOCR text extraction.
    Can be configured to use local PaddleOCR or remote service.
    """

    def __init__(self, config: Config):
        self.config = config
        self.enabled = config.ENABLE_PADDLE_OCR
        self.endpoint = config.MODEL_ENDPOINTS['paddle_ocr']
        self.timeout = config.MODEL_REQUEST_TIMEOUT

    def extract(self, image_path: str) -> Dict[str, Any]:
        """
        Extract printed text from cheque image.

        Args:
            image_path: Path to the image file

        Returns:
            Dictionary with extracted text, confidence, and metadata
        """
        if not self.enabled:
            return {
                'status': 'DISABLED',
                'message': 'PaddleOCR is disabled',
            }

        return self.extract_from_service(image_path)

    def extract_from_service(self, image_path: str) -> Dict[str, Any]:
        """
        Call remote PaddleOCR service (if available).

        Args:
            image_path: Path to the image file

        Returns:
            Extraction results from remote service
        """
        try:
            with open(image_path, 'rb') as f:
                files = {'image': f}
                response = requests.post(
                    self.endpoint,
                    files=files,
                    timeout=self.timeout
                )

            if response.status_code == 200:
                return response.json()
            else:
                logger.warning(f"OCR service returned {response.status_code}")
                return {
                    'status': 'SERVICE_ERROR',
                    'error': f'Service returned {response.status_code}',
                }

        except requests.Timeout:
            return {
                'status': 'TIMEOUT',
                'error': 'OCR service request timed out',
            }
        except Exception as e:
            logger.error(f"Error calling OCR service: {e}")
            return {
                'status': 'ERROR',
                'error': str(e),
            }

    def parse_cheque_fields(self, ocr_text: str) -> Dict[str, str]:
        """
        Parse OCR text to extract cheque fields.
        This is a placeholder - customize based on your cheque format.

        Fields to extract:
        - Date
        - Payee name
        - Amount (numeric and words)
        - Bank name
        - Account number
        - IFSC code
        """
        # TODO: Implement field extraction logic
        return {
            'date': None,
            'payee': None,
            'amount_numeric': None,
            'amount_words': None,
            'bank_name': None,
            'account_number': None,
            'ifsc_code': None,
        }
