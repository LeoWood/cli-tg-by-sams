> ⚠️ **归档说明**：本文档为历史设计/阶段性记录，可能与当前实现不一致。请以 README.md、AGENTS.md、docs/development.md、docs/configuration.md 为准。

# TODO-2: 配置管理

## 目标
创建一套健壮的、感知环境的配置系统，支持开发、测试和生产部署，并具备完善的校验与安全机制。

## 配置结构

### 环境变量模式
```
# Bot 配置
TELEGRAM_BOT_TOKEN=             # 必填：来自 BotFather 的 Bot Token
TELEGRAM_BOT_USERNAME=          # 必填：Bot 用户名

# 安全
APPROVED_DIRECTORY=             # 必填：项目的基础目录
ALLOWED_USERS=                  # 可选：逗号分隔的用户 ID 列表
ENABLE_TOKEN_AUTH=false         # 可选：启用令牌认证
AUTH_TOKEN_SECRET=              # ENABLE_TOKEN_AUTH=true 时必填

# Claude 配置
CLAUDE_MAX_TURNS=10             # 最大对话轮数
CLAUDE_TIMEOUT_SECONDS=300      # Claude 操作超时时间
CLAUDE_MAX_COST_PER_USER=10.0   # 每用户最大费用（美元）

# 限流
RATE_LIMIT_REQUESTS=10          # 每个时间窗口的请求数
RATE_LIMIT_WINDOW=60            # 时间窗口（秒）
RATE_LIMIT_BURST=20             # 突发容量

# 存储
DATABASE_URL=sqlite:///data/bot.db  # 数据库连接
SESSION_TIMEOUT_HOURS=24            # 会话过期时间
MAX_SESSIONS_PER_USER=5             # 每用户并发会话数

# 功能开关
ENABLE_MCP=false                # Model Context Protocol
MCP_CONFIG_PATH=                # MCP 配置文件路径
ENABLE_GIT_INTEGRATION=true     # Git 命令
ENABLE_FILE_UPLOADS=true        # 文件上传处理
ENABLE_QUICK_ACTIONS=true       # 快捷操作按钮

# 监控
LOG_LEVEL=INFO                  # 日志级别
ENABLE_TELEMETRY=false          # 匿名使用统计
SENTRY_DSN=                     # 错误追踪

# 开发
DEBUG=false                     # 调试模式
DEVELOPMENT_MODE=false          # 开发功能
```

## 配置实现

### 主配置类
```python
# src/config.py
"""
使用 Pydantic Settings 进行配置管理

功能特性：
- 环境变量加载
- 类型校验
- 默认值
- 计算属性
- 环境特定配置
"""

from pydantic import BaseSettings, validator, SecretStr, DirectoryPath
from typing import List, Optional, Dict
from pathlib import Path

class Settings(BaseSettings):
    # Bot 设置
    telegram_bot_token: SecretStr
    telegram_bot_username: str

    # 安全
    approved_directory: DirectoryPath
    allowed_users: Optional[List[int]] = None
    enable_token_auth: bool = False
    auth_token_secret: Optional[SecretStr] = None

    # Claude 设置
    claude_max_turns: int = 10
    claude_timeout_seconds: int = 300
    claude_max_cost_per_user: float = 10.0

    # 限流
    rate_limit_requests: int = 10
    rate_limit_window: int = 60
    rate_limit_burst: int = 20

    # 存储
    database_url: str = "sqlite:///data/bot.db"
    session_timeout_hours: int = 24
    max_sessions_per_user: int = 5

    # 功能开关
    enable_mcp: bool = False
    mcp_config_path: Optional[Path] = None
    enable_git_integration: bool = True
    enable_file_uploads: bool = True
    enable_quick_actions: bool = True

    # 监控
    log_level: str = "INFO"
    enable_telemetry: bool = False
    sentry_dsn: Optional[str] = None

    # 开发
    debug: bool = False
    development_mode: bool = False

    class Config:
        env_file = '.env'
        env_file_encoding = 'utf-8'
        case_sensitive = False
```

### 校验器与计算属性
```python
# 校验器
@validator('allowed_users', pre=True)
def parse_allowed_users(cls, v):
    """解析逗号分隔的用户 ID"""
    if isinstance(v, str):
        return [int(uid.strip()) for uid in v.split(',') if uid.strip()]
    return v

@validator('auth_token_secret')
def validate_auth_token(cls, v, values):
    """确保启用令牌认证时提供了密钥"""
    if values.get('enable_token_auth') and not v:
        raise ValueError('auth_token_secret required when enable_token_auth is True')
    return v

@validator('approved_directory')
def validate_approved_directory(cls, v):
    """确保批准目录存在且为绝对路径"""
    path = Path(v).resolve()
    if not path.exists():
        raise ValueError(f'Approved directory does not exist: {path}')
    return path

# 计算属性
@property
def is_production(self) -> bool:
    return not (self.debug or self.development_mode)

@property
def database_path(self) -> Path:
    """从数据库 URL 中提取路径"""
    if self.database_url.startswith('sqlite:///'):
        return Path(self.database_url.replace('sqlite:///', ''))
    raise ValueError('Only SQLite supported in current version')
```

### 环境特定配置
```python
# src/config/environments.py
"""
环境特定的配置覆盖
"""

class DevelopmentConfig:
    """开发环境覆盖"""
    debug = True
    development_mode = True
    log_level = "DEBUG"
    rate_limit_requests = 100  # 测试时更宽松

class TestingConfig:
    """测试环境配置"""
    database_url = "sqlite:///:memory:"
    approved_directory = "/tmp/test_projects"
    enable_telemetry = False

class ProductionConfig:
    """生产环境配置"""
    debug = False
    development_mode = False
    enable_telemetry = True
```

