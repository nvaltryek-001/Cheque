"""WSGI entry point: `waitress-serve --port=5000 wsgi:app` or `gunicorn wsgi:app`.

Run a single process. The start-up housekeeping of `run.py` (closing jobs that a stopped server never finished,
removing temporary files) and the SIGTERM handler that makes container stops prompt are not applied here."""
import os

from run import load_dotenv
from pathlib import Path

os.umask(0o077)
load_dotenv(Path(__file__).with_name(".env"))
from app import create_app  # noqa: E402

app = create_app()
