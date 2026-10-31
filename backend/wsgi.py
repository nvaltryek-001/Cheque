"""WSGI entry point: `waitress-serve --port=5000 wsgi:app` or `gunicorn wsgi:app`."""
from run import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).with_name(".env"))
from app import create_app  # noqa: E402

app = create_app()
