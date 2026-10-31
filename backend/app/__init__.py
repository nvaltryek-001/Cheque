"""ChequeVerify AI – Flask application factory."""
from __future__ import annotations

import logging
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

from .api.common import ApiError, error_response
from .config import Config
from .db import close_db, init_db

__version__ = "2.0.0"


def create_app(overrides: dict | None = None) -> Flask:
    app = Flask(__name__, static_folder=None)
    app.config.from_object(Config)
    if overrides:
        app.config.update(overrides)
    for key in ("INSTANCE_DIR", "STORAGE_DIR", "MODELS_DIR"):
        Path(app.config[key]).mkdir(parents=True, exist_ok=True)
    if not app.config.get("SECRET_KEY"):
        Config.INSTANCE_DIR = app.config["INSTANCE_DIR"]
        app.config["SECRET_KEY"] = Config.resolve_secret()
    app.config["APP_VERSION"] = __version__

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    init_db(str(app.config["DATABASE"]))

    from .services.auth import bootstrap_admin
    generated = bootstrap_admin(app)
    if generated:
        msg = ("\n" + "=" * 64 + "\n  First run: created user 'admin' with password: " + generated +
               "\n  Sign in and change it under Settings > Users.\n" + "=" * 64 + "\n")
        print(msg, flush=True)
        app.config["INITIAL_ADMIN_PASSWORD"] = generated

    from .services.defaults import ensure_default_settings
    ensure_default_settings(app)

    from .api import register_blueprints
    register_blueprints(app)
    app.teardown_appcontext(close_db)

    @app.errorhandler(ApiError)
    def _api_error(err: ApiError):
        return error_response(err.status, err.code, err.message, err.details)

    @app.errorhandler(RequestEntityTooLarge)
    def _too_large(_err):
        return error_response(413, "file_too_large",
                              f"Upload exceeds {app.config['MAX_CONTENT_LENGTH'] // (1024 * 1024)} MB.")

    @app.errorhandler(HTTPException)
    def _http(err: HTTPException):
        if request.path.startswith("/api/"):
            return error_response(err.code or 500, (err.name or "error").lower().replace(" ", "_"), err.description or "")
        return err

    @app.errorhandler(Exception)
    def _unhandled(err: Exception):  # never leak stack traces / paths to clients
        app.logger.exception("Unhandled error on %s", request.path)
        return error_response(500, "internal_error", "An unexpected error occurred.")

    @app.after_request
    def _headers(resp):
        origin = request.headers.get("Origin")
        if origin and origin in app.config["CORS_ORIGINS"] and request.path.startswith("/api/"):
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Vary"] = "Origin"
            resp.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
            resp.headers["Access-Control-Expose-Headers"] = "Content-Disposition"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        if request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.before_request
    def _preflight():
        if request.method == "OPTIONS" and request.path.startswith("/api/"):
            return app.make_default_options_response()
        return None

    # --- serve the built React app (single-process deployment) ---------------
    dist = Path(app.config["FRONTEND_DIST"])

    @app.route("/", defaults={"path": ""})
    @app.route("/<path:path>")
    def _spa(path: str):
        if path.startswith("api/"):
            return error_response(404, "not_found", "Unknown API endpoint.")
        if dist.is_dir():
            target = (dist / path).resolve()
            if path and dist.resolve() in target.parents and target.is_file():
                return send_from_directory(dist, path)
            return send_from_directory(dist, "index.html")
        return jsonify({"name": "ChequeVerify AI API", "version": __version__,
                        "message": "Frontend build not found. Run `npm run build` in Frontend/ or use the Vite dev server."})

    return app
