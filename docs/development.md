# 开发指南

本文档描述 CLITG 当前版本的开发流程与调试方式。

## 1. 前置条件

- Python 3.10+
- Poetry
- Git
- 建议安装 tmux（用于后台托管）

## 2. 初始化开发环境

```bash
git clone https://github.com/codingSamss/cli-tg.git
cd cli-tg
make dev
cp .env.example .env
```

根据本机环境编辑 `.env`，至少设置：

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_BOT_USERNAME`
- `APPROVED_DIRECTORY`

## 3. 常用命令

```bash
make help         # 查看所有命令
make test         # 运行 pytest
make lint         # black/isort/flake8/mypy
make format       # 自动格式化
make run          # tmux 托管重启（单实例校验）
make run-debug    # tmux 托管 debug 重启
make run-local    # 当前终端前台运行
make bot-status   # 查看 tmux + 进程状态
make bot-logs     # 查看最近日志
```

## 4. 项目结构（当前）

```text
src/
├── bot/          # Telegram 命令、消息、回调、中间件
├── claude/       # Claude/Codex 统一集成层
├── services/     # 会话/事件/审批等应用服务
├── storage/      # SQLite 和仓储
├── config/       # Settings 和环境加载
├── security/     # 鉴权、限流、校验
├── utils/        # 通用工具
└── main.py       # 入口

tests/
└── unit/         # 当前主测试目录
```

## 5. 开发与提交流程

1. 从 `main` 创建分支。
2. 完成功能与测试。
3. 提交前执行：

```bash
make format
make lint
make test
```

4. 提交信息建议使用 Conventional Commits。

## 6. 调试建议

### 6.1 运行态问题

优先检查：

1. `make bot-status`（期望 bot 进程数为 1）
2. `make bot-logs`（检查异常栈、轮询状态）
3. `.env` 是否正确（token、白名单、目录）

### 6.2 远程重启链路

- 管理员命令：`/restartbot`
- 脚本入口：`scripts/restart-from-telegram.sh`
- 事件日志：`logs/restart-events.log`

## 7. 文档同步要求

以下改动必须同步文档：

- 新增/移除命令：更新 `README.md`
- 配置项变更：更新 `.env.example` 与 `docs/configuration.md`
- 启停脚本变更：更新 `AGENTS.md`、`docs/development.md`、`SYSTEMD_SETUP.md`
