#!/usr/bin/env bash
set -euo pipefail

REQUEST_ID="${1:-manual-$(date +%s)}"
REQUESTER_ID="${2:-unknown}"
CHAT_ID="${3:-unknown}"
MESSAGE_THREAD_ID="${4:-0}"
DELAY_SECONDS="${RESTART_DELAY_SECONDS:-1.5}"
POST_RESTART_STATUS_TIMEOUT_SECONDS="${POST_RESTART_STATUS_TIMEOUT_SECONDS:-45}"
POST_RESTART_STATUS_INTERVAL_SECONDS="${POST_RESTART_STATUS_INTERVAL_SECONDS:-2}"

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

notify_telegram() {
  local status="$1"
  local detail="${2:-}"

  if [[ ! "$CHAT_ID" =~ ^-?[0-9]+$ ]] || [[ "$CHAT_ID" == "0" ]]; then
    log_event "notify_skipped" "reason=invalid_chat_id status=$status"
    return 0
  fi

  local token="${TELEGRAM_BOT_TOKEN:-}"
  if [[ -z "$token" ]]; then
    log_event "notify_skipped" "reason=missing_bot_token status=$status"
    return 0
  fi

  local text
  if [[ "$status" == "success" ]]; then
    text="✅ Bot 重启完成
request_id: $REQUEST_ID"
  else
    text="❌ Bot 重启失败
request_id: $REQUEST_ID
$detail"
  fi

  local api_url="https://api.telegram.org/bot${token}/sendMessage"
  local -a curl_args
  curl_args=(
    --silent
    --show-error
    --fail
    -X POST
    "$api_url"
    --data-urlencode "chat_id=$CHAT_ID"
    --data-urlencode "text=$text"
  )

  if [[ "$MESSAGE_THREAD_ID" =~ ^[0-9]+$ ]] && (( MESSAGE_THREAD_ID > 1 )); then
    curl_args+=(--data-urlencode "message_thread_id=$MESSAGE_THREAD_ID")
  fi

  if curl "${curl_args[@]}" >/dev/null 2>&1; then
    log_event "notify_sent" "status=$status"
  else
    log_event "notify_failed" "status=$status"
  fi
}

wait_for_post_restart_status() {
  local deadline
  local now
  local rc=1
  deadline=$(( $(date +%s) + POST_RESTART_STATUS_TIMEOUT_SECONDS ))

  while true; do
    if "$PROJECT_ROOT/scripts/tmux-bot.sh" status >>"$LOG_FILE" 2>&1; then
      return 0
    fi
    rc=$?

    now="$(date +%s)"
    if (( now >= deadline )); then
      return "$rc"
    fi

    log_event "post_status_retry" \
      "exit_code=$rc interval_seconds=$POST_RESTART_STATUS_INTERVAL_SECONDS"
    sleep "$POST_RESTART_STATUS_INTERVAL_SECONDS"
  done
}

log_event "restart_requested" "pid=$$ ppid=$PPID delay_seconds=$DELAY_SECONDS"

if ! command -v tmux >/dev/null 2>&1; then
  log_event "restart_aborted" "reason=tmux_missing"
  notify_telegram "failed" "tmux 未安装，无法执行重启。"
  exit 1
fi

sleep "$DELAY_SECONDS"

log_event "restart_begin" "script=./scripts/tmux-bot.sh action=restart"
if "$PROJECT_ROOT/scripts/tmux-bot.sh" restart >>"$LOG_FILE" 2>&1; then
  log_event "restart_succeeded" "result=tmux-bot-restart-ok"
else
  rc=$?
  log_event "restart_failed" "exit_code=$rc"
  notify_telegram "failed" "tmux-bot restart 失败，exit_code=$rc"
  exit "$rc"
fi

if wait_for_post_restart_status; then
  log_event "post_status_ok"
  notify_telegram "success"
else
  rc=$?
  log_event "post_status_failed" "exit_code=$rc"
  notify_telegram "failed" "重启后状态检查失败，exit_code=$rc"
  exit "$rc"
fi
