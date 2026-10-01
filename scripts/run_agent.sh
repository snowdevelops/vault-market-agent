#!/usr/bin/env bash
# Run one agent job inside the agent folder of the vault.
#   run_agent.sh research   deepen the knowledge base (Knowledge/)
#   run_agent.sh brief      refresh data, then write the weekly brief (Briefs/)
# Paths and secrets come from .env (gitignored). Schedule with cron.
set -euo pipefail

JOB="${1:-}"
case "$JOB" in
  brief|research) ;;
  *) echo "usage: $0 brief|research" >&2; exit 2 ;;
esac

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "$REPO_DIR/.env" ]]; then
  set -a; source "$REPO_DIR/.env"; set +a
fi
: "${VAULT_DIR:?VAULT_DIR is not set in .env}"
VAULT_DIR="${VAULT_DIR/#\~/$HOME}"
AGENT_DIR="${AGENT_DIR:-Business}"
WORK="$VAULT_DIR/$AGENT_DIR"
CLAUDE_BIN="${CLAUDE_BIN:-$HOME/.local/bin/claude}"
LOG="$WORK/.last_${JOB}.log"

if [[ ! -d "$WORK" ]]; then
  echo "Agent folder not found: $WORK (copy vault-template/agent there first)" >&2
  exit 1
fi

if [[ "$JOB" == "brief" ]]; then
  python3 "$REPO_DIR/scripts/fetch_market.py" || echo "fetch had errors; the brief will report them"
fi

# The agent starts INSIDE its folder and the settings file blocks every read
# outside it, all shell commands, and edits to the owner's files. The settings
# file lives in this repo, outside the vault, so the agent cannot change it.
failed=0
cd "$WORK"
"$CLAUDE_BIN" -p "$(cat "$REPO_DIR/prompts/$JOB.md")" \
  --settings "$REPO_DIR/config/agent-settings.json" \
  --permission-mode acceptEdits \
  --allowedTools "Read,Glob,Grep,WebSearch,WebFetch" \
  > "$LOG" 2>&1 || failed=1

# Local history only. The vault repo has no remote, so nothing leaves this machine.
if git -C "$VAULT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git -C "$VAULT_DIR" add -- "$AGENT_DIR" >/dev/null 2>&1 || true
  git -C "$VAULT_DIR" commit -qm "agent $JOB $(date +%F)" >/dev/null 2>&1 || true
fi

if [[ -n "${TELEGRAM_BOT_TOKEN:-}" ]]; then
  if [[ $failed -eq 1 ]]; then
    python3 "$REPO_DIR/scripts/notify_telegram.py" --failed "$JOB" || echo "telegram notify failed"
  else
    python3 "$REPO_DIR/scripts/notify_telegram.py" "$JOB" || echo "telegram notify failed"
  fi
fi

exit $failed
