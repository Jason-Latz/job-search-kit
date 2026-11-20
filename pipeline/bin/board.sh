#!/bin/bash
# Open the job tracker. Reuses an already-running server.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${1:-8765}"
if curl -sf -m 2 "http://127.0.0.1:$PORT/api/board" >/dev/null 2>&1; then
  open "http://127.0.0.1:$PORT/"
else
  cd "$REPO" && exec python3 scripts/tracker.py --port "$PORT" --open
fi
