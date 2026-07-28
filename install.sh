#!/usr/bin/env bash
# =============================================================================
# install.sh — Provisions a fresh DietPi system for Gesture Kiosk
#
# Usage on Pi:
#   curl -sL https://raw.githubusercontent.com/.../install.sh | sudo GESTURE_APP_DIR=/home bash
#
# Or with existing files (skip copy step):
#   sudo SKIP_COPY=true ./install_services.sh
# =============================================================================

set -euo pipefail

APP_DIR="${GESTURE_APP_DIR:-/home}"
VENV_DIR=".venv"
SKIP_COPY="${SKIP_COPY:-false}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

MP_WHEEL_URL="https://github.com/niconielsen32/mediapipe-raspberrypi/releases/download/v0.10.14/mediapipe-0.10.14-cp311-cp311-linux_aarch64.whl"
MP_WHEEL_FILE="/tmp/mediapipe-0.10.14-cp311-cp311-linux_aarch64.whl"
CORAL_INDEX="https://google-coral.github.io/py-repo/"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}  ✔${NC}  $*"; }
warn() { echo -e "${YELLOW}  ⚠${NC}  $*"; }
die()  { echo -e "${RED}  ✘${NC}  $*" >&2; exit 1; }

echo "=========================================="
echo "  Gesture Kiosk — Full Installation"
echo "  $(date)"
echo "  App directory: ${APP_DIR}"
echo "=========================================="

# ── 1. Copy application files ───────────────────────────────────────────────────
if [ "${SKIP_COPY}" != "true" ]; then
    echo ""
    echo "[1/6] Copying application files …"
    for f in gesture_engine.py overlay.py depth_metrics.py hand_landmarker.task; do
        [ -f "${SCRIPT_DIR}/${f}" ] && { cp -f "${SCRIPT_DIR}/${f}" "${APP_DIR}/"; ok "Copied ${f}"; } || true
    done
else
    echo ""
    echo "[1/6] Skipping file copy (SKIP_COPY=true)"
fi
ok "Application files in place"

# ── 2. Install OS packages ──────────────────────────────────────────────────────
echo ""
echo "[2/6] Installing OS packages …"
sudo apt-get update -qq

# Try to install python3.11 - may not exist in older repos
if apt-cache search python3.11 | grep -q python3.11; then
    sudo apt-get install -y --no-install-recommends \
        python3.11 python3.11-venv python3.11-dev python3-pip build-essential \
        libcamera-dev libopenblas-dev libatlas-base-dev \
        python3-pyqt5 v4l-utils curl unzip xdotool || warn "Some packages may not be available"
else
    # Fall back to deadsnakes PPA or use python3
    warn "python3.11 not in repos, trying deadsnakes PPA..."
    sudo apt-get install -y --no-install-recommends \
        software-properties-common curl unzip xdotool libcamera-dev
    sudo add-apt-repository -y ppa:deadsnakes/ppa
    sudo apt-get update -qq
    sudo apt-get install -y --no-install-recommends \
        python3.11 python3.11-venv python3.11-dev python3-pip build-essential \
        libopenblas-dev libatlas-base-dev python3-pyqt5 v4l-utils || {
        die "Failed to install Python 3.11. Please install manually or use python3."
    }
fi
ok "OS packages installed"

# ── 3. Detect / install Python 3.11 ───────────────────────────────────────────
echo ""
echo "[3/6] Setting up Python 3.11 …"
PYTHON_BIN=""
if command -v python3.11 &>/dev/null; then
    PYTHON_BIN="$(command -v python3.11)"
    echo "      Found: $PYTHON_BIN  ($(python3.11 --version))"
else
    die "Python 3.11 not found after package installation"
fi

# ── 4. Create virtual environment ───────────────────────────────────────────────
echo ""
echo "[4/6] Creating virtual environment …"
[ -d "${APP_DIR}/${VENV_DIR}" ] && rm -rf "${APP_DIR}/${VENV_DIR}"
"${PYTHON_BIN}" -m venv "${APP_DIR}/${VENV_DIR}"
source "${APP_DIR}/${VENV_DIR}/bin/activate"
pip install --upgrade pip wheel setuptools --quiet
ok "Virtual environment created"

# ── 5. Install Python dependencies ────────────────────────────────────────────────
echo ""
echo "[5/6] Installing Python dependencies …"
[ ! -f "${MP_WHEEL_FILE}" ] && curl -fsSL "${MP_WHEEL_URL}" -o "${MP_WHEEL_FILE}" 2>/dev/null || warn "Wheel download failed, using fallback"
[ -f "${MP_WHEEL_FILE}" ] && pip install "${MP_WHEEL_FILE}" || pip install mediapipe --extra-index-url "${CORAL_INDEX}" --quiet
pip install "opencv-python-headless>=4.9.0,<4.11.0" "websockets>=12.0,<14.0" "pynput>=1.7.0" --quiet
ok "Python dependencies installed"

# ── 6. Install systemd services ──────────────────────────────────────────────────
echo ""
echo "[6/6] Installing systemd services …"
sudo bash "${SCRIPT_DIR}/install_services.sh"

echo ""
echo "=========================================="
ok "Full installation complete!"
echo "=========================================="