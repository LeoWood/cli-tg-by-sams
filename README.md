# CLITG

通过 IM 远程操控 CLI 编码智能体，支持多引擎切换、多会话、工具权限审批、流式输出。

基于 Python，当前集成 Telegram + Claude/Codex 双引擎。Long Polling 模式，无需公网 IP 或反向代理，启动即用。

## 架构概览

```
IM 客户端 (Telegram / ...)
    |  HTTPS
    v
IM 平台 API
    |  Long Polling / Webhook
    v
本地 Python Bot 进程
    |  引擎抽象层 (SDK / CLI 子进程)
    v
CLI 编码智能体 (Claude / Codex / ...)
    |  结果解析 + SQLite 存储
    v
IM 回复用户
```

Bot 使用 Long Polling 模式主动拉取消息，不需要公网 IP 或反向代理。引擎集成采用双后端架构：SDK 为主、CLI 子进程兜底，失败时自动切换。

## 前置要求

- Python 3.10+ (推荐 3.11)
- [Poetry](https://python-poetry.org/) 包管理器
- Claude Code CLI (已登录认证；当前启动流程仍会初始化 Claude 集成)
- Codex CLI (已安装并登录，用于 Codex 引擎)
- Telegram 账号

## 部署步骤

### Step 1: 安装系统依赖

```bash
# macOS
brew install python@3.11

# Poetry
curl -sSL https://install.python-poetry.org | python3 -

# Node.js (CLI fallback 模式需要)
brew install node
```

### Step 2: 创建 Telegram Bot

1. 在 Telegram 搜索 `@BotFather`，发送 `/newbot`
2. 按提示设置 Bot 名称，获得 **Bot Token** (格式: `1234567890:ABC-DEF...`)
3. 记下 Bot 用户名 (不带 `@`)
4. 获取你的 User ID: 向 `@userinfobot` 发消息，记下返回的数字

### Step 3: 克隆项目并安装依赖

```bash
git clone <repo-url> ~/cli-tg
cd ~/cli-tg
poetry install
```

### Step 4: 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env`，填写以下必填项:

```bash
# === 必填 ===
TELEGRAM_BOT_TOKEN=<从 BotFather 获取>
TELEGRAM_BOT_USERNAME=<Bot 用户名，不带 @>
APPROVED_DIRECTORY=/path/to/your/projects

# === 安全 ===
ALLOWED_USERS=<你的 Telegram User ID>

# === CLI 引擎集成（推荐：Codex 为主）===
USE_SDK=false
CLAUDE_CLI_PATH=./claude-wrapper.sh
CLAUDE_MAX_TURNS=50
CLAUDE_TIMEOUT_SECONDS=600

# 启用 Codex 引擎适配（当前版本默认优先 Codex）
ENABLE_CODEX_CLI=true
CODEX_CLI_PATH=/opt/homebrew/bin/codex

# 建议默认 false，避免 MCP 启动卡顿；需要 MCP 工具时再临时打开
CODEX_ENABLE_MCP=false
```

完整配置项参考 `.env.example`。

如果你安装了 Poetry 但 `poetry` 命令不可用，请先把 Poetry 加入 PATH：

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

### Step 5: 配置 claude-wrapper.sh

如果使用 CLI 子进程模式 (`USE_SDK=false`)，建议从模板复制：

```bash
cp claude-wrapper.example.sh claude-wrapper.sh
chmod +x claude-wrapper.sh
```

然后按你的本机环境修改 `claude-wrapper.sh`（例如代理地址、CLI 路径）。  
本地 `claude-wrapper.sh` 保持在 `.gitignore` 中，避免把机器相关配置提交到仓库。

### Step 6: 确保 Claude CLI 已认证（切换到 Claude 引擎时需要）

```bash
# 安装 Claude Code CLI
npm install -g @anthropic-ai/claude-code

# 登录认证
claude auth login

# 验证状态
claude auth status
```

### Step 7: 确保 Codex CLI 已认证（Codex 默认引擎）

```bash
# 验证 Codex CLI 可执行
codex --version

# 登录认证
codex login

# 验证状态
codex login status
```

### Step 8: 启动

```bash
# 普通启动（通过 tmux 托管，自动重启并校验单实例）
make run

# Debug 日志启动（tmux 托管）
make run-debug

# 前台直接运行（不经过 tmux）
make run-local

# 或直接运行入口
poetry run python -m src.main
```

启动后在 Telegram 中给 Bot 发消息即可使用。
首次建议执行：

```text
/engine
/engine codex
/status
```

## 日常使用

### Bot 命令（与当前版本同步）

| 命令 | 说明 | 适用引擎 |
|------|------|------|
| `/start` | 显示欢迎页与快捷入口 | 全部 |
| `/help` | 查看完整命令说明 | 全部 |
| `/engine [claude\|codex]` | 切换 CLI 引擎（也可不带参数走按钮） | 全部 |
| `/resume` | 恢复桌面端最近会话 | 全部 |
| `/new` | 清除当前绑定并新建会话 | 全部 |
| `/continue [message]` | 显式续接当前会话 | 全部 |
| `/end` | 结束当前会话 | 全部 |
| `/context [full]` | 查看会话上下文与用量 | 全部（Claude 主展示） |
| `/status [full]` | `/context` 的兼容别名 | 全部（Codex 主展示） |
| `/model` | Claude：按钮切换 Sonnet/Opus/Haiku | Claude |
| `/model [name\|default]` | Codex：设置/清除 `--model` | Codex |
| `/codexdiag [root\|<session_id>]` | 诊断 Codex MCP 调用情况 | Codex |
| `/cd <path>` | 切换目录（带安全校验） | 全部 |
| `/ls` | 列出当前目录内容 | 全部 |
| `/pwd` | 查看当前目录 | 全部 |
| `/projects` | 显示可用项目 | 全部 |
| `/git` | Git 仓库信息与操作入口 | 全部 |
| `/actions` | 快捷动作菜单 | 全部 |
| `/export` | 导出当前会话 | 全部 |
| `/cancel` | 取消当前运行中的任务 | 全部 |
| `/provider` | Claude 通道切换（cc-switch） | Claude |
| `/restartbot` | 远程重启 bot（管理员） | 全部 |
| `/opsstatus` | 查看运行态诊断信息（管理员） | 全部 |

### 会话导出说明

- `/export` 支持 `Markdown / JSON / HTML` 三种格式
- JSON 导出包含会话元数据（`project_path`、`created_at`、`last_used`、`total_cost`、`total_turns`）和消息字段（`prompt`、`response`、`error`、`cost`、`duration_ms`）
- HTML 导出会对消息内容做转义，避免原始 HTML/脚本内容直接渲染

### 使用方式

- 直接发送文本消息 = 向当前引擎（Claude/Codex）下达指令
- 发送文件 = 由当前引擎分析文件内容（支持代码、配置、文档）
- 发送图片 = 引擎分析截图/图表（能力取决于当前引擎与模式）
- 会话按“用户 + 会话作用域（私聊/群聊话题）+ 目录”维护
- 引擎切换后会清理旧会话绑定，并引导你重新选择目录与可恢复会话

## 安全模型

5 层防御体系:

| 层级 | 机制 | 说明 |
|------|------|------|
| 身份认证 | Telegram User ID 白名单 | `ALLOWED_USERS` 配置 |
| 目录隔离 | `APPROVED_DIRECTORY` + 路径穿越防护 | 只允许访问指定目录及子目录 |
| 输入验证 | 屏蔽 `..`、`;`、`&&`、`$()` 等 | 阻止命令注入 |
| 限流 | Token Bucket 算法 | 可配置请求数/窗口/突发容量 |
| 审计日志 | 全操作记录 | 安全事件自动告警 |

> Telegram Bot 消息非端到端加密，经过 Telegram 服务器中转。不要通过 Bot 传递密码、API Key 等敏感信息。

## 故障排查

| 问题 | 原因 | 解决 |
|------|------|------|
| `zsh: command not found: poetry` | Poetry 未加入 PATH | 执行 `export PATH="$HOME/.local/bin:$PATH"` 并写入 `~/.zshrc` |
| `ModuleNotFoundError` | 依赖未安装 | `poetry install` |
| `No such file: claude` | CLI 路径错误 | 检查 `.env` 中 `CLAUDE_CLI_PATH` |
| `ENABLE_CODEX_CLI is true but codex binary not found` | 未安装 Codex CLI 或路径错误 | 安装 Codex CLI，或在 `.env` 中设置 `CODEX_CLI_PATH` |
| `codex: command not found` | Codex CLI 未安装或不在 PATH | 安装 Codex CLI，并确保 `which codex` 有输出 |
| `codex login status` 显示未登录 | Codex 认证未完成 | 执行 `codex login` 完成认证 |
| `Can't parse entities` | 消息格式解析失败 | 检查响应中的特殊字符转义 |
| `Authentication failed` | User ID 不在白名单 | 检查 `ALLOWED_USERS` |
| `Rate limit exceeded` | 请求过于频繁 | 调整 `RATE_LIMIT_*` 配置 |
| Bot 无响应 | Token 错误或进程未启动 | 检查 `TELEGRAM_BOT_TOKEN` 和进程状态 |
| `Claude process error: exit code 1` | 常见于引擎/模型不匹配 | 先 `/engine claude`，再 `/model` 选 Claude 模型或执行 `/model default` |
| `invalid claude code request` | SDK 显式 setting sources 与网关不兼容 | 保持 `CLAUDE_SETTING_SOURCES` 为空；若需要强制来源再设为 `user,project,local` |

### 诊断日志（定位“进程在线但 TG 无响应”）

- 默认会写入滚动日志到 `logs/bot.log`（单文件 10MB，保留 5 个历史文件）
- 可通过环境变量覆盖日志路径：`CLITG_LOG_FILE=/path/to/custom.log`
- 轮询模式会周期写入 watchdog 心跳与健康探针日志，建议重点检索：
  - `Polling watchdog heartbeat`
  - `Polling health probe succeeded` / `Polling health probe failed`
  - `Attempting polling self-recovery` / `Polling self-recovery failed`
  - `Shutdown signal received`

## 开发命令

```bash
make dev          # 安装所有依赖 (含开发依赖)
make install      # 仅安装生产依赖
make run          # tmux 托管重启（单实例校验）
make run-debug    # tmux 托管 Debug 重启
make run-local    # 当前终端前台运行
make test         # 运行测试 + 覆盖率
make lint         # Black + isort + flake8 + mypy
make format       # 自动格式化代码
```

## 参考链接

- [python-telegram-bot 文档](https://docs.python-telegram-bot.org/)
- [claude-agent-sdk](https://pypi.org/project/claude-agent-sdk/)
- [Telegram Bot API](https://core.telegram.org/bots/api)
- [Poetry 文档](https://python-poetry.org/docs/)
