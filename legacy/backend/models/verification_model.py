"""
Cheque and Signature Verification Model
Integrates with the trained verification model for authenticity and signature verification.
"""
import logging
from typing import Dict, Any
import requests
from config import Config

logger = logging.getLogger(__name__)

class VerificationModel:
    """
    Wrapper for trained cheque verification and signature verification model.
    Supports both cheque authenticity and signature verification.
    """

    def __init__(self, config: Config):
        self.config = config
        self.enabled = config.ENABLE_VERIFICATION_MODEL
        self.endpoint = config.MODEL_ENDPOINTS['verification']
        self.timeout = config.MODEL_REQUEST_TIMEOUT

    def verify_cheque(
        self,
        image_path: str,
        ocr_output: Dict = None,
        vlm_output: Dict = None
    ) -> Dict[str, Any]:
        """
        Verify cheque authenticity.

        Args:
            image_path: Path to the image file
            ocr_output: Optional PaddleOCR extraction results
            vlm_output: Optional Qwen2.5-VL extraction results

        Returns:
            Dictionary with verification results, confidence, and decision
        """
        if not self.enabled:
            return {
                'status': 'UNAVAILABLE',
                'message': 'Verification model is not available',
                'confidence': None,
                'decision': None,
            }

        try:
            return self._call_verification_service(
                image_path,
                'cheque',
                ocr_output,
                vlm_output
            )

        except Exception as e:
            logger.error(f"Cheque verification error: {e}")
            return {
                'status': 'ERROR',
                'error': str(e),
                'confidence': None,
                'decision': None,
            }

    def verify_signature(
        self,
        image_path: str,
        vlm_output: Dict = None
    ) -> Dict[str, Any]:
        """
        Verify signature authenticity.

        Args:
            image_path: Path to the image file
            vlm_output: Optional Qwen2.5-VL extraction results

        Returns:
            Dictionary with verification results, confidence, and decision
        """
        if not self.enabled:
            return {
                'status': 'UNAVAILABLE',
                'message': 'Verification model is not available',
                'confidence': None,
                'decision': None,
            }

        try:
            return self._call_verification_service(
                image_path,
                'signature',
                None,
                vlm_output
            )

        except Exception as e:
            logger.error(f"Signature verification error: {e}")
            return {
                'status': 'ERROR',
                'error': str(e),
                'confidence': None,
                'decision': None,
            }

    def _call_verification_service(
        self,
        image_path: str,
        verification_type: str,
        ocr_output: Dict = None,
        vlm_output: Dict = None
    ) -> Dict[str, Any]:
        """
        Call remote verification service.

        Args:
            image_path: Path to image
            verification_type: 'cheque' or 'signature'
            ocr_output: Optional OCR results
            vlm_output: Optional VLM results

        Returns:
            Verification results from service
        """
        try:
            with open(image_path, 'rb') as f:
                files = {'image': f}
                data = {
                    'type': verification_type,
                }

                # Attach extracted text if available
                if ocr_output and 'text' in ocr_output:
                    data['ocr_text'] = ocr_output['text']
                if vlm_output and 'text' in vlm_output:
                    data['vlm_text'] = vlm_output['text']

                response = requests.post(
                    self.endpoint,
                    files=files,
                    data=data,
                    timeout=self.timeout
                )

            if response.status_code == 200:
                return response.json()
            else:
                logger.warning(f"Verification service returned {response.status_code}")
                return {
                    'status': 'SERVICE_ERROR',
                    'error': f'Service returned {response.status_code}',
                    'confidence': None,
                    'decision': None,
                }

        except requests.Timeout:
            return {
                'status': 'TIMEOUT',
                'error': 'Verification service request timed out',
                'confidence': None,
                'decision': None,
            }
        except Exception as e:
            logger.error(f"Error calling verification service: {e}")
            return {
                'status': 'ERROR',
                'error': str(e),
                'confidence': None,
                'decision': None,
            }
