#!/usr/bin/env bash
set -euo pipefail

REQUEST_ID="${1:-manual-$(date +%s)}"
REQUESTER_ID="${2:-unknown}"
CHAT_ID="${3:-unknown}"
DELAY_SECONDS="${RESTART_DELAY_SECONDS:-1.5}"

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$PROJECT_ROOT/logs"
LOG_FILE="$LOG_DIR/restart-events.log"

mkdir -p "$LOG_DIR"

timestamp() {
  date '+%Y-%m-%d %H:%M:%S %z'
}

log_event() {
  local event="$1"
  local extra="${2:-}"
  printf '%s request_id=%s requester_id=%s chat_id=%s event=%s %s\n' \
    "$(timestamp)" \
    "$REQUEST_ID" \
    "$REQUESTER_ID" \
    "$CHAT_ID" \
    "$event" \
    "$extra" >>"$LOG_FILE"
}

log_event "restart_requested" "pid=$$ ppid=$PPID delay_seconds=$DELAY_SECONDS"

if ! command -v tmux >/dev/null 2>&1; then
  log_event "restart_aborted" "reason=tmux_missing"
  exit 1
fi

sleep "$DELAY_SECONDS"

log_event "restart_begin" "script=./scripts/tmux-bot.sh action=restart"
if "$PROJECT_ROOT/scripts/tmux-bot.sh" restart >>"$LOG_FILE" 2>&1; then
  log_event "restart_succeeded" "result=tmux-bot-restart-ok"
else
  rc=$?
  log_event "restart_failed" "exit_code=$rc"
  exit "$rc"
fi

if "$PROJECT_ROOT/scripts/tmux-bot.sh" status >>"$LOG_FILE" 2>&1; then
  log_event "post_status_ok"
else
  rc=$?
  log_event "post_status_failed" "exit_code=$rc"
  exit "$rc"
fi
