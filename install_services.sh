#!/usr/bin/env bash
# =============================================================================
# install_services.sh — Install gesture kiosk systemd services on Raspberry Pi 5
#
# Run this once on the Pi after deploying the project to /home/.
# Must be run as root (or with sudo).
#
# What it does:
#   1. Copies gesture-engine.service  →  /etc/systemd/system/
#   2. Copies gesture-overlay.service →  /etc/systemd/system/
#   3. Reloads systemd and enables both services to start at boot
#   4. Starts both services immediately (no reboot required for first run)
#   5. Enables auto-login for root so graphical.target fires on every boot
#   6. Ensures XDG_RUNTIME_DIR exists for root (needed by XWayland)
#
# Usage:
#   sudo bash /home/install_services.sh
# =============================================================================

set -euo pipefail

APP_DIR="${GESTURE_APP_DIR:-/home}"
VENV_PYTHON="${APP_DIR}/.venv/bin/python"
SERVICE_DIR="/etc/systemd/system"

# ── Colour helpers ────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}  ✔${NC}  $*"; }
warn() { echo -e "${YELLOW}  ⚠${NC}  $*"; }
die()  { echo -e "${RED}  ✘${NC}  $*" >&2; exit 1; }

echo ""
echo "══════════════════════════════════════════════════"
echo "  Gesture Kiosk — Service Installer"
echo "  $(date)"
echo "══════════════════════════════════════════════════"

# ── Root check ────────────────────────────────────────────────────────────────
[ "$(id -u)" -eq 0 ] || die "Run as root: sudo bash $0"

# ── Verify project files exist ────────────────────────────────────────────────
echo ""
echo "[1/6] Checking project files ..."

[ -f "${APP_DIR}/gesture_engine.py" ] || die "gesture_engine.py not found in ${APP_DIR}/"
[ -f "${APP_DIR}/overlay.py"        ] || die "overlay.py not found in ${APP_DIR}/"
# Check if venv exists and use it; otherwise check if python3.11 is available
if [ -x "${VENV_PYTHON}" ]; then
    ok "venv Python found at ${VENV_PYTHON}"
elif command -v python3.11 &>/dev/null; then
    warn "venv not found, using system python3.11 — run with venv for best results"
    VENV_PYTHON="$(command -v python3.11)"
else
    die "No Python found: venv at ${VENV_PYTHON} and python3.11 not available"
fi
ok "Project files OK"

# ── Create XDG_RUNTIME_DIR for root (uid=0) ───────────────────────────────────
echo ""
echo "[2/6] Setting up XDG runtime dir for root ..."
XDG_RT="/run/user/0"
mkdir -p "${XDG_RT}"
chmod 700 "${XDG_RT}"
# Persist across reboots via tmpfiles.d
cat > /etc/tmpfiles.d/xdg-runtime-root.conf << 'EOF'
d /run/user/0 0700 root root -
EOF
ok "XDG_RUNTIME_DIR=${XDG_RT} ready"

# ── Write service files ───────────────────────────────────────────────────────
echo ""
echo "[3/6] Installing systemd service files ..."

# gesture-engine.service
cat > "${SERVICE_DIR}/gesture-engine.service" << EOF
[Unit]
Description=Gesture Engine — camera capture + WebSocket landmark server
After=sysinit.target network.target
Wants=network.target

[Service]
Type=simple
User=root
WorkingDirectory=${APP_DIR}
ExecStartPre=/bin/sleep 3
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
ok "gesture-engine.service written to ${SERVICE_DIR}"

# gesture-overlay.service
cat > "${SERVICE_DIR}/gesture-overlay.service" << EOF
[Unit]
Description=Gesture Overlay — fullscreen transparent PyQt5 hand-skeleton
After=graphical.target gesture-engine.service
Wants=gesture-engine.service

[Service]
Type=simple
User=root
WorkingDirectory=${APP_DIR}
ExecStartPre=/bin/sleep 5
ExecStart=${VENV_PYTHON} ${APP_DIR}/overlay.py
Environment=DISPLAY=:0
Environment=XDG_RUNTIME_DIR=/run/user/0
Environment=QT_QPA_PLATFORM=xcb
Environment=QT_XCB_NATIVE_PAINTING=1
Environment=QT_LOGGING_RULES=qt.qpa.*=false
Environment=WLR_NO_HARDWARE_CURSORS=1
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=gesture-overlay

[Install]
WantedBy=graphical.target
EOF
ok "gesture-overlay.service written to ${SERVICE_DIR}"

# ── Enable auto-login for root ────────────────────────────────────────────────
echo ""
echo "[4/6] Configuring auto-login for root ..."

# tty1 autologin drop-in (works for both Wayfire and X11 sessions)
AUTOLOGIN_DROP="/etc/systemd/system/getty@tty1.service.d/autologin.conf"
mkdir -p "$(dirname "${AUTOLOGIN_DROP}")"
cat > "${AUTOLOGIN_DROP}" << 'EOF'
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin root --noclear %I $TERM
EOF
ok "tty1 autologin configured for root"

# lightdm — if used as display manager (X11 desktop)
LIGHTDM_CONF="/etc/lightdm/lightdm.conf"
if [ -f "${LIGHTDM_CONF}" ]; then
    sed -i 's/^#\?autologin-user=.*/autologin-user=root/'        "${LIGHTDM_CONF}"
    sed -i 's/^#\?autologin-user-timeout=.*/autologin-user-timeout=0/' "${LIGHTDM_CONF}"
    ok "lightdm auto-login configured for root"
fi

# Ensure the display manager starts at boot
if systemctl list-unit-files display-manager.service &>/dev/null 2>&1; then
    systemctl enable display-manager.service 2>/dev/null \
        && ok "Display manager enabled at boot" \
        || warn "Could not enable display manager (may not be installed)"
fi

# ── Reload + enable + start ───────────────────────────────────────────────────
echo ""
echo "[5/6] Reloading systemd and enabling services ..."

systemctl daemon-reload
ok "systemd reloaded"

systemctl enable gesture-engine.service
ok "gesture-engine enabled (starts at multi-user.target on every boot)"

systemctl enable gesture-overlay.service
ok "gesture-overlay enabled (starts at graphical.target on every boot)"

# ── Start immediately ─────────────────────────────────────────────────────────
echo ""
echo "[6/6] Starting services now ..."

systemctl start gesture-engine.service && ok "gesture-engine started" \
    || warn "gesture-engine failed to start — check: journalctl -u gesture-engine"

# Overlay needs a compositor — only auto-start if we're already in a graphical session
if [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; then
    systemctl start gesture-overlay.service && ok "gesture-overlay started" \
        || warn "gesture-overlay failed — check: journalctl -u gesture-overlay"
else
    warn "No display detected in this shell — gesture-overlay will start on next graphical boot"
    warn "To start it now from the desktop:  systemctl start gesture-overlay"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════"
echo -e "${GREEN}  Installation complete!${NC}"
echo ""
echo "  Check status:"
echo "    systemctl status gesture-engine"
echo "    systemctl status gesture-overlay"
echo ""
echo "  Live logs:"
echo "    journalctl -u gesture-engine  -f"
echo "    journalctl -u gesture-overlay -f"
echo ""
echo "  Restart a service:"
echo "    systemctl restart gesture-engine"
echo "    systemctl restart gesture-overlay"
echo ""
echo "  Disable autostart:"
echo "    systemctl disable gesture-engine gesture-overlay"
echo "══════════════════════════════════════════════════"
