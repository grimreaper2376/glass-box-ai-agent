#!/usr/bin/env bash
# =============================================================================
# GlassBox — one-command launcher for Linux and macOS.
#
# Run ./start.sh (or bash start.sh). It checks for Python, creates an
# isolated environment the first time it runs, installs everything GlassBox
# needs, then starts the dashboard and opens it in your browser
# automatically. Every run after the first skips straight to launching.
# =============================================================================
set -e

cd "$(dirname "${BASH_SOURCE[0]}")/backend"

echo
echo "  GlassBox"
echo "  ========"
echo

# --- 1. Python present? -----------------------------------------------------
PY=""
for cand in python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then PY="$cand"; break; fi
done
if [ -z "$PY" ]; then
    echo "  Python 3.11+ was not found."
    echo "  Debian/Ubuntu: sudo apt install python3 python3-venv python3-pip"
    echo "  Fedora:        sudo dnf install python3 python3-pip"
    echo "  macOS:         brew install python3"
    exit 1
fi

# --- 2. First run: create the environment and install dependencies --------
if [ ! -f ".venv/bin/python" ]; then
    echo "  First time setup — this takes about a minute..."
    echo
    "$PY" -m venv .venv
    # shellcheck disable=SC1091
    source .venv/bin/activate
    python -m pip install --upgrade pip --quiet
    if ! pip install -r requirements.txt; then
        echo
        echo "  Package installation failed. Check your internet connection"
        echo "  and try again, or see docs/TESTING_LINUX.md for manual steps."
        exit 1
    fi
    echo
    echo "  Setup complete."
else
    # shellcheck disable=SC1091
    source .venv/bin/activate
    # A fast, network-free check: if everything GlassBox needs is already
    # importable, skip pip entirely. Running `pip install` unconditionally on
    # every launch would still reach out to PyPI to check for newer versions
    # even when nothing changed — meaning a machine that loses internet
    # access after a successful first setup would hang on every subsequent
    # launch, which defeats the entire point of "one command, no waiting."
    if ! python -c "import fastapi, uvicorn, httpx, pydantic, yaml, websockets" >/dev/null 2>&1; then
        echo "  Updating dependencies..."
        pip install -r requirements.txt --quiet --disable-pip-version-check
    fi
fi

# --- 3. Launch. Real Binance data by default, paper trading by default,
#        browser opens itself. Nothing further to type. --------------------
echo
echo "  Starting GlassBox with live Binance market data..."
echo "  Your browser will open automatically in a moment."
echo "  Press Ctrl+C to stop."
echo
# exec replaces this script's process with the server's, so Ctrl+C (or any
# signal sent to this script) reaches the actual running server directly
# instead of potentially leaving it as an orphaned background process.
exec python -m glassbox serve