### 功能开关系统
```python
# src/config/features.py
"""
功能开关管理
"""

class FeatureFlags:
    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def mcp_enabled(self) -> bool:
        return self.settings.enable_mcp and self.settings.mcp_config_path

    @property
    def git_enabled(self) -> bool:
        return self.settings.enable_git_integration

    @property
    def file_uploads_enabled(self) -> bool:
        return self.settings.enable_file_uploads

    @property
    def quick_actions_enabled(self) -> bool:
        return self.settings.enable_quick_actions

    def is_feature_enabled(self, feature_name: str) -> bool:
        """通用功能检查"""
        return getattr(self, f"{feature_name}_enabled", False)
```

### 配置加载器
```python
# src/config/loader.py
"""
带有环境检测的配置加载
"""

import os
from typing import Optional

def load_config(env: Optional[str] = None) -> Settings:
    """根据环境加载配置"""
    env = env or os.getenv('ENVIRONMENT', 'development')

    # 加载基础设置
    settings = Settings()

    # 应用环境覆盖
    if env == 'development':
        settings = apply_overrides(settings, DevelopmentConfig)
    elif env == 'testing':
        settings = apply_overrides(settings, TestingConfig)
    elif env == 'production':
        settings = apply_overrides(settings, ProductionConfig)

    # 校验配置
    validate_config(settings)

    return settings

def validate_config(settings: Settings):
    """额外的运行时校验"""
    # 检查文件权限
    if not os.access(settings.approved_directory, os.R_OK | os.X_OK):
        raise ConfigurationError(f"Cannot access approved directory: {settings.approved_directory}")

    # 校验功能依赖
    if settings.enable_mcp and not settings.mcp_config_path:
        raise ConfigurationError("MCP enabled but no config path provided")
```

## .env.example 模板
```bash
# Claude Code Telegram Bot 配置

# === 必填项 ===
# 来自 @BotFather 的 Telegram Bot Token
TELEGRAM_BOT_TOKEN=your_bot_token_here

# Bot 用户名（不含 @）
TELEGRAM_BOT_USERNAME=your_bot_username

# 项目访问的基础目录（绝对路径）
APPROVED_DIRECTORY=/home/user/projects

# === 安全设置 ===
# 允许访问的 Telegram 用户 ID 列表，逗号分隔（可选）
# 留空则允许所有用户（生产环境不建议）
ALLOWED_USERS=123456789,987654321

# 启用令牌认证
ENABLE_TOKEN_AUTH=false

# 生成认证令牌的密钥（ENABLE_TOKEN_AUTH=true 时必填）
# 可用以下命令生成：openssl rand -hex 32
AUTH_TOKEN_SECRET=

# === Claude 设置 ===
# 需要新建会话前的最大对话轮数
CLAUDE_MAX_TURNS=10

# Claude 操作超时时间（秒）
CLAUDE_TIMEOUT_SECONDS=300

# 每用户最大费用（美元）
CLAUDE_MAX_COST_PER_USER=10.0

# === 限流设置 ===
# 每个时间窗口允许的请求数
RATE_LIMIT_REQUESTS=10

# 限流时间窗口（秒）
RATE_LIMIT_WINDOW=60

# 限流突发容量
RATE_LIMIT_BURST=20

# === 存储设置 ===
# 数据库 URL（默认使用 SQLite）
DATABASE_URL=sqlite:///data/bot.db

# 会话超时时间（小时）
SESSION_TIMEOUT_HOURS=24

# 每用户最大并发会话数
MAX_SESSIONS_PER_USER=5

# === 功能开关 ===
# 启用 Model Context Protocol
ENABLE_MCP=false

# MCP 配置文件路径
MCP_CONFIG_PATH=

# 启用 Git 集成
ENABLE_GIT_INTEGRATION=true

# 启用文件上传处理
ENABLE_FILE_UPLOADS=true

# 启用快捷操作按钮
ENABLE_QUICK_ACTIONS=true

# === 监控 ===
# 日志级别（DEBUG, INFO, WARNING, ERROR）
LOG_LEVEL=INFO

# 启用匿名遥测
ENABLE_TELEMETRY=false

# Sentry DSN 用于错误追踪（可选）
SENTRY_DSN=

# === 开发 ===
# 启用调试模式
DEBUG=false

# 启用开发功能
DEVELOPMENT_MODE=false
```

## 使用示例
```python
# 简单用法
from src.config import load_config

config = load_config()
bot_token = config.telegram_bot_token.get_secret_value()

# 配合功能开关
from src.config import load_config, FeatureFlags

config = load_config()
features = FeatureFlags(config)

if features.git_enabled:
    # 启用 git 命令
    pass

# 指定环境
config = load_config(env='production')

# 访问计算属性
if config.is_production:
    # 生产环境专用行为
    pass
```

## 测试配置
```python
# tests/test_config.py
"""
测试配置加载与校验
"""

def test_required_fields():
    """测试缺少必填字段时抛出错误"""

def test_validator_allowed_users():
    """测试逗号分隔的用户 ID 解析"""

def test_environment_overrides():
    """测试环境特定配置"""

def test_feature_flags():
    """测试功能开关系统"""
```

## 验收标准

- [ ] 配置可以从环境变量加载
- [ ] 校验能够捕获缺失的必填字段
- [ ] 环境特定覆盖正常工作
- [ ] 功能开关能够正确控制功能
- [ ] 敏感值（Token 等）已正确脱敏
- [ ] 配置可以针对不同环境加载
- [ ] 所有校验器在合法输入下通过
- [ ] 无效配置抛出清晰的错误信息
- [ ] .env.example 包含所有配置选项
- [ ] 测试覆盖所有配置场景
