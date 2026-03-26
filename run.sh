#!/usr/bin/env bash
set -euo pipefail

VENV=".venv"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
RELOAD="${RELOAD:-false}"

if [ ! -d "$VENV" ]; then
    echo "ERROR: .venv not found — run ./setup.sh first"
    exit 1
fi

RELOAD_FLAG=""
if [ "$RELOAD" = "true" ]; then
    RELOAD_FLAG="--reload"
fi

echo "==> Starting ZeroDaemon on http://${HOST}:${PORT}"
echo "    Docs: http://localhost:${PORT}/docs"
echo "    Reload: ${RELOAD}"
echo ""

exec "$VENV/bin/uvicorn" main:app \
    --host "$HOST" \
    --port "$PORT" \
    --log-level info \
    $RELOAD_FLAG
