> ⚠️ **归档说明**：本文档为历史设计/阶段性记录，可能与当前实现不一致。请以 README.md、AGENTS.md、docs/development.md、docs/configuration.md 为准。

# TODO-8: 测试与质量保证

## 目标
实现全面的测试策略，包括单元测试、集成测试、端到端测试和性能测试，同时通过代码检查、类型检查和持续集成确保代码质量。

## 测试架构

### 测试目录结构
```
tests/
├── unit/                    # 单元测试（镜像 src 结构）
│   ├── bot/
│   │   ├── test_handlers.py
│   │   ├── test_middleware.py
│   │   └── test_core.py
│   ├── claude/
│   │   ├── test_integration.py
│   │   ├── test_parser.py
│   │   └── test_session.py
│   ├── security/
│   │   ├── test_auth.py
│   │   ├── test_validators.py
│   │   └── test_rate_limiter.py
│   └── storage/
│       ├── test_repositories.py
│       └── test_models.py
├── integration/            # 集成测试
│   ├── test_bot_claude.py
│   ├── test_storage_integration.py
│   └── test_security_integration.py
├── e2e/                   # 端到端测试
│   ├── test_user_flows.py
│   └── test_scenarios.py
├── performance/           # 性能测试
│   ├── test_load.py
│   └── test_memory.py
├── fixtures/              # 测试数据
│   ├── __init__.py
│   ├── factories.py      # 测试数据工厂
│   ├── mocks.py         # Mock 对象
│   └── sample_data/     # 样本文件
└── conftest.py          # Pytest 配置
```

## 测试实现

### Pytest 配置
```python
# tests/conftest.py
"""
Pytest 配置和共享 fixtures
"""

import pytest
import asyncio
from pathlib import Path
from unittest.mock import Mock, AsyncMock
import tempfile
import aiosqlite

# 配置异步测试
pytest_plugins = ['pytest_asyncio']

@pytest.fixture(scope="session")
def event_loop():
    """为异步测试创建事件循环"""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()

@pytest.fixture
def test_config():
    """测试配置"""
    from src.config import Settings

    return Settings(
        telegram_bot_token="test_token",
        telegram_bot_username="test_bot",
        approved_directory=Path("/tmp/test_projects"),
        allowed_users=[123456789],
        database_url="sqlite:///:memory:",
        claude_timeout_seconds=10,
        rate_limit_requests=100,
        session_timeout_hours=1,
        enable_telemetry=False
    )

@pytest.fixture
async def test_db():
    """内存测试数据库"""
    from src.storage.database import DatabaseManager

    db = DatabaseManager("sqlite:///:memory:")
    await db.initialize()
    yield db
    await db.close()

@pytest.fixture
def mock_telegram_update():
    """Mock Telegram update"""
    update = Mock()
    update.effective_user.id = 123456789
    update.effective_user.username = "testuser"
    update.message.text = "Test message"
    update.message.chat.id = 123456789
    update.message.reply_text = AsyncMock()
    return update

@pytest.fixture
def mock_claude_response():
    """Mock Claude 响应"""
    from src.claude.integration import ClaudeResponse

    return ClaudeResponse(
        content="Test response",
        session_id="test-session-123",
        cost=0.001,
        duration_ms=1000,
        num_turns=1,
        tools_used=[]
    )

@pytest.fixture
async def test_storage(test_db):
    """带数据库的测试存储"""
    from src.storage.facade import Storage

    storage = Storage("sqlite:///:memory:")
    await storage.initialize()
    yield storage
    await storage.close()

@pytest.fixture
def temp_project_dir():
    """临时项目目录"""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir) / "test_project"
        project_dir.mkdir()

        # 创建样本文件
        (project_dir / "main.py").write_text("print('Hello World')")
        (project_dir / "README.md").write_text("# Test Project")

        yield project_dir
```

### 单元测试

