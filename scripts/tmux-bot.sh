#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-status}"
SESSION_NAME="${BOT_TMUX_SESSION:-cli_tg_bot}"
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STARTUP_WAIT_SECONDS="${BOT_STARTUP_WAIT_SECONDS:-3}"
LOG_TAIL_LINES="${BOT_LOG_TAIL_LINES:-120}"

log() {
  printf '[tmux-bot] %s\n' "$*"
}

has_tmux() {
  command -v tmux >/dev/null 2>&1
}

require_tmux() {
  if ! has_tmux; then
    log "tmux not found. Install it first: brew install tmux"
    exit 1
  fi
}

list_bot_processes() {
  pgrep -af "virtualenvs/cli-tg-.*bin/(cli-tg-bot|claude-telegram-bot)|python -m src.main" || true
}

bot_process_count() {
  local entries
  entries="$(list_bot_processes)"
  if [[ -z "$entries" ]]; then
    echo 0
    return
  fi
  echo "$entries" | wc -l | tr -d ' '
}

cleanup_residual_processes() {
  pkill -f "virtualenvs/cli-tg-.*bin/(cli-tg-bot|claude-telegram-bot)" >/dev/null 2>&1 || true
  pkill -f "python -m src.main" >/dev/null 2>&1 || true
}

start_bot() {
  require_tmux

  cd "$PROJECT_ROOT"

  # Pre-cleanup: enforce single startup entry and remove old leftovers.
  tmux kill-session -t "$SESSION_NAME" >/dev/null 2>&1 || true
  cleanup_residual_processes

  local entry="./scripts/restart-bot.sh"
  if [[ "${BOT_DEBUG:-}" == "1" ]]; then
    entry="./scripts/restart-bot.sh --debug"
  fi
  tmux new-session -d -s "$SESSION_NAME" -c "$PROJECT_ROOT" \
    "export PATH=\"$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:\$PATH\"; $entry"

  sleep "$STARTUP_WAIT_SECONDS"

  local count
  count="$(bot_process_count)"
  if [[ "$count" -ne 1 ]]; then
    log "startup check failed: expected 1 bot process, found $count"
    list_bot_processes
    exit 1
  fi

  log "started in tmux session '$SESSION_NAME' (single instance confirmed)"
  list_bot_processes
}

stop_bot() {
  tmux kill-session -t "$SESSION_NAME" >/dev/null 2>&1 || true
  cleanup_residual_processes
  log "stopped session '$SESSION_NAME' and cleaned residual processes"
}

status_bot() {
  local tmux_status="missing"
  if has_tmux && tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
    tmux_status="running"
  fi

  local count
  count="$(bot_process_count)"

  log "tmux session '$SESSION_NAME': $tmux_status"
  log "bot process count: $count"
  list_bot_processes

  if [[ "$count" -ne 1 ]]; then
    return 1
  fi
  return 0
}

logs_bot() {
  require_tmux
  if ! tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
    log "tmux session '$SESSION_NAME' not found"
    exit 1
  fi
  tmux capture-pane -t "$SESSION_NAME" -p | tail -n "$LOG_TAIL_LINES"
}

attach_bot() {
  require_tmux
  tmux attach -t "$SESSION_NAME"
}

case "$ACTION" in
  start)
    start_bot
    ;;
  stop)
    stop_bot
    ;;
  restart)
    stop_bot
    start_bot
    ;;
  status)
    status_bot
    ;;
  logs)
    logs_bot
    ;;
  attach)
    attach_bot
    ;;
  *)
    log "unknown action '$ACTION'. usage: $0 {start|stop|restart|status|logs|attach}"
    exit 1
    ;;
esac

