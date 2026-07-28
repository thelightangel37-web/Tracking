#!/usr/bin/env bash
# =============================================================================
# restart.sh — Restarts kiosk services in the correct order
#
# What this script does:
#   1. Stops gesture-overlay (depends on gesture-engine)
#   2. Stops gesture-engine
#   3. Starts gesture-engine
#   4. Starts gesture-overlay
#   5. Verifies both services are running
#
# Usage:
#   chmod +x restart.sh
#   ./restart.sh
# =============================================================================

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; NC='\033[0m'
ok()   { echo -e "${GREEN}  ✔${NC}  $*"; }
fail() { echo -e "${RED}  ✘${NC}  $*"; }

echo "=========================================="
echo "  Gesture Kiosk — Service Restart"
echo "  $(date)"
echo "=========================================="

# ── Stop services (reverse order) ───────────────────────────────────────────
echo ""
echo "[1/4] Stopping services …"
systemctl stop gesture-overlay.service 2>/dev/null && ok "gesture-overlay stopped" || ok "gesture-overlay not running"
systemctl stop gesture-engine.service 2>/dev/null && ok "gesture-engine stopped" || ok "gesture-engine not running"

# ── Start gesture-engine ──────────────────────────────────────────────────────
echo ""
echo "[2/4] Starting gesture-engine …"
systemctl start gesture-engine.service && ok "gesture-engine started" || fail "gesture-engine failed to start"

# Wait for engine to be ready
sleep 3

# ── Start gesture-overlay ─────────────────────────────────────────────────────
echo ""
echo "[3/4] Starting gesture-overlay …"
systemctl start gesture-overlay.service && ok "gesture-overlay started" || fail "gesture-overlay failed to start"

# ── Verify services ───────────────────────────────────────────────────────────
echo ""
echo "[4/4] Verifying services …"
sleep 2
systemctl is-active gesture-engine.service &>/dev/null && ok "gesture-engine active" || fail "gesture-engine not active"
systemctl is-active gesture-overlay.service &>/dev/null && ok "gesture-overlay active" || fail "gesture-overlay not active"

echo ""
echo "=========================================="
echo "  Services restarted!"
echo "=========================================="