#### Bot 处理器测试
```python
# tests/unit/bot/test_handlers.py
"""
Bot 命令处理器单元测试
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch
from pathlib import Path

from src.bot.handlers import command

class TestCommandHandlers:
    """测试命令处理器"""

    @pytest.mark.asyncio
    async def test_list_files_success(self, mock_telegram_update, temp_project_dir):
        """测试 ls 命令正确列出文件"""
        # 准备
        context = Mock()
        context.user_data = {
            'deps': {
                'session_manager': Mock(get_session=Mock(return_value=Mock(
                    current_directory=temp_project_dir
                ))),
                'audit_logger': AsyncMock(),
                'config': Mock(approved_directory=temp_project_dir.parent)
            }
        }

        # 执行
        await command.list_files(mock_telegram_update, context)

        # 断言
        mock_telegram_update.message.reply_text.assert_called_once()
        call_args = mock_telegram_update.message.reply_text.call_args[0][0]
        assert "main.py" in call_args
        assert "README.md" in call_args

    @pytest.mark.asyncio
    async def test_change_directory_security(self, mock_telegram_update):
        """测试 cd 命令阻止目录遍历攻击"""
        # 准备
        context = Mock()
        context.args = ["../../../etc"]
        context.user_data = {
            'deps': {
                'session_manager': Mock(),
                'security_validator': Mock(
                    validate_path=Mock(return_value=(False, None, "Access denied"))
                ),
                'audit_logger': AsyncMock()
            }
        }

        # 执行
        await command.change_directory(mock_telegram_update, context)

        # 断言
        mock_telegram_update.message.reply_text.assert_called_with("Access denied")

    @pytest.mark.asyncio
    async def test_new_session_clears_state(self, mock_telegram_update):
        """测试新建会话清除 Claude 会话状态"""
        # 准备
        session = Mock()
        session.claude_session_id = "old-session"
        session.current_directory = Path("/test")

        context = Mock()
        context.user_data = {
            'deps': {
                'session_manager': Mock(get_session=Mock(return_value=session)),
                'config': Mock(approved_directory=Path("/"))
            }
        }

        # 执行
        await command.new_session(mock_telegram_update, context)

        # 断言
        assert session.claude_session_id is None
        mock_telegram_update.message.reply_text.assert_called_once()
```

#### 安全测试
```python
# tests/unit/security/test_validators.py
"""
安全验证器单元测试
"""

import pytest
from pathlib import Path

from src.security.validators import SecurityValidator

class TestSecurityValidator:
    """测试安全验证"""

    @pytest.fixture
    def validator(self, temp_project_dir):
        return SecurityValidator(temp_project_dir)

    @pytest.mark.parametrize("path,should_fail", [
        ("../../../etc/passwd", True),
        ("./valid_dir", False),
        ("subdir/file.txt", False),
        ("~/.ssh/keys", True),
        ("/etc/shadow", True),
        ("project/../../../", True),
        ("project/./valid", False),
        ("project%2F..%2F..", True),
        ("$(whoami)", True),
        ("file;rm -rf /", True),
        ("file|mail attacker", True),
    ])
    def test_path_validation(self, validator, path, should_fail):
        """测试路径验证捕获危险路径"""
        valid, resolved, error = validator.validate_path(
            path,
            validator.approved_directory
        )

        if should_fail:
            assert not valid
            assert error is not None
        else:
            assert valid or not (validator.approved_directory / path).exists()

    @pytest.mark.parametrize("filename,should_fail", [
        ("../../etc/passwd", True),
        ("normal_file.py", False),
        (".hidden_file", True),
        ("file.exe", True),
        ("script.sh", False),
        ("../malicious.py", True),
        ("file\x00.txt", True),
    ])
    def test_filename_validation(self, validator, filename, should_fail):
        """测试文件名验证"""
        valid, error = validator.validate_filename(filename)

        if should_fail:
            assert not valid
            assert error is not None
        else:
            assert valid
```

