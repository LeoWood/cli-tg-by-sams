#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

# Ensure Poetry and common package manager paths are available
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

# Prevent nested Claude runtime detection when restarted from an AI session shell.
unset CLAUDECODE || true

# Force local proxy for Telegram connectivity in this environment and keep
# uppercase/lowercase variants consistent for different HTTP clients.
export http_proxy="http://127.0.0.1:7897"
export https_proxy="http://127.0.0.1:7897"
export HTTP_PROXY="$http_proxy"
export HTTPS_PROXY="$https_proxy"
unset ALL_PROXY all_proxy || true

# Stop existing bot processes with precise patterns to avoid broad self-kill.
pkill -f "virtualenvs/cli-tg-.*bin/(cli-tg-bot|claude-telegram-bot)" >/dev/null 2>&1 || true
pkill -f "python -m src.main" >/dev/null 2>&1 || true

BOT_ARGS=()
if [[ "${1:-}" == "--debug" || "${BOT_DEBUG:-}" == "1" ]]; then
  BOT_ARGS+=("--debug")
fi

# Start in foreground; run inside tmux/screen if you want it detached
if poetry run which cli-tg-bot >/dev/null 2>&1; then
  if [[ ${#BOT_ARGS[@]} -gt 0 ]]; then
    exec poetry run cli-tg-bot "${BOT_ARGS[@]}"
  fi
  exec poetry run cli-tg-bot
fi

if [[ ${#BOT_ARGS[@]} -gt 0 ]]; then
  exec poetry run claude-telegram-bot "${BOT_ARGS[@]}"
fi
exec poetry run claude-telegram-bot
