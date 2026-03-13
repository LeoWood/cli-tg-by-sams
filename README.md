# CLITG

通过 IM 远程操控 CLI 编码智能体，支持多引擎切换、多会话、工具权限审批、流式输出。

基于 Python，当前集成 Telegram + Codex/Claude 双引擎（Codex 为主，Claude 备选）。Long Polling 模式，无需公网 IP 或反向代理，启动即用；当前版本还支持更顺滑的 `/resume` 会话恢复、可展开的 thinking 过程展示、轮询自愈，以及可选的本地 runtime metrics 端点。

## 近期重要更新

- `/resume` 现在默认直接针对当前目录恢复桌面会话；会话按钮会显示更易读的主题摘要，恢复成功后会附带上下文摘要和“最近5轮”入口，也支持在目标目录直接开启全新会话。
- 流式回复的 thinking 过程会先折叠成简洁摘要，按需通过 `View thinking process` 展开；取消、异常结束时也会尽量保留可回看的 thinking 记录。
- Long Polling 增加了更稳健的自愈逻辑：区分传输异常、待处理 update 堵塞等场景自动恢复；`scripts/tmux-bot.sh` 也内置启动重试与退避，降低 bot 启动偶发失败的影响。
- 新增可选本地只读 metrics HTTP 端点：`/metrics` 提供 Prometheus 风格原始指标，`/metricsz` 提供紧凑文本摘要；Telegram 内的 `/opsstatus` 也会带上 tmux / 进程 / 重启事件 / metrics 快照。

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
CLI 编码智能体 (Codex / Claude / ...)
    |  结果解析 + SQLite 存储
    v
IM 回复用户
```

Bot 使用 Long Polling 模式主动拉取消息，不需要公网 IP 或反向代理。默认优先 Codex 引擎；Claude 可按需切换作为备选。

## 前置要求

- Python 3.10+ (推荐 3.11)
- [Poetry](https://python-poetry.org/) 包管理器
- Codex CLI (已安装并登录，默认主引擎)
- Telegram 账号
- Claude Code CLI (可选，仅在切换 Claude 引擎时需要)

## 部署步骤

### Step 1: 安装系统依赖

```bash
# macOS
brew install python@3.11

# Poetry
curl -sSL https://install.python-poetry.org | python3 -

# 可选：仅在使用 Claude CLI 备选引擎时需要
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

# === Codex 主引擎（推荐默认）===
ENABLE_CODEX_CLI=true
CODEX_CLI_PATH=/opt/homebrew/bin/codex

# 建议默认 false，避免 MCP 启动卡顿；需要 MCP 工具时再临时打开
CODEX_ENABLE_MCP=false

# === 可选：本地 runtime metrics ===
METRICS_ENABLED=false
METRICS_HOST=127.0.0.1
METRICS_PORT=9464

# === 可选：轮询自愈阈值 ===
POLLING_UPDATE_STALL_SECONDS=60
POLLING_PENDING_UPDATE_STALL_SECONDS=120

# === Claude 备选（可选）===
USE_SDK=false
CLAUDE_CLI_PATH=./claude-wrapper.sh
CLAUDE_MAX_TURNS=50
CLAUDE_TIMEOUT_SECONDS=600
```

完整配置项参考 `.env.example`；若模板里暂未列出最新变量，可按 `src/config/settings.py` 中同名环境变量补充。
如果暂时只用 Codex，可先不配置 Claude 相关项。
如果要接 Prometheus / VictoriaMetrics / curl 自查，可再打开 `METRICS_ENABLED=true`。

如果你安装了 Poetry 但 `poetry` 命令不可用，请先把 Poetry 加入 PATH：

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

### Step 5: 确保 Codex CLI 已认证（主引擎）

```bash
# 验证 Codex CLI 可执行
codex --version

# 登录认证
codex login

# 验证状态
codex login status
```

### Step 6: 启动

```bash
# 普通启动（通过 tmux 托管，后台触发重启，推荐）
make run

# Debug 日志启动（tmux 托管，后台触发重启）
make run-debug

# 前台直接运行（不经过 tmux）
make run-local

# 或直接运行入口
poetry run python -m src.main
```

启动后在 Telegram 中给 Bot 发消息即可使用。
首次建议执行：

```text
/engine codex
/status
```

### Step 7: （可选）启用 Claude 备选引擎

仅在你需要切换到 Claude 时再做这一节：

```bash
# 1) 配置 wrapper
cp claude-wrapper.example.sh claude-wrapper.sh
chmod +x claude-wrapper.sh

