#!/usr/bin/env bash
# Run one agent job inside the agent folder of the vault.
#   run_agent.sh research   deepen the knowledge base (Knowledge/)
#   run_agent.sh brief      refresh data, then write the weekly brief (Briefs/)
#   run_agent.sh review     review each sold deal without Reviews/<deal>.md and update
#                           Knowledge/playbook.md; exits without calling claude if none
# Paths and secrets come from .env (gitignored). Schedule with cron.
#
# Only one job runs at a time: a second one waits for the lock up to 30 minutes
# (AGENT_LOCK_WAIT seconds), then gives up with exit code 75.
# AGENT_SKIP_NOTIFY=1 skips the Telegram message (the bot replies itself).
set -euo pipefail

JOB="${1:-}"
case "$JOB" in
  brief|research|review) ;;
  *) echo "usage: $0 brief|research|review" >&2; exit 2 ;;
esac

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "$REPO_DIR/.env" ]]; then
  set -a; source "$REPO_DIR/.env"; set +a
fi
: "${VAULT_DIR:?VAULT_DIR is not set in .env}"
VAULT_DIR="${VAULT_DIR/#\~/$HOME}"
AGENT_DIR="${AGENT_DIR:-Business}"
AGENT_DIR="${AGENT_DIR%/}"
WORK="$VAULT_DIR/$AGENT_DIR"
CLAUDE_BIN="${CLAUDE_BIN:-$HOME/.local/bin/claude}"
# Full event stream (follow it live with scripts/watch_agent.py) and a short log.
STREAM="$WORK/.last_${JOB}.jsonl"
LOG="$WORK/.last_${JOB}.log"

if [[ ! -d "$WORK" ]]; then
  echo "Agent folder not found: $WORK (copy vault-template/agent there first)" >&2
  exit 1
fi

# Sold deals still waiting for a review, as note paths relative to the agent folder.
PENDING=()
pending_reviews() {
  local out
  out="$(python3 "$REPO_DIR/scripts/deals.py" pending-reviews)"
  PENDING=()
  if [[ -n "$out" ]]; then mapfile -t PENDING <<< "$out"; fi
}

# The daily review run costs nothing when there is nothing to review: no lock,
# no status entry, no claude, no message.
if [[ "$JOB" == "review" ]]; then
  pending_reviews
  if (( ${#PENDING[@]} == 0 )); then
    echo "$(date '+%F %T') review: no sold deal without a review; nothing to do"
    exit 0
  fi
fi

notify() {
  if [[ -n "${TELEGRAM_BOT_TOKEN:-}" && -z "${AGENT_SKIP_NOTIFY:-}" ]]; then
    python3 "$REPO_DIR/scripts/notify_telegram.py" "$@" || echo "telegram notify failed"
  fi
}

# One job at a time. The lock and the run status live in this repo, not the vault.
STATE_DIR="$REPO_DIR/.state"
LOCK_WAIT="${AGENT_LOCK_WAIT:-1800}"
mkdir -p "$STATE_DIR"
exec 9>"$STATE_DIR/agent.lock"
if ! flock -w "$LOCK_WAIT" 9; then
  if (( LOCK_WAIT >= 60 )); then wait_text="$((LOCK_WAIT / 60)) min"; else wait_text="${LOCK_WAIT}s"; fi
  echo "$(date '+%F %T') $JOB: another agent job is still running after waiting $wait_text; giving up." >&2
  notify --busy "$JOB"
  exit 75
fi
if [[ "$JOB" == "review" ]]; then
  pending_reviews  # again: a run that held the lock may have written some meanwhile
  if (( ${#PENDING[@]} == 0 )); then
    echo "$(date '+%F %T') review: no sold deal without a review; nothing to do"
    exit 0
  fi
fi
python3 "$REPO_DIR/scripts/status.py" start "$JOB" "$$" || echo "could not record job status" >&2
trap 'python3 "$REPO_DIR/scripts/status.py" finish "$JOB" "$?" || echo "could not record job status" >&2' EXIT

if [[ "$JOB" == "brief" ]]; then
  python3 "$REPO_DIR/scripts/fetch_market.py" || echo "fetch had errors; the brief will report them"
fi

# The agent starts INSIDE its folder and the settings file blocks every read
# outside it, all shell commands, and edits to the owner's files. The settings
# file lives in this repo, outside the vault, so the agent cannot change it.
# 9>&- keeps the lock out of Claude's child processes, so it is released when
# this script exits, and env -u keeps the Telegram token out of its environment.
# A stream with no result, or one that ended in an error, also counts as failed.
run_claude() {
  local rc=0
  env -u TELEGRAM_BOT_TOKEN "$CLAUDE_BIN" -p "$1" \
    --settings "$REPO_DIR/config/agent-settings.json" \
    --permission-mode acceptEdits \
    --allowedTools "Read,Glob,Grep,WebSearch,WebFetch" \
    --output-format stream-json --verbose \
    > "$STREAM" 2>> "$LOG" 9>&- || rc=1
  # Append the outcome and final answer to the short log.
  python3 "$REPO_DIR/scripts/watch_agent.py" --summary "$STREAM" >> "$LOG" 2>&1 || rc=1
  return $rc
}

failed=0
REVIEWED=()
: > "$LOG"
cd "$WORK"
if [[ "$JOB" == "review" ]]; then
  # One run per deal; the stream file shows the deal being reviewed now.
  for note in "${PENDING[@]}"; do
    deal="$(basename "$note" .md)"
    echo "== $note" >> "$LOG"
    prompt="$(cat "$REPO_DIR/prompts/review.md")"$'\n\n'"Negócio a revisar: $note"$'\n'"Escreva a revisão em: Reviews/$deal.md"
    run_claude "$prompt" || failed=1
    if [[ -f "Reviews/$deal.md" ]]; then
      REVIEWED+=("$deal")
    else
      echo "Reviews/$deal.md was not written" >> "$LOG"
      failed=1
    fi
  done
else
  run_claude "$(cat "$REPO_DIR/prompts/$JOB.md")" || failed=1
fi

# Local history only. The vault repo has no remote, so nothing leaves this machine.
# The run logs are left out: the stream is large and changes on every run.
if git -C "$VAULT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git -C "$VAULT_DIR" add -- "$AGENT_DIR" ":(exclude)$AGENT_DIR/.last_*" >/dev/null 2>&1 || true
  git -C "$VAULT_DIR" commit -qm "agent $JOB $(date +%F)" >/dev/null 2>&1 || true
fi

if [[ $failed -eq 1 ]]; then
  notify --failed "$JOB"
elif [[ "$JOB" == "review" ]]; then
  notify review "${REVIEWED[@]}"
else
  notify "$JOB"
fi

exit $failed
