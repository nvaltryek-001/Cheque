"""
Image Quality Analyzer for Cheque Images
Evaluates cheque image quality before verification using pixel-level metrics.
"""
import cv2
import numpy as np
from typing import Dict, Any, Tuple
import logging

logger = logging.getLogger(__name__)

class QualityAnalyzer:
    def __init__(self, config):
        self.config = config
        self.thresholds = config.QUALITY_THRESHOLDS
        self.weights = config.QUALITY_WEIGHTS

    def analyze(self, image_path: str, ocr_output: Dict = None, vlm_output: Dict = None) -> Dict[str, Any]:
        """
        Comprehensive quality analysis of a cheque image.

        Args:
            image_path: Path to the image file
            ocr_output: Optional PaddleOCR extraction results
            vlm_output: Optional Qwen2.5-VL extraction results

        Returns:
            Dictionary with metrics, score, and recommendations
        """
        try:
            image = cv2.imread(image_path)
            if image is None:
                return self._error_response("Cannot read image file")

            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

            metrics = {
                'resolution': self._analyze_resolution(image),
                'sharpness': self._analyze_sharpness(gray),
                'brightness': self._analyze_brightness(gray),
                'contrast': self._analyze_contrast(gray),
                'skew': self._analyze_skew(image),
                'text_visibility': self._analyze_text_visibility(ocr_output, vlm_output),
            }

            # Calculate weighted score
            overall_score = self._calculate_score(metrics)
            quality_band = self._get_quality_band(overall_score)
            recommendations = self._generate_recommendations(metrics, overall_score)

            return {
                'success': True,
                'overall_score': overall_score,
                'quality_band': quality_band,
                'metrics': metrics,
                'recommendations': recommendations,
                'timestamp': self._get_timestamp(),
            }

        except Exception as e:
            logger.error(f"Quality analysis error: {e}")
            return self._error_response(str(e))

    def _analyze_resolution(self, image: np.ndarray) -> Dict[str, Any]:
        """Analyze image resolution and dimensions."""
        height, width = image.shape[:2]
        mp = (width * height) / 1_000_000

        resolution_score = 100 if mp >= 2.0 else (mp / 2.0) * 100
        resolution_score = min(100, max(0, resolution_score))

        passed = (width >= self.thresholds['MIN_WIDTH'] and
                 height >= self.thresholds['MIN_HEIGHT'] and
                 mp >= self.thresholds['MIN_RESOLUTION_MP'])

        return {
            'width': int(width),
            'height': int(height),
            'megapixels': round(mp, 2),
            'score': round(resolution_score, 2),
            'status': 'PASS' if passed else 'FAIL',
            'message': f'{width}x{height} ({mp:.2f}MP)',
        }

    def _analyze_sharpness(self, gray: np.ndarray) -> Dict[str, Any]:
        """Analyze image sharpness using Laplacian variance."""
        laplacian = cv2.Laplacian(gray, cv2.CV_64F)
        variance = laplacian.var()

        # Normalize to 0-100 scale (typical range 50-500)
        sharpness_score = min(100, (variance / 500) * 100)

        passed = variance >= self.thresholds['SHARPNESS_THRESHOLD']

        return {
            'laplacian_variance': round(variance, 2),
            'score': round(sharpness_score, 2),
            'status': 'PASS' if passed else 'FAIL',
            'message': 'Good focus' if passed else 'Image appears blurry',
        }

    def _analyze_brightness(self, gray: np.ndarray) -> Dict[str, Any]:
        """Analyze image brightness and illumination."""
        mean_brightness = gray.mean()

        # Calculate score based on distance from optimal range
        optimal_min = 100
        optimal_max = 150

        if optimal_min <= mean_brightness <= optimal_max:
            brightness_score = 100
        else:
            distance = min(
                abs(mean_brightness - optimal_min),
                abs(mean_brightness - optimal_max)
            )
            brightness_score = max(0, 100 - (distance / 100) * 50)

        threshold_min = self.thresholds['MIN_BRIGHTNESS']
        threshold_max = self.thresholds['MAX_BRIGHTNESS']
        passed = threshold_min <= mean_brightness <= threshold_max

        if mean_brightness < threshold_min:
            message = 'Image too dark'
        elif mean_brightness > threshold_max:
            message = 'Image too bright'
        else:
            message = 'Good illumination'

        return {
            'mean_brightness': round(mean_brightness, 2),
            'score': round(brightness_score, 2),
            'status': 'PASS' if passed else 'FAIL',
            'message': message,
        }

    def _analyze_contrast(self, gray: np.ndarray) -> Dict[str, Any]:
        """Analyze image contrast."""
        std_dev = gray.std()

        # Normalize to 0-100 scale (typical range 0-100)
        contrast_score = min(100, std_dev)

        passed = std_dev >= self.thresholds['MIN_CONTRAST']

        return {
            'standard_deviation': round(std_dev, 2),
            'score': round(contrast_score, 2),
            'status': 'PASS' if passed else 'FAIL',
            'message': 'Good contrast' if passed else 'Low contrast - text may be hard to read',
        }

    def _analyze_skew(self, image: np.ndarray) -> Dict[str, Any]:
        """Analyze image skew and rotation."""
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 50, 150)
        lines = cv2.HoughLinesP(edges, 1, np.pi/180, 50, minLineLength=50, maxLineGap=10)

        if lines is None or len(lines) == 0:
            return {
                'angle': 0,
                'score': 100,
                'status': 'PASS',
                'message': 'No significant skew detected',
            }

        # Calculate angles from detected lines
        angles = []
        for x1, y1, x2, y2 in np.asarray(lines).reshape(-1, 4):
            if x2 - x1 != 0:
                angle = np.arctan((y2 - y1) / (x2 - x1)) * 180 / np.pi
                angles.append(angle)

        if angles:
            median_angle = np.median(angles)
        else:
            median_angle = 0

        # Normalize angle to [-90, 90]
        if abs(median_angle) > 90:
            median_angle = median_angle - 180 if median_angle > 0 else median_angle + 180

        skew_score = max(0, 100 - abs(median_angle) * 5)
        passed = abs(median_angle) <= self.thresholds['SKEW_TOLERANCE_DEGREES']

        return {
            'angle_degrees': round(median_angle, 2),
            'score': round(skew_score, 2),
            'status': 'PASS' if passed else 'WARN',
            'message': 'Good alignment' if passed else f'Image skewed by {abs(median_angle):.1f}°',
        }

    def _analyze_text_visibility(self, ocr_output: Dict = None, vlm_output: Dict = None) -> Dict[str, Any]:
        """
        Analyze text visibility from OCR/VLM output.
        Do NOT treat word count as quality; use extraction confidence if available.
        """
        score = 100
        status = 'PASS'
        message = 'Text visibility not yet evaluated'
        extracted_text_count = 0
        confidence_avg = None

        # If OCR output available, check extraction quality
        if ocr_output:
            if isinstance(ocr_output, dict):
                if 'text' in ocr_output and ocr_output['text']:
                    extracted_text_count += len(ocr_output['text'].split())
                if 'confidence' in ocr_output:
                    confidence_avg = ocr_output['confidence']

        # If VLM output available, check handwriting extraction
        if vlm_output:
            if isinstance(vlm_output, dict):
                if 'text' in vlm_output and vlm_output['text']:
                    extracted_text_count += len(vlm_output['text'].split())
                if 'confidence' in vlm_output:
                    if confidence_avg:
                        confidence_avg = (confidence_avg + vlm_output['confidence']) / 2
                    else:
                        confidence_avg = vlm_output['confidence']

        # Only use confidence if available; don't penalize on word count alone
        if confidence_avg is not None:
            score = round(confidence_avg * 100, 2)
            status = 'PASS' if confidence_avg >= 0.7 else 'WARN' if confidence_avg >= 0.5 else 'FAIL'
            message = f'Extraction confidence: {confidence_avg*100:.1f}%'
        elif extracted_text_count > 0:
            message = f'Text extraction in progress'
        else:
            message = 'Awaiting OCR/VLM analysis'

        return {
            'extracted_words': extracted_text_count,
            'extraction_confidence': confidence_avg,
            'score': score,
            'status': status,
            'message': message,
        }

    def _calculate_score(self, metrics: Dict[str, Dict]) -> float:
        """Calculate weighted overall quality score."""
        score = 0
        for metric_name, metric_data in metrics.items():
            if metric_name in self.weights:
                weight = self.weights[metric_name]
                metric_score = metric_data.get('score', 0)
                score += weight * metric_score

        return round(score, 2)

    def _get_quality_band(self, score: float) -> Dict[str, Any]:
        """Determine quality band based on score."""
        bands = self.config.QUALITY_BANDS

        if score >= bands['GOOD']['min']:
            return {
                'level': 'GOOD',
                'label': bands['GOOD']['label'],
                'color': 'green',
            }
        elif score >= bands['ACCEPTABLE']['min']:
            return {
                'level': 'ACCEPTABLE',
                'label': bands['ACCEPTABLE']['label'],
                'color': 'yellow',
            }
        else:
            return {
                'level': 'POOR',
                'label': bands['POOR']['label'],
                'color': 'red',
            }

    def _generate_recommendations(self, metrics: Dict, score: float) -> list:
        """Generate actionable recommendations based on metrics."""
        recommendations = []

        # Resolution
        if metrics['resolution']['status'] == 'FAIL':
            recommendations.append({
                'type': 'RESOLUTION',
                'severity': 'HIGH',
                'message': 'Image resolution too low. Please use a higher resolution camera.',
            })

        # Sharpness
        if metrics['sharpness']['status'] == 'FAIL':
            recommendations.append({
                'type': 'SHARPNESS',
                'severity': 'HIGH',
                'message': 'Image is blurry. Ensure steady hands and good focus when capturing.',
            })

        # Brightness
        if metrics['brightness']['status'] == 'FAIL':
            if metrics['brightness']['mean_brightness'] < self.thresholds['MIN_BRIGHTNESS']:
                recommendations.append({
                    'type': 'BRIGHTNESS',
                    'severity': 'HIGH',
                    'message': 'Image too dark. Improve lighting conditions.',
                })
            else:
                recommendations.append({
                    'type': 'BRIGHTNESS',
                    'severity': 'HIGH',
                    'message': 'Image overexposed. Reduce brightness or adjust camera settings.',
                })

        # Contrast
        if metrics['contrast']['status'] == 'FAIL':
            recommendations.append({
                'type': 'CONTRAST',
                'severity': 'MEDIUM',
                'message': 'Low contrast detected. Use better lighting to improve text visibility.',
            })

        # Skew
        if metrics['skew']['status'] == 'WARN':
            recommendations.append({
                'type': 'SKEW',
                'severity': 'LOW',
                'message': f"Image rotated by {abs(metrics['skew']['angle_degrees'])}°. Align cheque horizontally.",
            })

        # No critical issues - ready for processing
        if not recommendations:
            recommendations.append({
                'type': 'OK',
                'severity': 'INFO',
                'message': 'Image quality is good. Ready for processing.',
            })

        return recommendations

    def _error_response(self, message: str) -> Dict[str, Any]:
        """Return error response."""
        return {
            'success': False,
            'error': message,
            'timestamp': self._get_timestamp(),
        }

    def _get_timestamp(self) -> str:
        """Get ISO format timestamp."""
        from datetime import datetime
        return datetime.utcnow().isoformat() + 'Z'
