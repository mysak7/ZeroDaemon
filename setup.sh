#!/usr/bin/env bash
set -euo pipefail

VENV=".venv"
PYTHON="${PYTHON:-python3.14}"

echo "==> Checking Python..."
$PYTHON --version

echo "==> Creating virtual environment: $VENV"
$PYTHON -m venv "$VENV"

echo "==> Activating venv and upgrading pip..."
"$VENV/bin/pip" install --upgrade pip --quiet

echo "==> Installing ZeroDaemon dependencies..."
"$VENV/bin/pip" install -e . --quiet

echo "==> Checking for .env file..."
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "    Created .env from .env.example — add your API keys before running."
else
    echo "    .env already exists, skipping."
fi

echo ""
echo "Done. Next steps:"
echo "  1. Edit .env and set ANTHROPIC_API_KEY / OPENAI_API_KEY as needed"
echo "  2. Run: ./run.sh"
