#!/usr/bin/env bash
# =============================================================================
# install.sh — Provisions system dependencies and python environment
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${GESTURE_APP_DIR:-$SCRIPT_DIR}"
VENV_DIR="${APP_DIR}/.venv"
SKIP_COPY="${SKIP_COPY:-false}"

MP_WHEEL_URL="https://github.com/niconielsen32/mediapipe-raspberrypi/releases/download/v0.10.14/mediapipe-0.10.14-cp311-cp311-linux_aarch64.whl"
MP_WHEEL_FILE="${SCRIPT_DIR}/mediapipe-0.10.14-cp311-cp311-linux_aarch64.whl"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}  ✔${NC}  $*"; }
warn() { echo -e "${YELLOW}  ⚠${NC}  $*"; }
die()  { echo -e "${RED}  ✘${NC}  $*" >&2; exit 1; }

echo "=========================================="
echo "  Gesture Kiosk — Installation"
echo "  Target Directory: ${APP_DIR}"
echo "=========================================="

# 1. Copy application files if APP_DIR is different from SCRIPT_DIR
if [ "${SKIP_COPY}" != "true" ] && [ "${SCRIPT_DIR}" != "${APP_DIR}" ]; then
    echo "[1/5] Copying application files..."
    mkdir -p "${APP_DIR}"
    for f in gesture_engine.py overlay.py hand_landmarker.task requirements.txt; do
        if [ -f "${SCRIPT_DIR}/${f}" ]; then
            cp -f "${SCRIPT_DIR}/${f}" "${APP_DIR}/"
            ok "Copied ${f}"
        fi
    done
else
    echo "[1/5] Using local directory files"
fi

# 2. Install OS system packages
echo "[2/5] Installing OS packages..."
sudo apt-get update -qq
sudo apt-get install -y --no-install-recommends \
    python3 python3-venv python3-dev python3-pip build-essential \
    libcamera-dev libopenblas-dev libatlas-base-dev \
    python3-pyqt5 v4l-utils curl unzip xdotool 2>/dev/null || warn "Some packages may already be installed"

# 3. Setup Python virtual environment
echo "[3/5] Setting up virtual environment..."
if [ ! -d "${VENV_DIR}" ]; then
    python3 -m venv --system-site-packages "${VENV_DIR}"
fi
source "${VENV_DIR}/bin/activate"
pip install --upgrade pip wheel setuptools --quiet
ok "Virtual environment active at ${VENV_DIR}"

# 4. Install Python dependencies
echo "[4/5] Installing Python packages..."
if [ -f "${APP_DIR}/requirements.txt" ]; then
    pip install -r "${APP_DIR}/requirements.txt" --quiet || {
        warn "PyPI wheel install fallback - downloading aarch64 wheel..."
        [ ! -f "${MP_WHEEL_FILE}" ] && curl -fsSL "${MP_WHEEL_URL}" -o "${MP_WHEEL_FILE}" || true
        [ -f "${MP_WHEEL_FILE}" ] && pip install "${MP_WHEEL_FILE}"
        pip install "opencv-python-headless>=4.9.0" "websockets>=12.0" "pynput>=1.7.0" "qasync>=0.27.0" --quiet
    }
fi
ok "Dependencies installed"

echo "=========================================="
ok "Installation complete!"
echo "=========================================="