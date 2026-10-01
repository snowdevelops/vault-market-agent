#!/usr/bin/env bash
# Install and start the Telegram bot as a systemd user service.
#   scripts/install_bot_service.sh
# Fills in this repo's path and python3 in deploy/vault-agent-bot.service, copies
# it to ~/.config/systemd/user/ and enables it. Run it again after moving the repo.
# The service keeps running after logout only if lingering is enabled:
#   sudo loginctl enable-linger "$USER"
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE="vault-agent-bot.service"
TEMPLATE="$REPO_DIR/deploy/$SERVICE"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
PYTHON="$(command -v python3)" || { echo "python3 not found" >&2; exit 1; }

if [[ ! -f "$REPO_DIR/.env" ]]; then
  echo "warning: $REPO_DIR/.env does not exist; the bot needs TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID there." >&2
fi

# Plain string replacement, so paths with any characters are safe.
unit="$(<"$TEMPLATE")"
unit="${unit//@REPO_DIR@/$REPO_DIR}"
unit="${unit//@PYTHON@/$PYTHON}"

mkdir -p "$UNIT_DIR"
printf '%s\n' "$unit" > "$UNIT_DIR/$SERVICE"
echo "Installed $UNIT_DIR/$SERVICE"

systemctl --user daemon-reload
systemctl --user enable "$SERVICE"
systemctl --user restart "$SERVICE"

echo
systemctl --user --no-pager status "$SERVICE" || true
echo
echo "Follow the log:  journalctl --user -u $SERVICE -f"
echo "Stop the bot:    systemctl --user stop $SERVICE"
