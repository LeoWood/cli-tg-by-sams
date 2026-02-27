# 贡献指南

感谢你为 CLITG 贡献代码。

## 先看这几份文档

- `README.md`：安装、运行、命令用法（用户视角）
- `AGENTS.md`：仓库内协作约定（开发与运维流程）
- `docs/development.md`：开发工作流与调试建议
- `docs/configuration.md`：配置项说明

## 环境要求

- Python 3.10+
- Poetry
- Git
- 可选：tmux（推荐用于长期运行）

## 本地开发快速开始

1. 克隆仓库并进入目录。
2. 安装依赖：`make dev`
3. 复制配置：`cp .env.example .env`
4. 按需修改 `.env`（至少填 `TELEGRAM_BOT_TOKEN`、`TELEGRAM_BOT_USERNAME`、`APPROVED_DIRECTORY`）
5. 运行测试：`make test`
6. 运行静态检查：`make lint`

## 日常开发命令

- `make test`：运行测试
- `make lint`：黑白名单检查（black/isort/flake8/mypy）
- `make format`：格式化代码
- `make run`：tmux 托管重启 bot（单实例校验）
- `make run-debug`：tmux 托管 debug 重启
- `make run-local`：当前终端前台运行

## 分支与提交

建议流程：

1. 从 `main` 拉出功能分支
2. 完成功能与测试
3. 提交前执行：`make format && make lint && make test`
4. 提交信息使用 Conventional Commits（如 `feat:`、`fix:`、`docs:`、`test:`、`refactor:`、`chore:`）

示例：

- `feat: add codex status window formatter`
- `fix: avoid stale tmux session duplicate process`
- `docs: align setup and development guides`

## Pull Request 建议内容

- 变更背景与目标
- 主要改动点
- 测试结果（至少相关定向测试）
- 兼容性/迁移说明（如有）
- 若影响 Telegram 交互，附关键日志或截图

## 代码规范

- 4 空格缩进，Black 行宽 88
- 类型标注必须完整（mypy 开启 `disallow_untyped_defs`）
- 模块/函数：`snake_case`，类：`PascalCase`，常量：`UPPER_SNAKE_CASE`
- 优先复用 `src/exceptions.py` 异常层级
- 日志使用结构化字段（`structlog`）

## 测试规范

- 测试目录当前以 `tests/unit/` 为主
- 文件命名：`test_*.py`
- 异步用例使用 `@pytest.mark.asyncio`
- 先跑受影响模块定向测试，再跑全量 `make test`

## 安全与配置注意事项

- 不要提交 `.env`、token、密钥、真实用户 ID 等敏感信息
- 变更路径访问逻辑时，必须验证 `APPROVED_DIRECTORY` 边界
- 涉及重启/进程管理逻辑时，以 `scripts/tmux-bot.sh` 与 `scripts/restart-bot.sh` 为准
- 远程重启链路为 `/restartbot -> scripts/restart-from-telegram.sh`

## 文档维护要求

当你修改了以下内容时，请同步更新文档：

- 命令菜单或 handler 注册：更新 `README.md`
- 启停/运维脚本：更新 `AGENTS.md` 与相关运维文档
- 配置项：更新 `.env.example` 与 `docs/configuration.md`
