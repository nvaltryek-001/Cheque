"""
Cheque Verification System - Backend
Main Flask application for image quality assessment and model integration.
"""
from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import logging
from datetime import datetime
from config import Config
from api.routes import register_routes
from utils.validators import create_uploads_directory

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    CORS(app, resources={r"/api/*": {"origins": "*"}})

    create_uploads_directory(app.config['UPLOAD_FOLDER'])

    register_routes(app)

    @app.before_request
    def log_request():
        logger.info(f"{request.method} {request.path} - {datetime.now()}")

    @app.errorhandler(400)
    def bad_request(error):
        return jsonify({'error': 'Bad request', 'message': str(error)}), 400

    @app.errorhandler(413)
    def payload_too_large(error):
        return jsonify({'error': 'File too large', 'message': 'Maximum file size is 20MB'}), 413

    @app.errorhandler(500)
    def internal_error(error):
        logger.error(f"Internal error: {error}")
        return jsonify({'error': 'Internal server error'}), 500

    return app

if __name__ == '__main__':
    app = create_app()
    app.run(debug=True, host='0.0.0.0', port=5000)
