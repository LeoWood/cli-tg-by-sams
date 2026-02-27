# 配置指南

本文档说明 CLITG 当前版本的配置项。权威来源是：

1. `src/config/settings.py`
2. `.env.example`

## 配置加载顺序

按优先级从低到高：

1. `Settings` 默认值
2. `.env` 文件
3. 进程环境变量
4. 环境覆盖（`development/testing/production`）

## 必填项

```bash
TELEGRAM_BOT_TOKEN=
TELEGRAM_BOT_USERNAME=
APPROVED_DIRECTORY=
```

若启用访问限制，建议同时配置：

```bash
ALLOWED_USERS=123456789
```

## 引擎相关（Claude/Codex）

```bash
USE_SDK=true|false
CLAUDE_CLI_PATH=
ANTHROPIC_API_KEY=
CLAUDE_SETTING_SOURCES=
CLAUDE_MAX_TURNS=10
CLAUDE_TIMEOUT_SECONDS=300
CLAUDE_MAX_COST_PER_USER=10.0

ENABLE_CODEX_CLI=false
CODEX_CLI_PATH=
CODEX_ENABLE_MCP=false
```

说明：

- 当前默认引擎是 Codex（运行时仍支持切换 Claude）。
- `USE_SDK=true` 时优先使用 SDK；`false` 时走 CLI 子进程。
- `CLAUDE_SETTING_SOURCES` 留空表示使用 CLI 默认来源。

## 安全与访问控制

```bash
ALLOWED_USERS=123456789,987654321
ENABLE_TOKEN_AUTH=false
AUTH_TOKEN_SECRET=
APPROVED_DIRECTORY=/abs/path
```

规则：

- 开启 `ENABLE_TOKEN_AUTH=true` 时必须提供 `AUTH_TOKEN_SECRET`。
- `APPROVED_DIRECTORY` 必须存在且为目录。

## 速率与会话

```bash
RATE_LIMIT_REQUESTS=10
RATE_LIMIT_WINDOW=60
RATE_LIMIT_BURST=20

SESSION_TIMEOUT_HOURS=24
MAX_SESSIONS_PER_USER=5
DATABASE_URL=sqlite:///data/bot.db
```

## 功能开关

```bash
ENABLE_MCP=false
MCP_CONFIG_PATH=
ENABLE_GIT_INTEGRATION=true
ENABLE_FILE_UPLOADS=true
ENABLE_QUICK_ACTIONS=true
ENABLE_SESSION_EXPORT=true
ENABLE_IMAGE_UPLOADS=true
ENABLE_CONVERSATION_MODE=true
```

## Telegram 网络与轮询参数

```bash
TELEGRAM_CONNECT_TIMEOUT_SECONDS=30
TELEGRAM_READ_TIMEOUT_SECONDS=30
TELEGRAM_WRITE_TIMEOUT_SECONDS=30
TELEGRAM_POOL_TIMEOUT_SECONDS=30
TELEGRAM_CONNECTION_POOL_SIZE=64

TELEGRAM_GET_UPDATES_READ_TIMEOUT_SECONDS=50
TELEGRAM_GET_UPDATES_POOL_TIMEOUT_SECONDS=30
TELEGRAM_GET_UPDATES_CONNECTION_POOL_SIZE=16

POLLING_UPDATE_STALL_SECONDS=0
```

## 诊断与运行建议

- 修改配置后需重启进程才能生效。
- 推荐先执行：`make bot-status`、`make bot-logs` 验证运行状态。
- 生产环境建议：`DEVELOPMENT_MODE=false`、`ENVIRONMENT=production`。

## 最佳实践

1. 从 `.env.example` 复制 `.env`，不要手写遗漏项。
2. 不要提交 `.env` 与任何真实密钥。
3. 配置变更同时更新文档（`README.md`、`docs/configuration.md`）。
4. 引擎路径变更后先本机验证：`which codex` / `which claude`。
