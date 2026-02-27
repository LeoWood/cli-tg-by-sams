# 安装与配置指南

本文档是 CLITG 的快速安装手册。更完整说明见 `README.md`。

## 1. 前置条件

- Python 3.10+（推荐 3.11）
- Poetry
- Telegram 账号（用于 BotFather 创建 bot）
- Claude CLI（可选，切换到 Claude 引擎时需要）
- Codex CLI（推荐，当前默认引擎）

## 2. 克隆与安装

```bash
git clone https://github.com/codingSamss/cli-tg.git ~/cli-tg
cd ~/cli-tg
make dev
```

## 3. 配置环境变量

```bash
cp .env.example .env
```

至少填写：

```bash
TELEGRAM_BOT_TOKEN=<BotFather token>
TELEGRAM_BOT_USERNAME=<bot username without @>
APPROVED_DIRECTORY=<absolute project root>
ALLOWED_USERS=<your telegram user id>
```

Codex 推荐配置：

```bash
ENABLE_CODEX_CLI=true
CODEX_CLI_PATH=/opt/homebrew/bin/codex
CODEX_ENABLE_MCP=false
```

## 4. 登录 CLI

```bash
# Codex（默认引擎）
codex login
codex login status

# Claude（仅在需要时）
claude auth login
claude auth status
```

## 5. 启动方式

```bash
make run          # tmux 托管重启（推荐）
make run-debug    # tmux 托管 debug 重启
make run-local    # 当前终端前台运行
```

## 6. 启动后验证

在 Telegram 中发送：

```text
/start
/engine
/status
```

如需切换引擎：

```text
/engine codex
/engine claude
```

## 7. 常见问题

- Bot 无响应：先 `make bot-status` 再 `make bot-logs`
- `codex: command not found`：检查 `which codex` 与 `CODEX_CLI_PATH`
- 鉴权失败：确认 `ALLOWED_USERS` 包含你的 Telegram ID
- 目录访问失败：确认 `APPROVED_DIRECTORY` 存在且是绝对路径
