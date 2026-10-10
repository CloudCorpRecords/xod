#!/bin/bash
# ==============================================================================
# XOD - Ultimate Research Assistant & TrustMRR Intelligence
# Launcher Script
# ==============================================================================

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

echo "========================================================"
echo " 🚀 Starting XOD - Ultimate Research Assistant"
echo "========================================================"

# Python environment (Python 3.10 or newer; 3.12 recommended)
if [ ! -x ".venv/bin/python" ]; then
    PY=""
    for c in python3.12 python3.13 python3.11 python3.10 python3; do
        if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then PY="$c"; break; fi
    done
    if [ -z "$PY" ]; then
        echo "❌ Xod needs Python 3.10 or newer. Get it from https://www.python.org/downloads/ (or: uv python install 3.12)"
        exit 1
    fi
    echo "⚙️  Setting up Xod's Python environment with $PY (one time, a few GB)..."
    "$PY" -m venv .venv && .venv/bin/python -m pip install --upgrade pip && .venv/bin/python -m pip install -r requirements.txt || exit 1
fi
source .venv/bin/activate

# Check LM Studio local server
if curl -s http://localhost:1234/v1/models >/dev/null 2>&1; then
    echo "✓ Local LLM Server detected at http://localhost:1234 (LM Studio is ON)"
else
    echo "⚠️  Note: LM Studio is not currently responding at http://localhost:1234."
    echo "   If using local models, please start LM Studio and load your model."
fi

echo "========================================================"
echo " 🌐 Web UI available at: http://localhost:8000"
echo "========================================================"

# Start FastAPI server with Uvicorn
exec python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