#### Claude 集成测试
```python
# tests/unit/claude/test_integration.py
"""
Claude 集成单元测试
"""

import pytest
import asyncio
import json
from unittest.mock import Mock, AsyncMock, patch

from src.claude.integration import ClaudeProcessManager, ClaudeResponse

class TestClaudeProcessManager:
    """测试 Claude 进程管理"""

    @pytest.fixture
    def process_manager(self, test_config):
        return ClaudeProcessManager(test_config)

    @pytest.mark.asyncio
    async def test_execute_command_success(self, process_manager):
        """测试命令执行成功"""
        # Mock 子进程
        mock_process = Mock()
        mock_process.stdout = self._create_mock_stream([
            json.dumps({"type": "system", "subtype": "init", "tools": ["bash"]}),
            json.dumps({
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "Hello"}]}
            }),
            json.dumps({
                "type": "result",
                "subtype": "success",
                "result": "Hello",
                "session_id": "test-123",
                "cost_usd": 0.001,
                "duration_ms": 100,
                "num_turns": 1
            })
        ])
        mock_process.wait = AsyncMock(return_value=0)

        with patch('asyncio.create_subprocess_exec', return_value=mock_process):
            result = await process_manager.execute_command(
                "test prompt",
                Path("/test"),
                None,
                False,
                None
            )

        assert isinstance(result, ClaudeResponse)
        assert result.content == "Hello"
        assert result.session_id == "test-123"
        assert result.cost == 0.001

    @pytest.mark.asyncio
    async def test_timeout_handling(self, process_manager, test_config):
        """测试超时终止进程"""
        # Mock 慢速子进程
        mock_process = Mock()
        mock_process.stdout = self._create_slow_stream()
        mock_process.kill = Mock()
        mock_process.wait = AsyncMock()

        test_config.claude_timeout_seconds = 0.1  # 极短超时

        with patch('asyncio.create_subprocess_exec', return_value=mock_process):
            with pytest.raises(ClaudeTimeoutError):
                await process_manager.execute_command(
                    "test", Path("/test"), None, False, None
                )

        mock_process.kill.assert_called_once()

    def _create_mock_stream(self, lines):
        """创建模拟流，逐行输出"""
        async def mock_readline():
            for line in lines:
                yield (line + '\n').encode()
            yield b''

        mock_stream = Mock()
        mock_stream.readline = mock_readline().__anext__
        return mock_stream
```

### 集成测试

#### Bot-Claude 集成
```python
# tests/integration/test_bot_claude.py
"""
Bot 与 Claude 的集成测试
"""

import pytest
from pathlib import Path

class TestBotClaudeIntegration:
    """测试 bot 与 Claude 的集成"""

    @pytest.mark.asyncio
    async def test_message_to_claude_flow(
        self,
        test_storage,
        mock_telegram_update,
        mock_claude_response
    ):
        """测试从消息到 Claude 响应的完整流程"""
        # 准备依赖
        deps = {
            'session_manager': Mock(get_session=Mock(return_value=Mock(
                current_directory=Path("/test"),
                claude_session_id=None
            ))),
            'claude_integration': AsyncMock(
                run_command=AsyncMock(return_value=mock_claude_response)
            ),
            'rate_limiter': Mock(
                check_rate_limit=AsyncMock(return_value=(True, None)),
                track_cost=AsyncMock()
            ),
            'config': Mock(enable_quick_actions=False)
        }

        context = Mock()
        context.user_data = {'deps': deps}

        # 执行
        from src.bot.handlers.message import handle_text_message
        await handle_text_message(mock_telegram_update, context)

        # 验证 Claude 被调用
        deps['claude_integration'].run_command.assert_called_once()

        # 验证响应已发送
        assert mock_telegram_update.message.reply_text.called

        # 验证费用已追踪
        deps['rate_limiter'].track_cost.assert_called_with(
            123456789,
            mock_claude_response.cost
        )
```

### 端到端测试

