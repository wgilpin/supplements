#!/usr/bin/env bash
# Run the M2 web app locally for testing, with auto-reload on code/template edits.
# Usage: ./dev.sh [port]   (default port 8000)
set -euo pipefail
cd "$(dirname "$0")"

PORT="${1:-8000}"

echo "Supplement KG → http://127.0.0.1:${PORT}  (Ctrl-C to stop)"
exec uv run uvicorn skg.web.app:app \
  --host 127.0.0.1 \
  --port "${PORT}" \
  --reload \
  --reload-dir skg
