# CLITG 项目概述

## 项目定位

CLITG 是一个通过 Telegram 远程操控 CLI 编码智能体的 Python Bot，支持 Claude/Codex 双引擎、会话持久化、权限审批、流式输出与运维诊断。

## 当前核心能力

- 多引擎切换：`/engine claude|codex`
- 会话管理：`/new`、`/continue`、`/end`、`/resume`
- 上下文与用量：`/context`、`/status`、`/codexdiag`
- 项目导航：`/projects`、`/cd`、`/ls`、`/pwd`
- 工具能力：文件处理、图片处理、Git 信息、会话导出
- 运维能力：`/restartbot`、`/opsstatus`、tmux 托管与单实例校验

## 架构分层

```text
Telegram Client
   -> Telegram Bot API (long polling)
   -> src/bot            # handlers + middleware + ui
   -> src/claude         # engine integration (Claude/Codex)
   -> src/services       # session/event/approval orchestration
   -> src/storage        # sqlite + repositories
   -> src/security       # auth/rate-limit/validation
   -> src/config         # settings + feature flags
```

## 代码结构

```text
src/
├── bot/
├── claude/
├── services/
├── storage/
├── config/
├── security/
├── utils/
└── main.py

tests/
└── unit/
```

## 运行模型

- 默认使用 long polling（无需公网入口）。
- 推荐通过 `scripts/tmux-bot.sh` 托管进程，确保单实例运行。
- 默认引擎为 Codex；可随时切换至 Claude。

## 配置与安全基线

- 必填：`TELEGRAM_BOT_TOKEN`、`TELEGRAM_BOT_USERNAME`、`APPROVED_DIRECTORY`
- 建议开启：`ALLOWED_USERS`
- 禁止提交：`.env` 与任何真实密钥
- 路径访问受 `APPROVED_DIRECTORY` 边界限制

## 文档入口

- 用户使用：`README.md`
- 开发流程：`docs/development.md`
- 配置项：`docs/configuration.md`
- 仓库协作约定：`AGENTS.md`
