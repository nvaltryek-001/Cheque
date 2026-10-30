"""
Vision-Language Model (VLM) Text Extraction Module
Integrates with Qwen2.5-VL-3B for handwritten text extraction.
"""
import logging
from typing import Dict, Any
import requests
from config import Config

logger = logging.getLogger(__name__)

class VLMExtractor:
    """
    Wrapper for Qwen2.5-VL-3B handwritten text extraction.
    Can be configured to use local VLM or remote service.
    """

    def __init__(self, config: Config):
        self.config = config
        self.enabled = config.ENABLE_QWEN_VLM
        self.endpoint = config.MODEL_ENDPOINTS['qwen_vlm']
        self.timeout = config.MODEL_REQUEST_TIMEOUT

    def extract(self, image_path: str) -> Dict[str, Any]:
        """
        Extract handwritten text from cheque image.

        Args:
            image_path: Path to the image file

        Returns:
            Dictionary with extracted text, confidence, and metadata
        """
        if not self.enabled:
            return {
                'status': 'DISABLED',
                'message': 'Qwen2.5-VL is disabled',
            }

        return self.extract_from_service(image_path)

    def extract_from_service(self, image_path: str) -> Dict[str, Any]:
        """
        Call remote Qwen2.5-VL service (if available).

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
                logger.warning(f"VLM service returned {response.status_code}")
                return {
                    'status': 'SERVICE_ERROR',
                    'error': f'Service returned {response.status_code}',
                }

        except requests.Timeout:
            return {
                'status': 'TIMEOUT',
                'error': 'VLM service request timed out',
            }
        except Exception as e:
            logger.error(f"Error calling VLM service: {e}")
            return {
                'status': 'ERROR',
                'error': str(e),
            }

    def parse_handwritten_fields(self, vlm_text: str) -> Dict[str, str]:
        """
        Parse VLM output to extract handwritten cheque fields.

        Common handwritten fields:
        - Signature
        - Additional notes
        - Custom endorsements
        """
        # TODO: Implement field extraction logic specific to handwritten content
        return {
            'signature': None,
            'endorsement': None,
            'notes': None,
        }
