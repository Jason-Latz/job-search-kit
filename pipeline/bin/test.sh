#!/bin/bash
# Offline test suite. No network, runs in about a second.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
python3 -m compileall -q scripts >/dev/null
exec python3 -m unittest discover -s tests -v
