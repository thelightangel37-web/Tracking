#!/usr/bin/env bash
# =============================================================================
# setup_pi.sh — Gesture Engine setup script for Raspberry Pi
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/.venv"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}  ✔${NC}  $*"; }
warn() { echo -e "${YELLOW}  ⚠${NC}  $*"; }

echo "=========================================="
echo "  Gesture Engine — Setup"
echo "=========================================="

# 1. Install OS packages
echo "[1/4] Installing system dependencies..."
sudo apt-get update -qq
sudo apt-get install -y --no-install-recommends \
    python3 python3-venv python3-dev python3-pip build-essential \
    libcamera-dev libopenblas-dev libatlas-base-dev \
    python3-pyqt5 v4l-utils curl xdotool 2>/dev/null || warn "Continuing setup..."

# 2. Virtual environment creation
echo "[2/4] Initializing Python virtual environment..."
if [ -d "${VENV_DIR}" ]; then
    rm -rf "${VENV_DIR}"
fi
python3 -m venv --system-site-packages "${VENV_DIR}"
source "${VENV_DIR}/bin/activate"
pip install --upgrade pip wheel setuptools --quiet
ok "Virtual environment created at ${VENV_DIR}"

# 3. Install requirements
echo "[3/4] Installing project requirements..."
if [ -f "${SCRIPT_DIR}/requirements.txt" ]; then
    pip install -r "${SCRIPT_DIR}/requirements.txt"
fi
ok "Python packages installed"

# 4. Verify hand_landmarker model
echo "[4/4] Verifying model asset..."
if [ ! -f "${SCRIPT_DIR}/hand_landmarker.task" ]; then
    warn "hand_landmarker.task not found locally. It will auto-download on first run."
else
    ok "hand_landmarker.task model present"
fi

echo "=========================================="
ok "Setup complete!"
echo "Run engine with: source .venv/bin/activate && python gesture_engine.py"
echo "=========================================="
