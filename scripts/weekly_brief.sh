#!/usr/bin/env bash
# Weekly brief: refresh market data, let Claude Code write the analysis into the
# vault, keep a local history, and send the TL;DR to Telegram.
# All paths and secrets come from .env (gitignored). Schedule with cron.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "$REPO_DIR/.env" ]]; then
  set -a; source "$REPO_DIR/.env"; set +a
fi
: "${VAULT_DIR:?VAULT_DIR is not set in .env}"
VAULT_DIR="${VAULT_DIR/#\~/$HOME}"
CLAUDE_BIN="${CLAUDE_BIN:-$HOME/.local/bin/claude}"
BRIEFS="$VAULT_DIR/Business/Briefs"
mkdir -p "$BRIEFS"

python3 "$REPO_DIR/scripts/fetch_market.py" || echo "fetch had errors; the brief will report them"

# Run Claude Code inside the vault so it picks up the vault's CLAUDE.md.
# File edits only: no Bash, so the agent cannot run commands on this machine.
failed=0
cd "$VAULT_DIR"
"$CLAUDE_BIN" -p "$(cat "$REPO_DIR/prompts/weekly_brief.md")" \
  --permission-mode acceptEdits \
  --allowedTools "Read,Write,Edit,Glob,Grep,WebSearch,WebFetch" \
  > "$BRIEFS/.last_run.log" 2>&1 || failed=1

# Local history only. The vault has no git remote, so nothing leaves this machine.
if [[ -d "$VAULT_DIR/.git" ]]; then
  git -C "$VAULT_DIR" add Business >/dev/null 2>&1 || true
  git -C "$VAULT_DIR" commit -qm "weekly brief $(date +%F)" >/dev/null 2>&1 || true
fi

if [[ -n "${TELEGRAM_BOT_TOKEN:-}" ]]; then
  if [[ $failed -eq 1 ]]; then
    python3 "$REPO_DIR/scripts/notify_telegram.py" --failed || echo "telegram notify failed"
  else
    python3 "$REPO_DIR/scripts/notify_telegram.py" || echo "telegram notify failed"
  fi
fi

exit $failed