#### 用户流程测试
```python
# tests/e2e/test_user_flows.py
"""
完整用户流程的端到端测试
"""

import pytest
from telegram import Update
from telegram.ext import Application

class TestUserFlows:
    """测试完整用户工作流"""

    @pytest.mark.asyncio
    async def test_new_user_onboarding(self, test_bot_app):
        """测试新用户引导流程"""
        # 模拟 /start 命令
        update = self._create_update("/start", user_id=999999)
        await test_bot_app.process_update(update)

        # 验证欢迎消息
        assert "Welcome to Claude Code Bot" in self.sent_messages[-1]

        # 模拟 /projects 命令
        update = self._create_update("/projects", user_id=999999)
        await test_bot_app.process_update(update)

        # 验证项目列表
        assert "Select a project" in self.sent_messages[-1]

    @pytest.mark.asyncio
    async def test_coding_session_flow(self, test_bot_app):
        """测试完整编码会话"""
        user_id = 123456789

        # 进入项目
        update = self._create_update("/cd myproject", user_id=user_id)
        await test_bot_app.process_update(update)

        # 发送编码请求
        update = self._create_update(
            "Create a Python function to calculate fibonacci",
            user_id=user_id
        )
        await test_bot_app.process_update(update)

        # 验证 Claude 响应
        assert "def fibonacci" in self.sent_messages[-1]

        # 继续对话
        update = self._create_update(
            "Now add memoization",
            user_id=user_id
        )
        await test_bot_app.process_update(update)

        # 验证会话连续性
        assert "memoization" in self.sent_messages[-1]
```

### 性能测试

#### 负载测试
```python
# tests/performance/test_load.py
"""
性能和负载测试
"""

import pytest
import asyncio
import time
from concurrent.futures import ThreadPoolExecutor

class TestPerformance:
    """测试性能特性"""

    @pytest.mark.asyncio
    async def test_concurrent_users(self, test_bot_app):
        """测试多用户并发处理"""
        num_users = 50
        messages_per_user = 5

        async def simulate_user(user_id):
            """模拟用户发送消息"""
            for i in range(messages_per_user):
                update = self._create_update(
                    f"Test message {i}",
                    user_id=user_id
                )
                await test_bot_app.process_update(update)
                await asyncio.sleep(0.1)  # 模拟输入间隔

        start_time = time.time()

        # 并发运行用户
        tasks = [
            simulate_user(user_id)
            for user_id in range(num_users)
        ]
        await asyncio.gather(*tasks)

        duration = time.time() - start_time
        total_messages = num_users * messages_per_user
        throughput = total_messages / duration

        # 断言性能指标
        assert throughput > 10  # 至少每秒 10 条消息
        assert duration < 30    # 30 秒内完成

    @pytest.mark.asyncio
    async def test_memory_usage(self, test_bot_app):
        """测试负载下的内存使用"""
        import psutil
        import os

        process = psutil.Process(os.getpid())
        initial_memory = process.memory_info().rss / 1024 / 1024  # MB

        # 发送大量消息
        for i in range(1000):
            update = self._create_update(f"Message {i}", user_id=123)
            await test_bot_app.process_update(update)

        final_memory = process.memory_info().rss / 1024 / 1024  # MB
        memory_increase = final_memory - initial_memory

        # 断言内存使用合理
        assert memory_increase < 100  # 增长不超过 100MB
```

### 测试工具

#### 测试数据工厂
```python
# tests/fixtures/factories.py
"""
测试数据工厂
"""

import factory
from datetime import datetime

from src.storage.models import UserModel, SessionModel, MessageModel

class UserFactory(factory.Factory):
    """创建测试用户"""
    class Meta:
        model = UserModel

    user_id = factory.Sequence(lambda n: 1000 + n)
    telegram_username = factory.Faker('user_name')
    first_seen = factory.LazyFunction(datetime.utcnow)
    last_active = factory.LazyFunction(datetime.utcnow)
    is_allowed = True
    total_cost = 0.0
    message_count = 0
    session_count = 0

class SessionFactory(factory.Factory):
    """创建测试会话"""
    class Meta:
        model = SessionModel

    session_id = factory.Faker('uuid4')
    user_id = factory.SubFactory(UserFactory)
    project_path = "/test/project"
    created_at = factory.LazyFunction(datetime.utcnow)
    last_used = factory.LazyFunction(datetime.utcnow)
    total_cost = 0.0
    total_turns = 0
    message_count = 0
    is_active = True
```

