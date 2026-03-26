#!/usr/bin/env bash
set -euo pipefail

VENV=".venv"
PYTHON="${PYTHON:-python3.14}"

# ---------------------------------------------------------------------------
# System tool dependencies — install everything
# ---------------------------------------------------------------------------

ALL_TOOLS=(nmap masscan nikto whois)

install_pkg() {
    local pkg="$1"
    if command -v apt-get &>/dev/null; then
        sudo apt-get install -y "$pkg"
    elif command -v apt &>/dev/null; then
        sudo apt install -y "$pkg"
    elif command -v dnf &>/dev/null; then
        sudo dnf install -y "$pkg"
    elif command -v yum &>/dev/null; then
        sudo yum install -y "$pkg"
    elif command -v brew &>/dev/null; then
        brew install "$pkg"
    else
        echo "    [!] No supported package manager found. Install '$pkg' manually."
        return 1
    fi
}

echo "==> Installing system tools..."
for tool in "${ALL_TOOLS[@]}"; do
    if command -v "$tool" &>/dev/null; then
        echo "    [ok] $tool already installed"
    else
        echo "    [..] installing $tool..."
        if install_pkg "$tool"; then
            echo "    [ok] $tool installed"
        else
            echo "    [!!] failed to install $tool"
        fi
    fi
done

# nuclei — requires Go, separate install
echo "    [..] checking nuclei..."
if command -v nuclei &>/dev/null; then
    echo "    [ok] nuclei already installed"
elif command -v go &>/dev/null; then
    echo "    [..] installing nuclei via go..."
    go install github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest
    echo "    [ok] nuclei installed"
else
    echo "    [!!] nuclei requires Go — install Go first, then run:"
    echo "         go install github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest"
fi

# ---------------------------------------------------------------------------
# Python virtual environment
# ---------------------------------------------------------------------------

echo ""
echo "==> Checking Python..."
$PYTHON --version

echo "==> Creating virtual environment: $VENV"
$PYTHON -m venv "$VENV"

echo "==> Upgrading pip..."
"$VENV/bin/pip" install --upgrade pip --quiet

echo "==> Installing ZeroDaemon Python dependencies..."
"$VENV/bin/pip" install -e . --quiet

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

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
