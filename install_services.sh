#!/usr/bin/env bash
# =============================================================================
# install_services.sh — Installs gesture kiosk systemd services
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${GESTURE_APP_DIR:-$SCRIPT_DIR}"
VENV_PYTHON="${APP_DIR}/.venv/bin/python"
SERVICE_DIR="/etc/systemd/system"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}  ✔${NC}  $*"; }
warn() { echo -e "${YELLOW}  ⚠${NC}  $*"; }
die()  { echo -e "${RED}  ✘${NC}  $*" >&2; exit 1; }

echo "=========================================="
echo "  Gesture Kiosk — Service Installer"
echo "  Target App Directory: ${APP_DIR}"
echo "=========================================="

[ "$(id -u)" -eq 0 ] || die "Run as root: sudo bash $0"

# 1. Verify files exist
echo "[1/5] Verifying files in ${APP_DIR}..."
[ -f "${APP_DIR}/gesture_engine.py" ] || die "gesture_engine.py not found in ${APP_DIR}/"
[ -f "${APP_DIR}/overlay.py"        ] || die "overlay.py not found in ${APP_DIR}/"

if [ -x "${VENV_PYTHON}" ]; then
    ok "Python venv found at ${VENV_PYTHON}"
elif command -v python3 &>/dev/null; then
    VENV_PYTHON="$(command -v python3)"
    warn "Using system python3 (${VENV_PYTHON})"
else
    die "No Python interpreter found"
fi

# 2. XDG_RUNTIME_DIR setup
echo "[2/5] Setting up XDG runtime environment..."
XDG_RT="/run/user/0"
mkdir -p "${XDG_RT}"
chmod 700 "${XDG_RT}"

cat > /etc/tmpfiles.d/xdg-runtime-root.conf << 'EOF'
d /run/user/0 0700 root root -
EOF
ok "XDG_RUNTIME_DIR=${XDG_RT} active"

# 3. Systemd service creation
echo "[3/5] Installing systemd services..."

# gesture-engine.service
cat > "${SERVICE_DIR}/gesture-engine.service" << EOF
[Unit]
Description=Gesture Engine Service
After=sysinit.target network.target
Wants=network.target

[Service]
Type=simple
User=root
WorkingDirectory=${APP_DIR}
ExecStartPre=/bin/sleep 2
ExecStart=${VENV_PYTHON} ${APP_DIR}/gesture_engine.py
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=gesture-engine
Nice=-5

[Install]
WantedBy=multi-user.target
EOF
ok "gesture-engine.service written"

# gesture-overlay.service
cat > "${SERVICE_DIR}/gesture-overlay.service" << EOF
[Unit]
Description=Gesture Overlay Service
After=graphical.target gesture-engine.service
Wants=gesture-engine.service

[Service]
Type=simple
User=root
WorkingDirectory=${APP_DIR}
ExecStartPre=/bin/sleep 3
ExecStart=${VENV_PYTHON} ${APP_DIR}/overlay.py
Environment=DISPLAY=:0
Environment=XDG_RUNTIME_DIR=/run/user/0
Environment=QT_QPA_PLATFORM=xcb
Environment=QT_XCB_NATIVE_PAINTING=1
Environment=QT_LOGGING_RULES=qt.qpa.*=false
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=gesture-overlay

[Install]
WantedBy=graphical.target
EOF
ok "gesture-overlay.service written"

# 4. Enable systemd services
echo "[4/5] Enabling services..."
systemctl daemon-reload
systemctl enable gesture-engine.service
systemctl enable gesture-overlay.service
ok "Services enabled on boot"

# 5. Start services
echo "[5/5] Starting services..."
systemctl start gesture-engine.service 2>/dev/null && ok "gesture-engine started" || warn "gesture-engine start pending"
if [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; then
    systemctl start gesture-overlay.service 2>/dev/null && ok "gesture-overlay started" || warn "gesture-overlay start pending"
fi

echo "=========================================="
ok "Service installation finished successfully!"
echo "=========================================="
