# systemd 用户服务配置指南

本指南说明如何在 Linux 上把 CLITG 作为 `systemd --user` 服务运行。

> macOS 常用 tmux 托管（`make run`）。本文件主要面向 Linux 服务器。

## 1. 准备变量

先约定下面变量（按你的实际路径替换）：

- `PROJECT_DIR`：项目根目录（例如 `/home/ubuntu/cli-tg`）
- `POETRY_BIN`：Poetry 可执行文件（例如 `/home/ubuntu/.local/bin/poetry`）
- `SERVICE_NAME`：建议 `cli-tg`

## 2. 创建服务文件

```bash
mkdir -p ~/.config/systemd/user
nano ~/.config/systemd/user/cli-tg.service
```

写入：

```ini
[Unit]
Description=CLITG Telegram Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=%h/cli-tg
ExecStart=%h/.local/bin/poetry run cli-tg-bot
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
Environment="PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin"

[Install]
WantedBy=default.target
```

说明：

- `ExecStart` 请使用当前项目脚本入口：`cli-tg-bot`
- `WorkingDirectory` 必须指向仓库根目录

## 3. 启动与自启

```bash
systemctl --user daemon-reload
systemctl --user enable cli-tg.service
systemctl --user start cli-tg.service
```

检查状态：

```bash
systemctl --user status cli-tg.service
```

查看日志：

```bash
journalctl --user -u cli-tg.service -f
```

## 4. 常用运维命令

```bash
systemctl --user restart cli-tg.service
systemctl --user stop cli-tg.service
systemctl --user disable cli-tg.service
journalctl --user -u cli-tg.service -n 100
```

## 5. 常见问题

### 服务无法启动

1. 检查 `WorkingDirectory` 是否正确。
2. 检查 `poetry run cli-tg-bot` 在该目录能否手动启动。
3. 检查 `.env` 是否齐全（token、目录、用户白名单）。

### 注销后服务停止

启用 lingering：

```bash
loginctl enable-linger "$USER"
```

## 6. 安全建议

- 生产环境建议：`DEVELOPMENT_MODE=false`、`ENVIRONMENT=production`
- 不要把 `.env`、token、密钥提交到仓库
- 建议限制 `ALLOWED_USERS`，避免开放访问
