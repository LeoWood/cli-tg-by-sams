# Repository Guidelines

## 项目结构与模块组织
核心代码位于 `src/`：
- `bot/`：Telegram 处理器、回调、消息流与中间件
- `claude/`：Claude/Codex 统一集成层与会话能力
- `services/`：会话、事件、审批等应用服务
- `storage/`：SQLite、仓储与门面
- `config/`、`security/`、`utils/`：配置、安全、通用工具

入口为 `src/main.py`，Poetry 脚本为 `cli-tg-bot` 与 `claude-telegram-bot`（均指向同一入口）。
测试以 `tests/unit/` 为主，当前没有独立 `tests/integration/` 目录。

## 构建、测试与开发命令
以 `Makefile` 为准：
- `make dev`：安装开发依赖并尝试安装 pre-commit
- `make install`：安装生产依赖
- `make test`：运行 `pytest`
- `make lint`：`black --check` + `isort --check-only` + `flake8` + `mypy`
- `make format`：自动格式化 `src/`、`tests/`
- `make run`：通过 `scripts/tmux-bot.sh restart-detached` 后台触发重启（默认推荐）
- `make run-debug`：调试模式后台重启（`BOT_DEBUG=1`）
- `make run-local`：前台直接运行 bot（不走 tmux）
- `make bot-stop|bot-status|bot-logs|bot-attach`：运维辅助命令

## 运行与重启流程（当前实现）
默认使用 `tmux` 托管进程，不建议手写 `tmux new-session` 命令。

1. 标准重启（默认推荐，后台触发）：`./scripts/tmux-bot.sh restart-detached`（或 `make run`）。
2. 同步重启（需要立即拿到启动校验结果）：`./scripts/tmux-bot.sh restart`。
3. 停止服务：`./scripts/tmux-bot.sh stop`（或 `make bot-stop`）。
4. 查看状态：`./scripts/tmux-bot.sh status`（或 `make bot-status`）。
5. 查看日志：`./scripts/tmux-bot.sh logs`（或 `make bot-logs`）。

实现细节（以脚本为准）：
- 会先清理旧 tmux session 与残留 bot 进程，再启动新实例。
- 启动后会检查 bot 进程数，期望值是 `1`；不满足会返回失败。
- tmux 会话名默认 `cli_tg_bot`，可用环境变量 `BOT_TMUX_SESSION` 覆盖。

远程重启链路：
- Telegram 命令 `/restartbot` 会调用 `scripts/restart-from-telegram.sh`。
- 重启事件记录在 `logs/restart-events.log`。

注意：
- 仅在用户明确要求“重启”时执行重启操作。
- 通过 Telegram 远程协作时，若用户仅要求“重启”，默认只执行单条命令 `./scripts/tmux-bot.sh restart-detached`（异步重启，避免会话中断导致回执丢失）；除非用户明确要求，否则不附带 `status/logs` 检测。
- 不要在文档中硬编码机器本地绝对路径或固定 bot 用户名。

## 代码风格与命名约定
使用 Python 3.10+，4 空格缩进，Black 行宽 88。
- 模块/函数：`snake_case`
- 类：`PascalCase`
- 常量：`UPPER_SNAKE_CASE`

`mypy` 开启 `disallow_untyped_defs`，新增或修改接口需补齐类型标注。
优先复用 `src/exceptions.py` 异常层级与结构化日志（`structlog`）模式。

## 测试指南
测试栈：`pytest` + `pytest-asyncio` + `pytest-cov`。
- 文件命名：`test_*.py`
- 异步测试：显式 `@pytest.mark.asyncio`
- 建议先跑定向测试，再跑全量 `make test`

覆盖率阈值以当前 CI/团队约定为准，不在此文件写死固定百分比。

## 提交与合并请求规范
建议使用 Conventional Commits：`feat:`、`fix:`、`refactor:`、`docs:`、`test:`、`chore:`。

PR 建议包含：
- 变更目的与范围
- 测试结果（至少相关定向测试；大改建议附 `make test` 与 `make lint`）
- 必要文档更新
- 若影响 Telegram 交互，附关键日志或截图

## 安全与配置提示
从 `.env.example` 复制 `.env`，禁止提交真实凭据。

重点配置：
- `TELEGRAM_BOT_TOKEN`、`TELEGRAM_BOT_USERNAME`
- `ALLOWED_USERS`
- `APPROVED_DIRECTORY`
- `ENABLE_CODEX_CLI`、`CODEX_CLI_PATH`、`CODEX_ENABLE_MCP`

当前默认引擎为 Codex（见 `src/bot/utils/cli_engine.py`）。
涉及 CLI 路径、代理和启动参数时，以 `scripts/restart-bot.sh` 与 `scripts/tmux-bot.sh` 的实际逻辑为准。
