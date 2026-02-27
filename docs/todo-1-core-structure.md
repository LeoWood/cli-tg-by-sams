> ⚠️ **归档说明**：本文档为历史设计/阶段性记录，可能与当前实现不一致。请以 README.md、AGENTS.md、docs/development.md、docs/configuration.md 为准。

# TODO-1: 项目结构与基础搭建

## 目标
建立一个组织良好、易于维护的项目结构，支持开发和开源贡献。

## 目录结构

```
cli-tg/
├── src/
│   ├── __init__.py
│   ├── bot/
│   │   ├── __init__.py
│   │   ├── handlers/
│   │   │   ├── __init__.py
│   │   │   ├── command.py      # Command handlers
│   │   │   ├── message.py      # Message handlers
│   │   │   └── callback.py     # Inline keyboard handlers
│   │   ├── middleware/
│   │   │   ├── __init__.py
│   │   │   ├── auth.py         # Authentication middleware
│   │   │   ├── logging.py      # Logging middleware
│   │   │   └── error.py        # Error handling middleware
│   │   └── core.py             # Main bot class
│   ├── claude/
│   │   ├── __init__.py
│   │   ├── integration.py      # Claude Code subprocess manager
│   │   ├── parser.py           # Output parsing
│   │   └── session.py          # Session management
│   ├── storage/
│   │   ├── __init__.py
│   │   ├── database.py         # Database connection
│   │   ├── models.py           # Data models
│   │   └── repositories.py     # Data access layer
│   ├── security/
│   │   ├── __init__.py
│   │   ├── auth.py            # Authentication logic
│   │   ├── validators.py      # Input validation
│   │   └── rate_limiter.py    # Rate limiting
│   ├── utils/
│   │   ├── __init__.py
│   │   ├── formatting.py      # Message formatting
│   │   ├── file_handler.py    # File operations
│   │   └── constants.py       # App constants
│   ├── config.py              # Configuration management
│   ├── exceptions.py          # Custom exceptions
│   └── main.py               # Entry point
├── tests/
│   ├── __init__.py
│   ├── unit/                  # Unit tests mirror src structure
│   ├── integration/           # Integration tests
│   ├── fixtures/              # Test data
│   └── conftest.py           # Pytest configuration
├── docs/
│   ├── setup.md
│   ├── configuration.md
│   ├── api/                   # API documentation
│   └── development.md
├── scripts/
│   ├── setup.sh              # Development setup
│   ├── migrate.py            # Database migrations
│   └── check_health.py       # Health check script
├── docker/
│   ├── Dockerfile
│   ├── docker-compose.yml
│   └── .dockerignore
├── .github/
│   ├── workflows/
│   │   ├── test.yml          # CI testing
│   │   ├── lint.yml          # Code quality
│   │   └── release.yml       # Release automation
│   ├── ISSUE_TEMPLATE/
│   │   ├── bug_report.md
│   │   └── feature_request.md
│   └── pull_request_template.md
├── requirements/
│   ├── base.txt              # Core dependencies
│   ├── dev.txt               # Development dependencies
│   └── test.txt              # Testing dependencies
├── .env.example              # Environment template
├── .gitignore
├── README.md
├── CONTRIBUTING.md
├── SECURITY.md
├── LICENSE
├── pyproject.toml           # Project metadata
├── setup.py                 # Package setup
└── Makefile                 # Common commands
```

## 核心包配置

### pyproject.toml
```toml
[tool.poetry]
name = "cli-tg"
version = "0.1.0"
description = "Telegram bot for remote Claude Code access"
authors = ["Your Name <email@example.com>"]
license = "MIT"
readme = "README.md"
repository = "https://github.com/codingSamss/cli-tg"
keywords = ["telegram", "bot", "claude", "ai", "development"]

[tool.black]
line-length = 88
target-version = ['py39']

[tool.isort]
profile = "black"
line_length = 88

[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = "test_*.py"
addopts = "-v --cov=src --cov-report=html --cov-report=term-missing"

[tool.mypy]
python_version = "3.9"
warn_return_any = true
warn_unused_configs = true
disallow_untyped_defs = true
```

## 日志基础设施

### 结构化日志配置
```python
# src/utils/logging.py
"""
使用 structlog 配置结构化日志
- 生产环境输出 JSON
- 开发环境美化打印
- 使用关联 ID 进行请求追踪
- 性能指标
"""

# 根据环境进行配置
# 开发环境：彩色、易于阅读的输出
# 生产环境：包含完整上下文的 JSON
# 包含字段：timestamp、level、logger、correlation_id、user_id、event
```

## 异常层级

### 基础异常
```python
# src/exceptions.py
"""
ClaudeCodeTelegramError (base)
├── ConfigurationError
│   ├── MissingConfigError
│   └── InvalidConfigError
├── SecurityError
│   ├── AuthenticationError
│   ├── AuthorizationError
│   └── DirectoryTraversalError
├── ClaudeError
│   ├── ClaudeTimeoutError
│   ├── ClaudeProcessError
│   └── ClaudeParsingError
├── StorageError
│   ├── DatabaseConnectionError
│   └── DataIntegrityError
└── TelegramError
    ├── MessageTooLongError
    └── RateLimitError
"""
```

## 开发环境

### Makefile 命令
```makefile
.PHONY: install dev test lint format clean

install:
	pip install -r requirements/base.txt

dev:
	pip install -r requirements/dev.txt
	pre-commit install

test:
	pytest

lint:
	black --check src tests
	isort --check-only src tests
	flake8 src tests
	mypy src

format:
	black src tests
	isort src tests

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
```

## 需要创建的初始文件

1. **src/__init__.py**：版本和包信息
2. **src/main.py**：带有基本参数解析的入口文件
3. **src/config.py**：空的配置类
4. **src/exceptions.py**：完整的异常层级
5. **src/utils/constants.py**：应用级常量
6. **.env.example**：包含所有必需变量的模板
7. **requirements/base.txt**：仅核心依赖
8. **README.md**：基本项目描述
9. **.gitignore**：Python 专用忽略规则
10. **Makefile**：开发命令

## 需要包含的依赖

### requirements/base.txt
```
python-telegram-bot>=20.0
structlog>=23.0
pydantic>=2.0
pydantic-settings>=2.0
asyncio>=3.4
aiofiles>=23.0
```

### requirements/dev.txt
```
-r base.txt
-r test.txt
black>=23.0
isort>=5.0
flake8>=6.0
mypy>=1.0
pre-commit>=3.0
ipython>=8.0
```

### requirements/test.txt
```
-r base.txt
pytest>=7.0
pytest-asyncio>=0.21
pytest-cov>=4.0
pytest-mock>=3.0
factory-boy>=3.0
```

## 验收标准

- [ ] 所有目录已创建并包含 __init__.py 文件
- [ ] 依赖安装成功
- [ ] 基本日志功能可以输出结构化内容
- [ ] 异常层级已实现
- [ ] Makefile 命令正常运行
- [ ] Pre-commit 钩子已配置
- [ ] 可以成功运行 `make test`（即使没有测试用例）
- [ ] 项目可通过 `pip install -e .` 安装
