#!/bin/bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

if [ -f "$DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$DIR/.env"
  set +a
fi

PYTHON="$DIR/venv/bin/python3"
if [ ! -x "$PYTHON" ]; then
  echo "ERROR: virtualenv not found. Run:" >&2
  echo "  python3 -m venv venv && venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

"$PYTHON" src/main.py >> "$DIR/hawkeye.log" 2>&1
