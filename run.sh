#!/usr/bin/env bash
set -euo pipefail

VENV=".venv"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8222}"
RELOAD="${RELOAD:-false}"

if [ ! -d "$VENV" ]; then
    echo "ERROR: .venv not found — run ./setup.sh first"
    exit 1
fi

RELOAD_FLAG=""
if [ "$RELOAD" = "true" ]; then
    RELOAD_FLAG="--reload"
fi

EXISTING_PID=$(lsof -ti tcp:"$PORT" 2>/dev/null || true)
if [ -n "$EXISTING_PID" ]; then
    echo "==> Killing existing process on port ${PORT} (PID: ${EXISTING_PID})"
    kill "$EXISTING_PID"
    sleep 1
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
