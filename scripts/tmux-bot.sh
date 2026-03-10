#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-status}"
SESSION_NAME="${BOT_TMUX_SESSION:-cli_tg_bot}"
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STARTUP_WAIT_SECONDS="${BOT_STARTUP_WAIT_SECONDS:-3}"
LOG_TAIL_LINES="${BOT_LOG_TAIL_LINES:-120}"
DETACHED_RESTART_LOG="${BOT_DETACHED_RESTART_LOG:-$PROJECT_ROOT/logs/restart-detached.log}"
BOT_HEALTH_FILE="${BOT_HEALTH_FILE:-$PROJECT_ROOT/logs/bot-health.txt}"
BOT_HEALTH_STALE_SECONDS="${BOT_HEALTH_STALE_SECONDS:-90}"
BOT_START_RETRY_DELAYS_SECONDS="${BOT_START_RETRY_DELAYS_SECONDS:-3 30 180 600 1800}"
BOT_START_RETRY_JITTER_PERCENT="${BOT_START_RETRY_JITTER_PERCENT:-10}"
DEFAULT_PATH_PREFIX="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export PATH="$DEFAULT_PATH_PREFIX:${PATH:-}"

log() {
  printf '[tmux-bot] %s\n' "$*"
}

has_tmux() {
  [[ -n "${TMUX_BIN:-}" ]]
}

