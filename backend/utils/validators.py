"""Validation utilities for file uploads and image processing."""
import os
import logging
from werkzeug.utils import secure_filename

logger = logging.getLogger(__name__)

def create_uploads_directory(upload_folder):
    """Create uploads directory if it doesn't exist."""
    if not os.path.exists(upload_folder):
        os.makedirs(upload_folder)
        logger.info(f"Created uploads directory: {upload_folder}")

def validate_file_extension(filename, allowed_extensions):
    """
    Validate file extension.

    Args:
        filename: Name of the file
        allowed_extensions: Set of allowed extensions (without dots)

    Returns:
        True if valid, False otherwise
    """
    if '.' not in filename:
        return False

    ext = filename.rsplit('.', 1)[1].lower()
    return ext in allowed_extensions

def validate_file_size(file_size_bytes, max_size_bytes):
    """
    Validate file size.

    Args:
        file_size_bytes: Size of the file in bytes
        max_size_bytes: Maximum allowed size in bytes

    Returns:
        True if valid, False otherwise
    """
    return file_size_bytes <= max_size_bytes

def get_file_size_mb(file_size_bytes):
    """Convert bytes to MB."""
    return round(file_size_bytes / (1024 * 1024), 2)

def format_error_message(error_type, details=None):
    """Format error message for API response."""
    messages = {
        'NO_FILE': 'No file provided in the request',
        'INVALID_EXTENSION': 'File format not supported. Allowed formats: JPG, PNG, GIF, BMP, WEBP',
        'FILE_TOO_LARGE': f'File too large. Maximum size: 20MB',
        'FILE_READ_ERROR': 'Could not read the file. Please ensure the file is valid',
        'MISSING_FIELD': 'Missing required field in request',
        'INVALID_JSON': 'Invalid JSON in request',
    }

    message = messages.get(error_type, 'Unknown error')
    if details:
        message += f': {details}'

    return message

def secure_save_filename(filename):
    """
    Generate a secure filename for saving.
    Uses werkzeug's secure_filename and adds timestamp prefix.
    """
    from datetime import datetime
    timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S_')
    safe_name = secure_filename(filename)

    if not safe_name:
        safe_name = 'upload'

    return timestamp + safe_name
