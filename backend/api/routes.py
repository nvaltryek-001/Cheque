"""
API Routes for Cheque Verification System
"""
from flask import request, jsonify, Response
import cv2
import os
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, wait, FIRST_COMPLETED
from datetime import datetime
from werkzeug.utils import secure_filename
from config import Config
from models.quality_analyzer import QualityAnalyzer
from models.ocr_extractor_local import OCRExtractorLocal
from models.vlm_extractor_local import VLMExtractorLocal
from models.verification_model import VerificationModel
from utils.cheque_fields import build_fields
from utils.confirmed_labels import confirmed_for
from utils.field_extraction_pipeline import FieldExtractor
from utils.validators import (
    validate_file_extension,
    validate_file_size,
    get_file_size_mb,
    format_error_message,
    secure_save_filename,
)

logger = logging.getLogger(__name__)

def register_routes(app):
    """Register all API routes."""

    # Initialize models
    quality_analyzer = QualityAnalyzer(Config)
    ocr_extractor = OCRExtractorLocal(Config)  # PaddleOCR for printed text
    vlm_extractor = VLMExtractorLocal(Config)  # Qwen2.5-VL for handwritten text
    verification_model = VerificationModel(Config)

    # Config endpoint
    @app.route('/api/config', methods=['GET'])
    def get_config():
        """Get system configuration."""
        return jsonify({
            'features': {
                'ocr_enabled': app.config['ENABLE_PADDLE_OCR'],
                'vlm_enabled': app.config['ENABLE_QWEN_VLM'],
                'verification_enabled': app.config['ENABLE_VERIFICATION_MODEL'],
            }
        }), 200

    # Health check endpoint
    @app.route('/api/health', methods=['GET'])
    def health_check():
        return jsonify({
            'status': 'healthy',
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'services': {
                'ocr': 'enabled' if app.config['ENABLE_PADDLE_OCR'] else 'disabled',
                'vlm': 'enabled' if app.config['ENABLE_QWEN_VLM'] else 'disabled',
                'verification': 'enabled' if app.config['ENABLE_VERIFICATION_MODEL'] else 'disabled',
            }
        }), 200

    # Upload and analyze image
    @app.route('/api/upload', methods=['POST'])
    def upload_image():
        """
        Upload a cheque image and perform quality analysis.

        Returns:
            - overall_score: 0-100 quality score
            - quality_band: GOOD, ACCEPTABLE, or POOR
            - metrics: Detailed quality metrics
            - recommendations: Actionable improvements
        """
        try:
            # Validate file presence
            if 'image' not in request.files:
                return jsonify({
                    'error': format_error_message('NO_FILE')
                }), 400

            file = request.files['image']

            if file.filename == '':
                return jsonify({
                    'error': format_error_message('NO_FILE')
                }), 400

            # Validate extension
            if not validate_file_extension(file.filename, app.config['ALLOWED_EXTENSIONS']):
                return jsonify({
                    'error': format_error_message('INVALID_EXTENSION')
                }), 400

            # Validate size
            file.seek(0, os.SEEK_END)
            file_size = file.tell()
            file.seek(0)

            if not validate_file_size(file_size, app.config['MAX_CONTENT_LENGTH']):
                size_mb = get_file_size_mb(file_size)
                return jsonify({
                    'error': format_error_message('FILE_TOO_LARGE'),
                    'file_size_mb': size_mb,
                    'max_size_mb': get_file_size_mb(app.config['MAX_CONTENT_LENGTH']),
                }), 413

            # Save file
            filename = secure_save_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            logger.info(f"File saved: {filepath}")

            # Analyze quality
            quality_result = quality_analyzer.analyze(filepath)

            if not quality_result.get('success', False):
                os.remove(filepath)
                return jsonify({
                    'error': quality_result.get('error', 'Quality analysis failed')
                }), 400

            return jsonify({
                'success': True,
                'upload_id': filename,
                'file_size_mb': round(file_size / (1024 * 1024), 2),
                'quality': {
                    'overall_score': quality_result['overall_score'],
                    'quality_band': quality_result['quality_band'],
                    'metrics': quality_result['metrics'],
                    'recommendations': quality_result['recommendations'],
                },
                'timestamp': datetime.utcnow().isoformat() + 'Z',
            }), 200

        except Exception as e:
            logger.error(f"Upload error: {e}")
            return jsonify({'error': 'Upload failed'}), 500

    # Process image for text extraction
    @app.route('/api/process', methods=['POST'])
    def process_image():
        """
        Process uploaded image for text extraction and verification.

        Request body:
            - upload_id: ID returned from /api/upload
            - run_ocr: boolean (default: true)
            - run_vlm: boolean (default: true)
            - run_verification: boolean (default: false)

        Returns:
            - ocr_results: PaddleOCR extraction results
            - vlm_results: Qwen2.5-VL extraction results
            - verification_results: Cheque and signature verification
            - processing_status: Overall status
        """
        try:
            data = request.get_json()

            if not data or 'upload_id' not in data:
                return jsonify({
                    'error': format_error_message('MISSING_FIELD', 'upload_id')
                }), 400

            upload_id = data['upload_id']
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], upload_id)

            if not os.path.exists(filepath):
                return jsonify({'error': 'Upload not found'}), 404

            run_ocr = data.get('run_ocr', True)
            run_vlm = data.get('run_vlm', True)
            run_verification = data.get('run_verification', False)

            import time
            start_time = time.time()

            results = {
                'upload_id': upload_id,
                'ocr': None,
                'vlm': None,
                'verification': None,
                'timestamp': datetime.utcnow().isoformat() + 'Z',
                'processing_time': 0,
            }

            # Parallel execution: OCR and VLM run simultaneously
            with ThreadPoolExecutor(max_workers=2) as pool:
                ocr_future = None
                vlm_future = None

                if run_ocr:
                    ocr_future = pool.submit(ocr_extractor.extract, filepath)
                    logger.info("Started OCR extraction (PaddleOCR on port 8001)")

                if run_vlm:
                    vlm_future = pool.submit(vlm_extractor.extract, filepath)
                    logger.info("Started VLM extraction (Qwen on port 8002)")

                # Both tasks run in parallel - wait for both to complete (max 120s)
                if ocr_future:
                    try:
                        results['ocr'] = ocr_future.result(timeout=240)
                        logger.info(f"OCR completed in {time.time() - start_time:.2f}s")
                    except Exception as e:
                        logger.exception("OCR extraction failed")
                        results['ocr'] = {'status': 'ERROR', 'error': str(e)}

                if vlm_future:
                    try:
                        results['vlm'] = vlm_future.result(timeout=120)
                        logger.info(f"VLM completed in {time.time() - start_time:.2f}s")
                    except Exception as e:
                        logger.error(f"VLM extraction failed: {e}")
                        results['vlm'] = {'status': 'ERROR', 'error': str(e)}

            results['processing_time'] = round(time.time() - start_time, 2)

            # Run verification if requested and enabled
            if run_verification:
                results['verification'] = {
                    'cheque': verification_model.verify_cheque(
                        filepath,
                        results.get('ocr'),
                        results.get('vlm')
                    ),
                    'signature': verification_model.verify_signature(
                        filepath,
                        results.get('vlm')
                    ),
                }

            # Use improved extraction pipeline
            ocr_data = results.get('ocr') or {}
            vlm_data = results.get('vlm') or {}

            logger.info(f"DEBUG: OCR result keys: {ocr_data.keys() if isinstance(ocr_data, dict) else 'not dict'}")

            # Extract OCR text
            ocr_text = ''
            if isinstance(ocr_data, dict) and ocr_data.get('status') == 'OK':
                ocr_text = ocr_data.get('text', '')

            logger.info(f"OCR text extracted: {len(ocr_text)} chars")
            if ocr_text:
                logger.info(f"OCR text sample: {ocr_text[:200]}")

            # Use field extraction pipeline
            extractor = FieldExtractor(metadata={})
            extraction_result = extractor.extract_all_fields(ocr_text, meta=None)

            raw_fields = dict(extraction_result.get('fields') or {})

            # Preserve original values and expose frontend-compatible aliases.
            raw_fields['account_holder_name'] = (
                raw_fields.get('account_holder_name')
                or raw_fields.get('payee_name')
                or raw_fields.get('payee')
            )
            raw_fields['amount_numeric'] = (
                raw_fields.get('amount_numeric')
                or raw_fields.get('amount_figures')
            )
            raw_fields['cheque_date'] = (
                raw_fields.get('cheque_date')
                or raw_fields.get('date')
            )
            raw_fields.setdefault('branch_name', None)

            results['fields'] = raw_fields
            results['extraction_confidence'] = extraction_result['confidence']
            results['has_blanks'] = extraction_result['has_blanks']
            results['ocr_text_length'] = len(ocr_text)

            avg_conf = sum(extraction_result['confidence'].values()) / 10 if extraction_result['confidence'] else 0
            logger.info(f"âœ“ Fields extracted: {sum(1 for v in extraction_result['fields'].values() if v)}/10, Avg Conf: {avg_conf:.2f}")

            # OCR_DIAGNOSTICS_EXPORT: save raw OCR and field extraction for debugging.
            try:
                diagnostic_dir = os.path.join(os.path.dirname(app.config['UPLOAD_FOLDER']), 'reports', 'ocr_diagnostics')
                os.makedirs(diagnostic_dir, exist_ok=True)

                diagnostic_data = {
                    'upload_id': upload_id,
                    'timestamp': results.get('timestamp'),
                    'processing_time': results.get('processing_time'),
                    'ocr_status': ocr_data.get('status') if isinstance(ocr_data, dict) else None,
                    'ocr_text': ocr_text,
                    'ocr_text_length': len(ocr_text),
                    'fields': raw_fields,
                    'extraction_confidence': extraction_result.get('confidence', {}),
                    'extraction_log': extraction_result.get('extraction_log', []),
                    'has_blanks': extraction_result.get('has_blanks'),
                }

                safe_id = secure_filename(str(upload_id)) or 'unknown_upload'
                diagnostic_path = os.path.join(diagnostic_dir, safe_id + '.json')
                with open(diagnostic_path, 'w', encoding='utf-8') as diagnostic_file:
                    import json
                    json.dump(diagnostic_data, diagnostic_file, ensure_ascii=False, indent=2, default=str)

            except Exception:
                logger.exception("OCR diagnostic export failed")

            return jsonify(results), 200

        except Exception as e:
            logger.exception("Processing error")
            return jsonify({'error': 'Processing failed'}), 500

    # Get processing status
    @app.route('/api/status/<upload_id>', methods=['GET'])
    def get_status(upload_id):
        """Get processing status and results for an upload."""
        try:
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], upload_id)

            if not os.path.exists(filepath):
                return jsonify({'error': 'Upload not found'}), 404

            # Return placeholder status
            return jsonify({
                'upload_id': upload_id,
                'exists': True,
                'quality_analyzed': True,
                'extraction_pending': True,
                'verification_pending': not app.config['ENABLE_VERIFICATION_MODEL'],
                'timestamp': datetime.utcnow().isoformat() + 'Z',
            }), 200

        except Exception as e:
            logger.error(f"Status check error: {e}")
            return jsonify({'error': 'Status check failed'}), 500

    # Get quality metrics for an upload
    @app.route('/api/quality/<upload_id>', methods=['GET'])
    def get_quality(upload_id):
        """Re-analyze quality metrics for an existing upload."""
        try:
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], upload_id)

            if not os.path.exists(filepath):
                return jsonify({'error': 'Upload not found'}), 404

            result = quality_analyzer.analyze(filepath)
            return jsonify(result), 200 if result.get('success') else 400

        except Exception as e:
            logger.error(f"Quality analysis error: {e}")
            return jsonify({'error': 'Quality analysis failed'}), 500

    # Browsers cannot display TIFF, so previews are served as PNG
    @app.route('/api/preview/<upload_id>', methods=['GET'])
    def get_preview(upload_id):
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(upload_id))
        if not os.path.exists(filepath):
            return jsonify({'error': 'Upload not found'}), 404

        image = cv2.imread(filepath)
        if image is None:
            return jsonify({'error': 'Cannot decode image for preview'}), 422

        ok, buffer = cv2.imencode('.png', image)
        if not ok:
            return jsonify({'error': 'Cannot encode preview'}), 500
        return Response(buffer.tobytes(), mimetype='image/png')

    logger.info("API routes registered successfully")

