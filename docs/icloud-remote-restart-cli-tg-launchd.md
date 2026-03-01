---
created: 2026-02-28
updated: 2026-03-01
tags:
  - kb
  - ai-tools
  - cli-tg
  - launchd
  - icloud
---

# iCloud 远程重启 cli-tg（launchd 监听）

## Summary

通过在 iCloud Drive 投递命令文件（`restart-cli-tg.txt` / `stop-cli-tg.txt`），触发本机 `launchd` 调用脚本执行 `cli-tg` 重启或停止，用于远程运维 Telegram Bot。

## Details

### 目标

- 手机端：在 iCloud 的命令目录丢一个文件。
- Mac 端：按文件名自动执行
  `./scripts/tmux-bot.sh restart` 或 `./scripts/tmux-bot.sh stop`。

### 1) 创建目录

```bash
mkdir -p "$HOME/Library/Mobile Documents/iCloud~is~workflow~my~workflows/Documents/RemoteCmd"/{inbox,done,error}
mkdir -p "$HOME/bin" "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
```

### 2) 创建监听脚本

文件：`$HOME/bin/remote-cli-tg-worker.sh`

```bash
cat > "$HOME/bin/remote-cli-tg-worker.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail

# launchd 默认 PATH 不包含 Homebrew 目录，显式补齐避免找不到 tmux
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

CMD_ROOT="$HOME/Library/Mobile Documents/iCloud~is~workflow~my~workflows/Documents/RemoteCmd"
INBOX="$CMD_ROOT/inbox"
DONE="$CMD_ROOT/done"
ERROR="$CMD_ROOT/error"
PROJECT="$HOME/Projects/AIGC/cli-tg-by-sams"
LOG_FILE="$HOME/Library/Logs/cli-tg-remote-restart.log"
RETENTION_DAYS=7

mkdir -p "$INBOX" "$DONE" "$ERROR"

shopt -s nullglob
for f in "$INBOX"/*; do
  name="$(basename "$f")"
  [[ -f "$f" ]] || continue
  [[ "$name" == .* ]] && continue
  ts="$(date +%Y%m%d-%H%M%S)"

  case "$name" in
    restart-cli-tg.txt)
      if (cd "$PROJECT" && ./scripts/tmux-bot.sh restart >>"$LOG_FILE" 2>&1 && ./scripts/tmux-bot.sh status >>"$LOG_FILE" 2>&1); then
        mv "$f" "$DONE/${ts}_${name}"
      else
        mv "$f" "$ERROR/${ts}_${name}"
      fi
      ;;
    stop-cli-tg.txt)
      if (cd "$PROJECT" && ./scripts/tmux-bot.sh stop >>"$LOG_FILE" 2>&1 && ./scripts/tmux-bot.sh status >>"$LOG_FILE" 2>&1); then
        mv "$f" "$DONE/${ts}_${name}"
      else
        mv "$f" "$ERROR/${ts}_${name}"
      fi
      ;;
    *)
      mv "$f" "$ERROR/${ts}_${name}"
      ;;
  esac
done

# 自动清理历史命令文件（保留最近 7 天）
# 某些机器下 launchd 访问 iCloud 目录可能被系统权限限制，清理失败时不影响主流程
find "$DONE" "$ERROR" -type f -mtime +"$RETENTION_DAYS" -delete 2>/dev/null || true
SH

chmod +x "$HOME/bin/remote-cli-tg-worker.sh"
```

### 3) 创建 LaunchAgent

文件：`$HOME/Library/LaunchAgents/com.liuhuan.remote-cli-tg.plist`

```bash
cat > "$HOME/Library/LaunchAgents/com.liuhuan.remote-cli-tg.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.liuhuan.remote-cli-tg</string>

  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$HOME/bin/remote-cli-tg-worker.sh</string>
  </array>

  <key>WatchPaths</key>
  <array>
    <string>$HOME/Library/Mobile Documents/iCloud~is~workflow~my~workflows/Documents/RemoteCmd/inbox</string>
  </array>

  <key>StartInterval</key>
  <integer>20</integer>

  <key>RunAtLoad</key>
  <true/>

  <key>StandardOutPath</key>
  <string>$HOME/Library/Logs/remote-cli-tg.log</string>
  <key>StandardErrorPath</key>
  <string>$HOME/Library/Logs/remote-cli-tg.err.log</string>
</dict>
</plist>
PLIST
```

### 4) 加载服务

```bash
launchctl bootout gui/$(id -u) "$HOME/Library/LaunchAgents/com.liuhuan.remote-cli-tg.plist" 2>/dev/null || true
launchctl bootstrap gui/$(id -u) "$HOME/Library/LaunchAgents/com.liuhuan.remote-cli-tg.plist"
launchctl kickstart -k gui/$(id -u)/com.liuhuan.remote-cli-tg
```

### 5) 本机测试

```bash
touch "$HOME/Library/Mobile Documents/iCloud~is~workflow~my~workflows/Documents/RemoteCmd/inbox/restart-cli-tg.txt"
sleep 3
tail -n 50 "$HOME/Library/Logs/cli-tg-remote-restart.log"
```

预期：

- `inbox/restart-cli-tg.txt` 或 `inbox/stop-cli-tg.txt` 会被移动到 `done/`。
- 日志出现对应的 `tmux-bot.sh restart/stop` 与 `status` 信息。
- `done/` 和 `error/` 目录会自动清理 7 天前的历史文件。

### 6) 手机端创建快捷指令（推荐）

目标：在 iPhone 上一键投递命令文件到 iCloud `RemoteCmd/inbox`。

1. 打开 iPhone「快捷指令」App，新建一个快捷指令（例如：`重启 cli-tg`）。
2. 添加动作「文本」，内容填：`restart-cli-tg.txt`（或直接固定为文件名）。
3. 添加动作「保存文件」。
4. 在「保存文件」动作里：
   - 关闭 `询问保存位置`。
   - 默认位置选 iCloud Drive 的 `快捷指令`。
   - 在 `子路径 (Subpath)` 填：`RemoteCmd/inbox`。
     - 路径区分大小写，建议保持与你的目录一致。
5. 可再建一个 `停止 cli-tg` 快捷指令，写入 `stop-cli-tg.txt` 到同一路径。


- 若你看到保存位置被锁定在「快捷指令」目录，这是正常的。
- 关键是使用 `子路径 (Subpath)` 补上目标目录（例如 `RemoteCmd/` 或 `RemoteCmd/inbox`）。
- 仅写 `RemoteCmd/` 会落到 `RemoteCmd` 根目录；本方案建议写 `RemoteCmd/inbox`，让 launchd 直接监听到。