resolve_tmux_bin() {
  local candidate

  if [[ -n "${BOT_TMUX_BIN:-}" ]]; then
    if [[ -x "${BOT_TMUX_BIN}" ]]; then
      printf '%s\n' "${BOT_TMUX_BIN}"
      return 0
    fi
    log "BOT_TMUX_BIN is set but not executable: ${BOT_TMUX_BIN}"
  fi

  candidate="$(command -v tmux 2>/dev/null || true)"
  if [[ -n "$candidate" && -x "$candidate" ]]; then
    printf '%s\n' "$candidate"
    return 0
  fi

  for candidate in /opt/homebrew/bin/tmux /usr/local/bin/tmux; do
    if [[ -x "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done

  return 1
}

require_tmux() {
  if ! has_tmux; then
    log "tmux not found. Install it first: brew install tmux"
    log "or set BOT_TMUX_BIN=/absolute/path/to/tmux"
    exit 1
  fi
}

TMUX_BIN="$(resolve_tmux_bin || true)"

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

read_health_value() {
  local key="$1"
  local file="$2"
  python3 - "$key" "$file" <<'PY'
from pathlib import Path
import sys

key = sys.argv[1]
path = Path(sys.argv[2])
if not path.exists():
    sys.exit(0)
for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
    if "=" not in raw_line:
        continue
    current_key, value = raw_line.split("=", 1)
    if current_key == key:
        print(value)
        break
PY
}

write_shell_health_snapshot() {
  local lifecycle_state="$1"
  local updater_running="$2"
  local note="$3"
  local now_epoch
  local tmp_file
  now_epoch="$(date +%s)"
  tmp_file="${BOT_HEALTH_FILE}.tmp"

  mkdir -p "$(dirname "$BOT_HEALTH_FILE")"
  {
    printf 'pid=\n'
    printf 'lifecycle_state=%s\n' "$lifecycle_state"
    printf 'is_running=0\n'
    printf 'updater_running=%s\n' "$updater_running"
    printf 'polling_restart_requested=0\n'
    printf 'watchdog_tick_count=0\n'
    printf 'last_watchdog_epoch=%s\n' "$now_epoch"
    printf 'last_health_probe_success_epoch=\n'
    printf 'last_update_id=\n'
    printf 'last_update_epoch=\n'
    printf 'updated_epoch=%s\n' "$now_epoch"
    printf 'note=%s\n' "$note"
  } >"$tmp_file"
  mv "$tmp_file" "$BOT_HEALTH_FILE"
}

compute_retry_delay_seconds() {
  local base_delay="$1"
  local jitter_percent="$BOT_START_RETRY_JITTER_PERCENT"
  local max_delta
  local delta
  local adjusted

  if [[ ! "$jitter_percent" =~ ^[0-9]+$ ]] || [[ "$jitter_percent" -le 0 ]]; then
    printf '%s\n' "$base_delay"
    return
  fi

  max_delta=$(( base_delay * jitter_percent / 100 ))
  if [[ "$max_delta" -le 0 ]]; then
    printf '%s\n' "$base_delay"
    return
  fi

  delta=$(( RANDOM % (max_delta * 2 + 1) - max_delta ))
  adjusted=$(( base_delay + delta ))
  if [[ "$adjusted" -lt 1 ]]; then
    adjusted=1
  fi
  printf '%s\n' "$adjusted"
}

launch_bot_once() {
  local entry="./scripts/restart-bot.sh"
  local count

  cd "$PROJECT_ROOT"

  # Pre-cleanup: enforce single startup entry and remove old leftovers.
  "$TMUX_BIN" kill-session -t "$SESSION_NAME" >/dev/null 2>&1 || true
  cleanup_residual_processes

  if [[ "${BOT_DEBUG:-}" == "1" ]]; then
    entry="./scripts/restart-bot.sh --debug"
  fi
  "$TMUX_BIN" new-session -d -s "$SESSION_NAME" -c "$PROJECT_ROOT" \
    "export PATH=\"$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:\$PATH\"; $entry"

  sleep "$STARTUP_WAIT_SECONDS"

  count="$(bot_process_count)"
  if [[ "$count" -ne 1 ]]; then
    log "startup check failed: expected 1 bot process, found $count"
    if [[ "$count" -gt 0 ]]; then
      list_bot_processes
    fi
    return 1
  fi

  log "started in tmux session '$SESSION_NAME' (single instance confirmed)"
  list_bot_processes
  return 0
}

start_bot() {
  local -a retry_delays=()
  local retry_delay
  local computed_delay
  local retry_index=0

  require_tmux
  read -r -a retry_delays <<< "$BOT_START_RETRY_DELAYS_SECONDS"

  if launch_bot_once; then
    return 0
  fi

  for retry_delay in "${retry_delays[@]}"; do
    if [[ ! "$retry_delay" =~ ^[0-9]+$ ]] || [[ "$retry_delay" -lt 1 ]]; then
      log "skipping invalid startup retry delay: $retry_delay"
      continue
    fi

    retry_index=$((retry_index + 1))
    computed_delay="$(compute_retry_delay_seconds "$retry_delay")"
    log "retrying bot startup after failure (retry $retry_index/${#retry_delays[@]}, base delay: ${retry_delay}s, actual delay: ${computed_delay}s)"
    sleep "$computed_delay"

    if launch_bot_once; then
      log "startup recovered on retry $retry_index/${#retry_delays[@]}"
      return 0
    fi
  done

  write_shell_health_snapshot "unhealthy" "0" "startup_retries_exhausted"
  log "startup failed after 1 initial attempt and ${#retry_delays[@]} retries"
  exit 1
}

stop_bot() {
  if has_tmux; then
    "$TMUX_BIN" kill-session -t "$SESSION_NAME" >/dev/null 2>&1 || true
  fi
  cleanup_residual_processes
  log "stopped session '$SESSION_NAME' and cleaned residual processes"
}

status_bot() {
  local tmux_status="missing"
  if has_tmux && "$TMUX_BIN" has-session -t "$SESSION_NAME" 2>/dev/null; then
    tmux_status="running"
  fi

  local count
  count="$(bot_process_count)"
  local health_state="missing"
  local health_updater_running=""
  local health_age_seconds=""
  local health_ok=0

  if [[ -f "$BOT_HEALTH_FILE" ]]; then
    local now_epoch last_watchdog_epoch
    now_epoch="$(date +%s)"
    last_watchdog_epoch="$(read_health_value "last_watchdog_epoch" "$BOT_HEALTH_FILE")"
    health_state="$(read_health_value "lifecycle_state" "$BOT_HEALTH_FILE")"
    health_updater_running="$(read_health_value "updater_running" "$BOT_HEALTH_FILE")"
    if [[ -n "$last_watchdog_epoch" && "$last_watchdog_epoch" =~ ^[0-9]+$ ]]; then
      health_age_seconds="$(( now_epoch - last_watchdog_epoch ))"
    fi
    if [[ "$health_state" == "healthy" && "$health_updater_running" == "1" && -n "$health_age_seconds" && "$health_age_seconds" -le "$BOT_HEALTH_STALE_SECONDS" ]]; then
      health_ok=1
    fi
  fi

  log "tmux session '$SESSION_NAME': $tmux_status"
  log "bot process count: $count"
  log "health file: $BOT_HEALTH_FILE"
  log "health state: $health_state"
  log "health updater running: ${health_updater_running:-unknown}"
  log "health age seconds: ${health_age_seconds:-unknown}"
  list_bot_processes

  if [[ "$count" -ne 1 || "$health_ok" -ne 1 ]]; then
    return 1
  fi
  return 0
}

logs_bot() {
  require_tmux
  if ! "$TMUX_BIN" has-session -t "$SESSION_NAME" 2>/dev/null; then
    log "tmux session '$SESSION_NAME' not found"
    exit 1
  fi
  "$TMUX_BIN" capture-pane -t "$SESSION_NAME" -p | tail -n "$LOG_TAIL_LINES"
}

attach_bot() {
  require_tmux
  "$TMUX_BIN" attach -t "$SESSION_NAME"
}

restart_bot_detached() {
  mkdir -p "$(dirname "$DETACHED_RESTART_LOG")"
  nohup "$0" restart >"$DETACHED_RESTART_LOG" 2>&1 < /dev/null &
  local dispatcher_pid="$!"
  log "detached restart scheduled (dispatcher pid: $dispatcher_pid)"
  log "detached restart log: $DETACHED_RESTART_LOG"
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
  restart-detached)
    restart_bot_detached
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
    log "unknown action '$ACTION'. usage: $0 {start|stop|restart|restart-detached|status|logs|attach}"
    exit 1
    ;;
esac
