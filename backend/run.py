"""Start ChequeVerify AI.

    python run.py                 # development server on http://127.0.0.1:5000
    python run.py --prod          # production server (waitress) on 0.0.0.0:5000
    python run.py --port 8000
Configuration is read from environment variables or backend/.env (see .env.example).
"""
from __future__ import annotations

import argparse
import os
import re
import signal
import threading
from pathlib import Path


def load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if value[:1] in ("'", '"') and value.count(value[0]) >= 2:      # quoted: the text between the quotes
            value = value[1:value.index(value[0], 1)]
        else:                                                              # unquoted: drop a trailing "  # comment"
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        os.environ.setdefault(key.strip(), value)


def install_stop_handler(grace_seconds: float | None = None) -> None:
    """Turn SIGTERM into a normal, prompt exit.

    A container's main process is PID 1, and the kernel gives PID 1 no default action for SIGTERM: without a handler
    the signal that `docker stop` and a Render redeploy send is simply ignored, the platform waits out its whole grace
    period (10 s for Docker, 30 s on Render) and then kills the server in the middle of whatever it was doing.
    With the handler the server stops serving, lets Waitress finish requests that are already running, and exits.
    A watchdog ends the process after `CV_STOP_GRACE_SECONDS` (default 20, 0 = no watchdog) even if something
    refuses to stop, so a stuck request cannot turn a clean stop into a kill."""
    if grace_seconds is None:
        grace_seconds = float(os.environ.get("CV_STOP_GRACE_SECONDS", "20"))

    def stop(signum, _frame):
        signal.signal(signum, signal.SIG_DFL)                  # a second signal is not swallowed
        print(f"received signal {signum}: shutting down", flush=True)
        if grace_seconds > 0:
            watchdog = threading.Timer(grace_seconds, os._exit, args=(0,))
            watchdog.daemon = True
            watchdog.start()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)


def main() -> None:
    os.umask(0o077)  # database, uploads and specimens readable by this user only
    load_dotenv(Path(__file__).with_name(".env"))
    install_stop_handler()
    parser = argparse.ArgumentParser(description="ChequeVerify AI server")
    parser.add_argument("--host", default=os.environ.get("CV_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("CV_PORT", "5000")))
    parser.add_argument("--prod", action="store_true", help="serve with waitress (multi-threaded, no debugger)")
    args = parser.parse_args()

    from app import create_app
    from app.services import lifecycle
    app = create_app()
    lifecycle.on_process_start(app)
    if args.prod:
        host = args.host
        try:
            from waitress import serve
        except ImportError:
            print("WARNING: waitress is not installed (pip install waitress); falling back to Flask's threaded server.")
            app.run(host=host, port=args.port, debug=False, threaded=True)
            return
        print(f"ChequeVerify AI (production) on http://{host}:{args.port}")
        serve(app, host=host, port=args.port, threads=8)
    else:
        app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
