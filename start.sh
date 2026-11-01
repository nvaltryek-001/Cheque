#!/usr/bin/env bash
# ChequeVerify AI – Linux/macOS launcher
#   ./start.sh          build frontend if needed, serve everything on http://127.0.0.1:5000
#   ./start.sh dev      backend :5000 + Vite dev server :5173
#   ./start.sh setup    create venv + install packages
#   ./start.sh test     backend tests + frontend type-check/build
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
VENV="$ROOT/.venv"; PY="$VENV/bin/python"; PORT="${PORT:-5000}"
ensure_py() { [ -x "$PY" ] || python3 -m venv "$VENV"; "$PY" -m pip install -q --disable-pip-version-check -r "$ROOT/backend/requirements.txt"; }
ensure_node() { command -v npm >/dev/null || { echo "npm not found (install Node 18+)"; exit 1; }; [ -d "$ROOT/Frontend/node_modules" ] || (cd "$ROOT/Frontend" && npm install); }
case "${1:-run}" in
  setup) ensure_py; ensure_node; echo "Setup complete." ;;
  test)  ensure_py; ensure_node; (cd "$ROOT/backend" && "$PY" -m pytest -q); (cd "$ROOT/Frontend" && npm run build); echo "All tests passed." ;;
  dev)   ensure_py; ensure_node; (cd "$ROOT/backend" && "$PY" run.py --port "$PORT") & (cd "$ROOT/Frontend" && npm run dev) ;;
  *)     ensure_py; [ -f "$ROOT/Frontend/dist/index.html" ] || { ensure_node; (cd "$ROOT/Frontend" && npm run build); }
         echo "Open http://127.0.0.1:$PORT"; cd "$ROOT/backend" && exec "$PY" run.py --prod --port "$PORT" ;;
esac
