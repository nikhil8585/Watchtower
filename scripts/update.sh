#!/bin/bash
# =============================================================================
# scripts/update.sh
# Watchtower auto-update script
#
# Usage (from project root):
#   ./scripts/update.sh
#
# What it does:
#   1. Backs up state.json (never lose seen survey IDs during update)
#   2. Pulls latest code from GitHub
#   3. Upgrades pip + installs Python dependencies
#   4. Installs/updates Playwright Chromium browser + system deps
#   5. Restarts the watchtower systemd service
#   6. Waits and verifies the service came back up cleanly
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
PROJECT_DIR="/opt/watchtower"
VENV="$PROJECT_DIR/venv/bin/activate"
STATE_FILE="$PROJECT_DIR/data/state.json"
BACKUP_DIR="$PROJECT_DIR/data/backups"
SERVICE_NAME="watchtower"
RESTART_WAIT_SECS=5

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
GREEN="\033[0;32m"
YELLOW="\033[1;33m"
RED="\033[0;31m"
BOLD="\033[1m"
RESET="\033[0m"

log_step()  { echo -e "\n${BOLD}${GREEN}>>>${RESET} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${RESET} $1"; }
log_error() { echo -e "${RED}[ERROR]${RESET} $1"; }
log_ok()    { echo -e "${GREEN}[OK]${RESET} $1"; }

timestamp() { date "+%Y-%m-%d %H:%M:%S"; }

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------
echo ""
echo -e "${BOLD}=============================================${RESET}"
echo -e "${BOLD}  Watchtower Auto Update  |  $(timestamp)${RESET}"
echo -e "${BOLD}=============================================${RESET}"

# ---------------------------------------------------------------------------
# Step 0: Verify project directory
# ---------------------------------------------------------------------------
if [ ! -d "$PROJECT_DIR" ]; then
    log_error "Project directory not found: $PROJECT_DIR"
    exit 1
fi

cd "$PROJECT_DIR"
log_ok "Working directory: $PROJECT_DIR"

# ---------------------------------------------------------------------------
# Step 1: Backup state.json
# ---------------------------------------------------------------------------
log_step "Backing up state.json..."
if [ -f "$STATE_FILE" ]; then
    mkdir -p "$BACKUP_DIR"
    BACKUP_NAME="state_$(date '+%Y%m%d_%H%M%S').json"
    cp "$STATE_FILE" "$BACKUP_DIR/$BACKUP_NAME"
    log_ok "Backup saved: data/backups/$BACKUP_NAME"
else
    log_warn "state.json not found — skipping backup (first run?)"
fi

# ---------------------------------------------------------------------------
# Step 2: Pull latest code
# ---------------------------------------------------------------------------
log_step "Pulling latest code from GitHub..."
git pull origin master
log_ok "Code updated."

# ---------------------------------------------------------------------------
# Step 3: Activate virtual environment
# ---------------------------------------------------------------------------
log_step "Activating virtual environment..."
if [ ! -f "$VENV" ]; then
    log_error "Virtual environment not found at: $VENV"
    log_error "Create it first: python3 -m venv $PROJECT_DIR/venv"
    exit 1
fi
source "$VENV"
log_ok "Virtual environment active."

# ---------------------------------------------------------------------------
# Step 4: Upgrade pip and install Python dependencies
# ---------------------------------------------------------------------------
log_step "Installing Python dependencies..."
pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
log_ok "Dependencies installed."

# ---------------------------------------------------------------------------
# Step 5: Install/update Playwright + system browser dependencies
# ---------------------------------------------------------------------------
log_step "Installing Playwright Chromium browser..."
playwright install chromium
log_ok "Chromium updated."

log_step "Installing Chromium system dependencies..."
# playwright install-deps may need sudo depending on server config
if sudo playwright install-deps chromium 2>/dev/null; then
    log_ok "System dependencies OK."
else
    log_warn "Could not install system deps (may already be present — continuing)."
fi

# ---------------------------------------------------------------------------
# Step 6: Restart the service
# ---------------------------------------------------------------------------
log_step "Restarting $SERVICE_NAME service..."
sudo systemctl restart "$SERVICE_NAME"
log_ok "Restart command issued."

# ---------------------------------------------------------------------------
# Step 7: Wait and verify service health
# ---------------------------------------------------------------------------
log_step "Waiting ${RESTART_WAIT_SECS}s for service to stabilise..."
sleep "$RESTART_WAIT_SECS"

if sudo systemctl is-active --quiet "$SERVICE_NAME"; then
    log_ok "Service is running."
else
    log_error "Service failed to start after restart!"
    echo ""
    echo "--- Last 30 journal lines ---"
    sudo journalctl -u "$SERVICE_NAME" -n 30 --no-pager
    exit 1
fi

echo ""
sudo systemctl --no-pager status "$SERVICE_NAME"

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo ""
echo -e "${BOLD}${GREEN}=============================================${RESET}"
echo -e "${BOLD}${GREEN}  Update complete  |  $(timestamp)${RESET}"
echo -e "${BOLD}${GREEN}=============================================${RESET}"
echo ""
