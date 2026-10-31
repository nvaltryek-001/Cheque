"""Configuration for the cheque verification system."""
import os
from datetime import timedelta

class Config:
    # Flask
    ENV = os.getenv('FLASK_ENV', 'development')
    DEBUG = ENV == 'development'

    # Upload settings
    UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
    MAX_CONTENT_LENGTH = 20 * 1024 * 1024  # 20MB
    ALLOWED_EXTENSIONS = {'jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'tif', 'tiff'}

    # Image quality thresholds (configurable)
    QUALITY_THRESHOLDS = {
        'MIN_WIDTH': 500,
        'MIN_HEIGHT': 300,
        'MIN_RESOLUTION_MP': 0.25,  # 0.25 megapixels
        'SHARPNESS_THRESHOLD': 100,  # Laplacian variance
        'MIN_BRIGHTNESS': 40,  # 0-255 scale
        'MAX_BRIGHTNESS': 215,
        'MIN_CONTRAST': 30,
        'SKEW_TOLERANCE_DEGREES': 15,
    }

    # Quality score bands (percentage)
    QUALITY_BANDS = {
        'GOOD': {'min': 80, 'label': 'Good'},
        'ACCEPTABLE': {'min': 60, 'max': 79, 'label': 'Acceptable with caution'},
        'POOR': {'max': 59, 'label': 'Poor - recapture recommended'},
    }

    # Quality metric weights (sum should be 1.0)
    QUALITY_WEIGHTS = {
        'resolution': 0.15,
        'sharpness': 0.25,
        'brightness': 0.15,
        'contrast': 0.15,
        'skew': 0.10,
        'text_visibility': 0.20,
    }

    # Model endpoints (to be configured)
    MODEL_ENDPOINTS = {
        'paddle_ocr': os.getenv('PADDLE_OCR_URL', 'http://127.0.0.1:8001/extract/text'),
        'qwen_vlm': os.getenv('QWEN_VLM_URL', 'http://127.0.0.1:8002/extract/handwriting'),
        'verification': os.getenv('VERIFICATION_MODEL_URL', 'http://localhost:8003/verify'),
    }

    # Timeout for model requests (seconds)
    MODEL_REQUEST_TIMEOUT = 1800

    # Processing
    TEMP_FILE_CLEANUP_HOURS = 24

    # Feature flags
    ENABLE_PADDLE_OCR = True
    ENABLE_QWEN_VLM = False  # Disabled due to disk space - focus on PaddleOCR
    ENABLE_VERIFICATION_MODEL = False  # Set to True when model is available
