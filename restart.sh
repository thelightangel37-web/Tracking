#!/usr/bin/env bash
# =============================================================================
# restart.sh — Restarts gesture engine and overlay services
# =============================================================================

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; NC='\033[0m'
ok()   { echo -e "${GREEN}  ✔${NC}  $*"; }
fail() { echo -e "${RED}  ✘${NC}  $*"; }

echo "=========================================="
echo "  Gesture Kiosk — Service Restart"
echo "=========================================="

echo "[1/3] Stopping active services..."
systemctl stop gesture-overlay.service 2>/dev/null && ok "gesture-overlay stopped" || ok "gesture-overlay not running"
systemctl stop gesture-engine.service 2>/dev/null && ok "gesture-engine stopped" || ok "gesture-engine not running"

echo "[2/3] Starting gesture-engine..."
systemctl start gesture-engine.service 2>/dev/null && ok "gesture-engine started" || fail "gesture-engine failed to start"
sleep 2

echo "[3/3] Starting gesture-overlay..."
systemctl start gesture-overlay.service 2>/dev/null && ok "gesture-overlay started" || fail "gesture-overlay failed to start"

echo "=========================================="
ok "Services restart sequence finished!"
echo "=========================================="