# 2) 安装并认证 Claude CLI
npm install -g @anthropic-ai/claude-code
claude auth login
claude auth status
```

完成后可在 Telegram 里执行 `/engine claude` 切换。

## 日常使用

### Bot 命令（与当前版本同步）

| 命令 | 说明 | 适用引擎 |
|------|------|------|
| `/start` | 显示欢迎页与快捷入口 | 全部 |
| `/help` | 查看完整命令说明 | 全部 |
| `/engine [claude\|codex]` | 切换 CLI 引擎（也可不带参数走按钮） | 全部 |
| `/resume` | 恢复最近会话（默认按当前目录直达） | 全部 |
| `/new` | 清除当前绑定并新建会话 | 全部 |
| `/continue [message]` | 显式续接当前会话 | 全部 |
| `/end` | 结束当前会话 | 全部 |
| `/context [full]` | 查看会话上下文与用量 | 全部（Claude 主展示） |
| `/status [full]` | `/context` 的兼容别名 | 全部（Codex 主展示） |
| `/model` | Claude：按钮切换 Sonnet/Opus/Haiku | Claude |
| `/model [name\|default]` | Codex：设置/清除 `--model` | Codex |
| `/effort [low\|medium\|high\|xhigh\|default]` | Codex：设置/清除思考深度 | Codex |
| `/codexdiag [root\|<session_id>]` | 诊断 Codex MCP 调用情况 | Codex |
| `/cd <path>` | 切换目录（带安全校验） | 全部 |
| `/ls` | 列出当前目录内容 | 全部 |
| `/pwd` | 查看当前目录 | 全部 |
| `/projects` | 显示可用项目 | 全部 |
| `/git` | Git 仓库信息与操作入口 | 全部 |
| `/actions` | 快捷动作菜单（含 `Resume` 入口） | 全部 |
| `/export` | 导出当前会话 | 全部 |
| `/cancel` | 取消当前运行中的任务 | 全部 |
| `/provider` | Claude 通道切换（cc-switch） | Claude |
| `/restartbot` | 远程重启 bot（管理员，重启前会先刷新会话持久化） | 全部 |
| `/opsstatus` | 查看运行态诊断信息（管理员，含 tmux / 进程 / 重启事件 / metrics 快照） | 全部 |

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

### 快捷动作菜单（当前）

- `Projects`：选择项目目录
- `Files`：浏览当前目录文件
- `Status`：查看当前会话与引擎状态
- `New`：创建新会话
- `Resume`：直接恢复当前目录可续接会话（不再强制先选目录）
- `Help`：打开帮助信息

### 会话恢复与 Thinking 展示

- `/resume` 默认优先扫描当前目录，不再强制先选项目；按钮文案会尽量显示“最近主题摘要 + 会话短 ID”，便于快速判断要接哪条上下文。
- 恢复成功后，Bot 会回显当前目录、session 短 ID、最近上下文摘要，并附带“最近5轮”按钮，便于快速确认是否接对会话。
- 如果当前目录下没有合适的历史会话，可直接点 *Start New Session Here* 清空旧绑定，在原目录开一条全新 session。
- 流式输出结束后默认展示折叠版 thinking 摘要；点 `View thinking process` 可展开/收起详细推理过程，取消或报错时也会尽量保留该入口。

### 本地 Runtime Metrics（可选）

启用 `METRICS_ENABLED=true` 后，bot 会在本机启动只读 HTTP 端点（默认 `127.0.0.1:9464`）：

- `GET /metrics`：Prometheus 文本格式，适合采集系统直接抓取
- `GET /metricsz`：紧凑的人类可读摘要，便于 `curl http://127.0.0.1:9464/metricsz`

当前已覆盖的指标重点包括：

- bot / polling / storage 是否在线
- polling 是否请求过自愈重启
- watchdog tick 与最近健康探针年龄
- pending updates 数量
- 当前活跃任务数、CLI 活跃进程数
- 文本请求成功/失败/排队次数与多段延迟直方图

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
| `Action Blocked by Security Policy` | 远程 TG 会话中触发了受限运维命令 | 重启用 `/restartbot`，诊断用 `/opsstatus` |
| `Tool Access Blocked` | 请求依赖了白名单外工具 | 调整 `CLAUDE_ALLOWED_TOOLS` 或改写任务 |
| `Claude process error: exit code 1` | 常见于引擎/模型不匹配 | 先 `/engine claude`，再 `/model` 选 Claude 模型或执行 `/model default` |
| `invalid claude code request` | SDK 显式 setting sources 与网关不兼容 | 保持 `CLAUDE_SETTING_SOURCES` 为空；若需要强制来源再设为 `user,project,local` |

### 诊断日志（定位“进程在线但 TG 无响应”）

- 默认会写入滚动日志到 `logs/bot.log`（单文件 10MB，保留 5 个历史文件）
- 可通过环境变量覆盖日志路径：`CLITG_LOG_FILE=/path/to/custom.log`
- `scripts/tmux-bot.sh restart` / `restart-detached` 现在内置多轮启动重试与退避；异步重启日志默认写入 `logs/restart-detached.log`
- shell 侧会持续刷新 `logs/bot-health.txt`，便于排查 watchdog、健康探针与 polling 自愈状态
- 轮询模式会周期写入 watchdog 心跳与健康探针日志，建议重点检索：
  - `Polling watchdog heartbeat`
  - `Polling health probe succeeded` / `Polling health probe failed`
  - `Attempting polling self-recovery` / `Polling self-recovery failed`
  - `Telegram transport failure detected`
  - `Pending updates detected but bot is not consuming them`
  - `Shutdown signal received`

### 服务宕机/短线自救（iCloud + iOS 快捷指令）

当你不在电脑前、Bot 又恰好宕机或短线时，可用 iCloud 命令投递触发远程重启：

1. 先按文档完成一次性配置：`launchd` 监听 iCloud 命令目录，并执行 `./scripts/tmux-bot.sh restart`（见 [iCloud 远程重启 cli-tg（launchd 监听）](docs/icloud-remote-restart-cli-tg-launchd.md)）。
2. 在 iPhone「快捷指令」创建“重启 cli-tg”，将 `restart-cli-tg.txt` 保存到 iCloud 的 `RemoteCmd/inbox`（使用子路径）。
3. 需要恢复服务时，手机点一次快捷指令即可触发重启。
4. 回到 Mac 可用 `make bot-status` 与日志确认恢复结果。

## 开发命令

```bash
make dev          # 安装所有依赖 (含开发依赖)
make install      # 仅安装生产依赖
make run          # tmux 托管后台重启（默认推荐）
make run-debug    # tmux 托管 Debug 后台重启
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
- [iCloud 远程重启 cli-tg（launchd 监听）文档](docs/icloud-remote-restart-cli-tg-launchd.md)