#### Mock 构建器
```python
# tests/fixtures/mocks.py
"""
Mock 对象构建器
"""

def create_mock_update(text, user_id=123456789, **kwargs):
    """创建 Mock Telegram update"""
    update = Mock()
    update.effective_user.id = user_id
    update.effective_user.username = kwargs.get('username', 'testuser')
    update.message.text = text
    update.message.chat.id = kwargs.get('chat_id', user_id)
    update.message.message_id = kwargs.get('message_id', 1)
    update.message.reply_text = AsyncMock()
    update.message.chat.send_action = AsyncMock()

    # 如果指定了回调数据则添加 callback query
    if 'callback_data' in kwargs:
        update.callback_query = Mock()
        update.callback_query.data = kwargs['callback_data']
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_text = AsyncMock()

    return update
```

## 质量保证

### 代码覆盖率配置
```ini
# .coveragerc
[run]
source = src
omit =
    */tests/*
    */migrations/*
    */__init__.py

[report]
precision = 2
show_missing = True
skip_covered = False

[html]
directory = htmlcov

[xml]
output = coverage.xml
```

### 代码检查配置
```toml
# pyproject.toml 附加配置
[tool.black]
line-length = 88
target-version = ['py39']
include = '\.pyi?$'

[tool.isort]
profile = "black"
line_length = 88

[tool.flake8]
max-line-length = 88
extend-ignore = E203, W503
exclude = .git,__pycache__,docs,old,build,dist

[tool.mypy]
python_version = "3.9"
warn_return_any = true
warn_unused_configs = true
disallow_untyped_defs = true
ignore_missing_imports = true

[tool.pylint]
max-line-length = 88
disable = C0103,C0114,C0115,C0116,R0903
```

### CI/CD 流水线
```yaml
# .github/workflows/test.yml
name: Tests

on:
  push:
    branches: [ main, develop ]
  pull_request:
    branches: [ main ]

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: [3.9, "3.10", 3.11]

    steps:
    - uses: actions/checkout@v3

    - name: 设置 Python ${{ matrix.python-version }}
      uses: actions/setup-python@v4
      with:
        python-version: ${{ matrix.python-version }}

    - name: 缓存依赖
      uses: actions/cache@v3
      with:
        path: ~/.cache/pip
        key: ${{ runner.os }}-pip-${{ hashFiles('requirements/*.txt') }}

    - name: 安装依赖
      run: |
        python -m pip install --upgrade pip
        pip install -r requirements/test.txt

    - name: flake8 代码检查
      run: |
        flake8 src tests

    - name: black 格式检查
      run: |
        black --check src tests

    - name: mypy 类型检查
      run: |
        mypy src

    - name: pytest 运行测试
      run: |
        pytest -v --cov=src --cov-report=xml --cov-report=html

    - name: 上传覆盖率
      uses: codecov/codecov-action@v3
      with:
        file: ./coverage.xml
        fail_ci_if_error: true
```

## 测试命令

### Makefile 附加命令
```makefile
# 测试命令
test:
	pytest -v

test-unit:
	pytest tests/unit -v

test-integration:
	pytest tests/integration -v

test-e2e:
	pytest tests/e2e -v

test-coverage:
	pytest --cov=src --cov-report=html --cov-report=term

test-watch:
	ptw -- -v

test-parallel:
	pytest -n auto

test-profile:
	pytest --profile

test-all: lint type-check test-coverage
```

## 成功标准

- [ ] 单元测试覆盖率 > 80%
- [ ] 所有集成测试通过
- [ ] 端到端测试覆盖主要用户流程
- [ ] 性能测试达到目标
- [ ] 无代码检查错误
- [ ] 类型检查通过
- [ ] CI/CD 流水线全绿
- [ ] 负载测试处理 50+ 并发用户
- [ ] 内存使用在限制范围内
- [ ] 安全测试通过 OWASP 检查
- [ ] Mock 对象正确模拟真实行为
- [ ] 测试执行时间 < 5 分钟
