> ⚠️ **归档说明**：本文档为历史设计/阶段性记录，可能与当前实现不一致。请以 README.md、AGENTS.md、docs/development.md、docs/configuration.md 为准。

# TODO-5: Claude Code 集成

## 目标
创建与 Claude Code 的健壮集成，同时支持 CLI 子进程执行和 Python SDK 集成方式，处理响应流式传输、会话状态、超时管理和输出解析，同时确保安全性和可靠性。

## 集成架构

### 组件概览
```
Claude 集成层
├── SDK 集成（Python SDK - 默认方式）
│   ├── 异步 SDK 客户端
│   ├── 流式传输支持
│   ├── 认证管理器
│   └── 工具执行监控
├── CLI 集成（传统子进程方式）
│   ├── 进程管理器（子进程处理）
│   ├── 输出解析器（JSON/流式解析）
│   └── 超时处理器（防止挂起）
├── 会话管理器（状态持久化）
├── 响应流式传输器（实时更新）
├── 费用计算器（用量追踪）
└── 工具监控器（追踪 Claude 的操作）
```

## 核心实现

### 集成模式

Bot 支持两种与 Claude 的集成模式：

#### SDK 集成（默认，推荐）
- 使用 Claude Code Python SDK 进行直接 API 集成
- 原生异步支持，性能更优
- 可靠的流式传输和错误处理
- 可使用现有的 Claude CLI 认证或直接使用 API 密钥
- 实现位于 `src/claude/sdk_integration.py`

#### CLI 集成（传统方式）
- 将 Claude Code CLI 作为子进程使用
- 需要安装并认证 Claude CLI
- 用于兼容性的传统模式
- 实现位于 `src/claude/integration.py`

### Claude SDK 管理器
```python
# src/claude/sdk_integration.py
"""
Claude Code Python SDK 集成

功能特性：
- 原生异步支持
- 流式响应
- 直接 API 集成
- CLI 认证支持
"""

import asyncio
from typing import AsyncIterator, Optional, Dict, Any
from claude_agent_sdk import query, ClaudeAgentOptions

@dataclass
class ClaudeResponse:
    """Claude Code SDK 的响应"""
    content: str
    session_id: str
    cost: float
    duration_ms: int
    num_turns: int
    is_error: bool = False
    error_type: Optional[str] = None
    tools_used: List[Dict[str, Any]] = field(default_factory=list)

class ClaudeSDKManager:
    """管理 Claude Code SDK 集成"""

    def __init__(self, config: Settings):
        self.config = config
        self.options = ClaudeAgentOptions(
            api_key=config.anthropic_api_key_str,
            timeout=config.claude_timeout_seconds,
            working_directory=config.approved_directory
        )

    async def execute_query(
        self,
        prompt: str,
        working_directory: Path,
        session_id: Optional[str] = None,
        stream_callback: Optional[Callable] = None
    ) -> ClaudeResponse:
        """使用 SDK 执行 Claude 查询"""

        try:
            # 为此次查询配置选项
            options = self.options.copy()
            options.working_directory = str(working_directory)

            # 通过流式传输执行
            async for update in query(prompt, options):
                if stream_callback:
                    await stream_callback(update)

            # 返回最终响应
            return self._format_response(update, session_id)

        except Exception as e:
            return ClaudeResponse(
                content=f"Error: {str(e)}",
                session_id=session_id or "unknown",
                cost=0.0,
                duration_ms=0,
                num_turns=0,
                is_error=True,
                error_type=type(e).__name__
            )
```

### Claude 进程管理器（CLI 模式）
```python
# src/claude/integration.py
"""
Claude Code 子进程管理

功能特性：
- 异步子进程执行
- 流式处理
- 超时管理
- 错误恢复
"""

import asyncio
import json
from asyncio.subprocess import Process
from dataclasses import dataclass
from typing import Optional, Callable, AsyncIterator, Dict, Any
from datetime import datetime
import uuid

@dataclass
class ClaudeResponse:
    """Claude Code 的响应"""
    content: str
    session_id: str
    cost: float
    duration_ms: int
    num_turns: int
    is_error: bool = False
    error_type: Optional[str] = None
    tools_used: List[Dict[str, Any]] = None

@dataclass
class StreamUpdate:
    """来自 Claude 的流式更新"""
    type: str  # 'assistant', 'user', 'system', 'result'
    content: Optional[str] = None
    tool_calls: Optional[List[Dict]] = None
    metadata: Optional[Dict] = None

class ClaudeProcessManager:
    """管理 Claude Code 子进程执行"""

    def __init__(self, config: Settings):
        self.config = config
        self.active_processes: Dict[str, Process] = {}

    async def execute_command(
        self,
        prompt: str,
        working_directory: Path,
        session_id: Optional[str] = None,
        continue_session: bool = False,
        stream_callback: Optional[Callable[[StreamUpdate], None]] = None
    ) -> ClaudeResponse:
        """执行 Claude Code 命令"""

        # 构建命令
        cmd = self._build_command(prompt, session_id, continue_session)

        # 创建用于追踪的进程 ID
        process_id = str(uuid.uuid4())

        try:
            # 启动进程
            process = await self._start_process(cmd, working_directory)
            self.active_processes[process_id] = process

            # 带超时地处理输出
            result = await asyncio.wait_for(
                self._handle_process_output(process, stream_callback),
                timeout=self.config.claude_timeout_seconds
            )

            return result

        except asyncio.TimeoutError:
            # 超时时杀死进程
            if process_id in self.active_processes:
                self.active_processes[process_id].kill()
                await self.active_processes[process_id].wait()

            raise ClaudeTimeoutError(
                f"Claude Code timed out after {self.config.claude_timeout_seconds}s"
            )

        finally:
            # 清理
            if process_id in self.active_processes:
                del self.active_processes[process_id]

    def _build_command(
        self,
        prompt: str,
        session_id: Optional[str],
        continue_session: bool
    ) -> List[str]:
        """构建带参数的 Claude Code 命令"""
        cmd = ['claude']

        if continue_session and not prompt:
            # 不带新提示词继续
            cmd.extend(['--continue'])
        else:
            # 新提示词或带提示词继续
            if continue_session:
                cmd.extend(['--continue', prompt])
            else:
                cmd.extend(['-p', prompt])

                if session_id:
                    cmd.extend(['--resume', session_id])

        # 始终使用流式 JSON 获取实时更新
        cmd.extend(['--output-format', 'stream-json'])

        # 添加安全限制
        cmd.extend(['--max-turns', str(self.config.claude_max_turns)])

        # 如果已配置，添加允许的工具
        if hasattr(self.config, 'allowed_tools'):
            cmd.extend(['--allowedTools', ','.join(self.config.allowed_tools)])

        return cmd

    async def _start_process(self, cmd: List[str], cwd: Path) -> Process:
        """启动 Claude Code 子进程"""
        return await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd),
            # 限制内存使用
            limit=1024 * 1024 * 512  # 512MB
        )

    async def _handle_process_output(
        self,
        process: Process,
        stream_callback: Optional[Callable]
    ) -> ClaudeResponse:
        """处理 Claude Code 的流式输出"""
        messages = []
        result = None

        async for line in self._read_stream(process.stdout):
            try:
                msg = json.loads(line)
                messages.append(msg)

                # 创建流式更新
                update = self._parse_stream_message(msg)
                if update and stream_callback:
                    await stream_callback(update)

                # 检查最终结果
                if msg.get('type') == 'result':
                    result = msg

            except json.JSONDecodeError:
                logger.warning(f"Failed to parse JSON: {line}")
                continue

        # 等待进程完成
        return_code = await process.wait()

        if return_code != 0:
            stderr = await process.stderr.read()
            raise ClaudeProcessError(
                f"Claude Code exited with code {return_code}: {stderr.decode()}"
            )

        if not result:
            raise ClaudeParsingError("No result message received from Claude Code")

        return self._parse_result(result, messages)

    async def _read_stream(self, stream) -> AsyncIterator[str]:
        """从流中读取行"""
        while True:
            line = await stream.readline()
            if not line:
                break
            yield line.decode('utf-8').strip()

    def _parse_stream_message(self, msg: Dict) -> Optional[StreamUpdate]:
        """解析流式消息为更新"""
        msg_type = msg.get('type')

        if msg_type == 'assistant':
            # 提取内容和工具调用
            message = msg.get('message', {})
            content_blocks = message.get('content', [])

            # 获取文本内容
            text_content = []
            tool_calls = []

            for block in content_blocks:
                if block.get('type') == 'text':
                    text_content.append(block.get('text', ''))
                elif block.get('type') == 'tool_use':
                    tool_calls.append({
                        'name': block.get('name'),
                        'input': block.get('input', {})
                    })

            return StreamUpdate(
                type='assistant',
                content='\n'.join(text_content) if text_content else None,
                tool_calls=tool_calls if tool_calls else None
            )

        elif msg_type == 'system' and msg.get('subtype') == 'init':
            # 包含可用工具的初始系统消息
            return StreamUpdate(
                type='system',
                metadata={
                    'tools': msg.get('tools', []),
                    'mcp_servers': msg.get('mcp_servers', [])
                }
            )

        return None

    def _parse_result(self, result: Dict, messages: List[Dict]) -> ClaudeResponse:
        """解析最终结果消息"""
        # 从消息中提取使用的工具
        tools_used = []
        for msg in messages:
            if msg.get('type') == 'assistant':
                message = msg.get('message', {})
                for block in message.get('content', []):
                    if block.get('type') == 'tool_use':
                        tools_used.append({
                            'name': block.get('name'),
                            'timestamp': msg.get('timestamp')
                        })

        return ClaudeResponse(
            content=result.get('result', ''),
            session_id=result.get('session_id', ''),
            cost=result.get('cost_usd', 0.0),
            duration_ms=result.get('duration_ms', 0),
            num_turns=result.get('num_turns', 0),
            is_error=result.get('is_error', False),
            error_type=result.get('subtype') if result.get('is_error') else None,
            tools_used=tools_used
        )
```

### 会话状态管理器
```python
# src/claude/session.py
"""
Claude Code 会话管理

功能特性：
- 会话状态追踪
- 多项目支持
- 会话持久化
- 清理策略
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, Optional, List
from pathlib import Path

@dataclass
class ClaudeSession:
    """Claude Code 会话状态"""
    session_id: str
    user_id: int
    project_path: Path
    created_at: datetime
    last_used: datetime
    total_cost: float = 0.0
    total_turns: int = 0
    message_count: int = 0
    tools_used: List[str] = field(default_factory=list)

    def is_expired(self, timeout_hours: int) -> bool:
        """检查会话是否已过期"""
        age = datetime.utcnow() - self.last_used
        return age > timedelta(hours=timeout_hours)

    def update_usage(self, response: ClaudeResponse):
        """使用响应数据更新会话"""
        self.last_used = datetime.utcnow()
        self.total_cost += response.cost
        self.total_turns += response.num_turns
        self.message_count += 1

        # 追踪唯一工具
        if response.tools_used:
            for tool in response.tools_used:
                tool_name = tool.get('name')
                if tool_name and tool_name not in self.tools_used:
                    self.tools_used.append(tool_name)

class SessionManager:
    """管理 Claude Code 会话"""

    def __init__(self, config: Settings, storage: 'SessionStorage'):
        self.config = config
        self.storage = storage
        self.active_sessions: Dict[str, ClaudeSession] = {}

    async def get_or_create_session(
        self,
        user_id: int,
        project_path: Path,
        session_id: Optional[str] = None
    ) -> ClaudeSession:
        """获取现有会话或创建新会话"""

        # 检查现有会话
        if session_id and session_id in self.active_sessions:
            session = self.active_sessions[session_id]
            if not session.is_expired(self.config.session_timeout_hours):
                return session

        # 尝试从存储加载
        if session_id:
            session = await self.storage.load_session(session_id)
            if session and not session.is_expired(self.config.session_timeout_hours):
                self.active_sessions[session_id] = session
                return session

        # 检查用户会话限制
        user_sessions = await self._get_user_sessions(user_id)
        if len(user_sessions) >= self.config.max_sessions_per_user:
            # 移除最旧的会话
            oldest = min(user_sessions, key=lambda s: s.last_used)
            await self.remove_session(oldest.session_id)

        # 创建新会话
        new_session = ClaudeSession(
            session_id=str(uuid.uuid4()),
            user_id=user_id,
            project_path=project_path,
            created_at=datetime.utcnow(),
            last_used=datetime.utcnow()
        )

        # 保存到存储
        await self.storage.save_session(new_session)
        self.active_sessions[new_session.session_id] = new_session

        return new_session

    async def update_session(self, session_id: str, response: ClaudeResponse):
        """使用响应数据更新会话"""
        if session_id in self.active_sessions:
            session = self.active_sessions[session_id]
            session.update_usage(response)

            # 持久化到存储
            await self.storage.save_session(session)

    async def remove_session(self, session_id: str):
        """移除会话"""
        if session_id in self.active_sessions:
            del self.active_sessions[session_id]

        await self.storage.delete_session(session_id)

    async def cleanup_expired_sessions(self):
        """清理过期会话"""
        all_sessions = await self.storage.get_all_sessions()

        for session in all_sessions:
            if session.is_expired(self.config.session_timeout_hours):
                await self.remove_session(session.session_id)

    async def _get_user_sessions(self, user_id: int) -> List[ClaudeSession]:
        """获取用户的所有会话"""
        return await self.storage.get_user_sessions(user_id)
```

### 输出解析器
```python
# src/claude/parser.py
"""
解析 Claude Code 输出格式

功能特性：
- JSON 解析
- 流式解析
- 错误检测
- 工具提取
"""

class OutputParser:
    """解析各种 Claude Code 输出格式"""

    @staticmethod
    def parse_json_output(output: str) -> Dict[str, Any]:
        """解析单条 JSON 输出"""
        try:
            return json.loads(output)
        except json.JSONDecodeError as e:
            raise ClaudeParsingError(f"Failed to parse JSON output: {e}")

    @staticmethod
    def parse_stream_json(lines: List[str]) -> List[Dict[str, Any]]:
        """解析流式 JSON 输出"""
        messages = []

        for line in lines:
            line = line.strip()
            if not line:
                continue

            try:
                msg = json.loads(line)
                messages.append(msg)
            except json.JSONDecodeError:
                logger.warning(f"Skipping invalid JSON line: {line}")
                continue

        return messages

    @staticmethod
    def extract_code_blocks(content: str) -> List[Dict[str, str]]:
        """从响应中提取代码块"""
        code_blocks = []
        pattern = r'```(\w+)?\n(.*?)```'

        for match in re.finditer(pattern, content, re.DOTALL):
            language = match.group(1) or 'text'
            code = match.group(2).strip()

            code_blocks.append({
                'language': language,
                'code': code
            })

        return code_blocks

    @staticmethod
    def extract_file_operations(messages: List[Dict]) -> List[Dict[str, Any]]:
        """从工具调用中提取文件操作"""
        file_ops = []

        for msg in messages:
            if msg.get('type') != 'assistant':
                continue

            message = msg.get('message', {})
            for block in message.get('content', []):
                if block.get('type') != 'tool_use':
                    continue

                tool_name = block.get('name', '')
                tool_input = block.get('input', {})

                # 检查与文件相关的工具
                if tool_name in ['create_file', 'edit_file', 'read_file']:
                    file_ops.append({
                        'operation': tool_name,
                        'path': tool_input.get('path'),
                        'content': tool_input.get('content'),
                        'timestamp': msg.get('timestamp')
                    })

        return file_ops
```

### 工具监控器
```python
# src/claude/monitor.py
"""
监控 Claude 的工具使用

功能特性：
- 追踪工具调用
- 安全校验
- 使用分析
"""

class ToolMonitor:
    """监控并校验 Claude 的工具使用"""

    def __init__(self, config: Settings, security_validator: SecurityValidator):
        self.config = config
        self.security_validator = security_validator
        self.tool_usage: Dict[str, int] = defaultdict(int)

    async def validate_tool_call(
        self,
        tool_name: str,
        tool_input: Dict[str, Any],
        working_directory: Path
    ) -> Tuple[bool, Optional[str]]:
        """在执行前校验工具调用"""

        # 检查工具是否被允许
        if hasattr(self.config, 'allowed_tools'):
            if tool_name not in self.config.allowed_tools:
                return False, f"Tool not allowed: {tool_name}"

        # 校验文件操作
        if tool_name in ['create_file', 'edit_file', 'read_file']:
            file_path = tool_input.get('path')
            if not file_path:
                return False, "File path required"

            # 校验路径安全性
            valid, resolved_path, error = self.security_validator.validate_path(
                file_path,
                working_directory
            )

            if not valid:
                return False, error

        # 追踪使用情况
        self.tool_usage[tool_name] += 1

        return True, None

    def get_tool_stats(self) -> Dict[str, Any]:
        """获取工具使用统计"""
        return {
            'total_calls': sum(self.tool_usage.values()),
            'by_tool': dict(self.tool_usage),
            'unique_tools': len(self.tool_usage)
        }
```

### 集成门面
```python
# src/claude/facade.py
"""
Claude Code 高层集成门面

为 Bot 处理器提供简洁的接口
"""

class ClaudeIntegration:
    """Claude Code 的主集成入口"""

    def __init__(
        self,
        config: Settings,
        process_manager: ClaudeProcessManager,
        session_manager: SessionManager,
        tool_monitor: ToolMonitor
    ):
        self.config = config
        self.process_manager = process_manager
        self.session_manager = session_manager
        self.tool_monitor = tool_monitor

    async def run_command(
        self,
        prompt: str,
        working_directory: Path,
        user_id: int,
        session_id: Optional[str] = None,
        on_stream: Optional[Callable[[StreamUpdate], None]] = None
    ) -> ClaudeResponse:
        """运行带有完整集成的 Claude Code 命令"""

        # 获取或创建会话
        session = await self.session_manager.get_or_create_session(
            user_id,
            working_directory,
            session_id
        )

        # 追踪流式更新
        tools_validated = True

        async def stream_handler(update: StreamUpdate):
            # 校验工具调用
            if update.tool_calls:
                for tool_call in update.tool_calls:
                    valid, error = await self.tool_monitor.validate_tool_call(
                        tool_call['name'],
                        tool_call.get('input', {}),
                        working_directory
                    )

                    if not valid:
                        tools_validated = False
                        logger.error(f"Tool validation failed: {error}")

            # 传递给调用方的处理器
            if on_stream:
                await on_stream(update)

        # 执行命令
        response = await self.process_manager.execute_command(
            prompt=prompt,
            working_directory=working_directory,
            session_id=session.session_id,
            continue_session=bool(session_id),
            stream_callback=stream_handler
        )

        # 更新会话
        await self.session_manager.update_session(session.session_id, response)

        # 在响应中设置会话 ID
        response.session_id = session.session_id

        return response

    async def continue_session(
        self,
        user_id: int,
        working_directory: Path,
        prompt: Optional[str] = None
    ) -> Optional[ClaudeResponse]:
        """继续最近的会话"""

        # 获取用户的会话
        sessions = await self.session_manager._get_user_sessions(user_id)

        # 查找当前目录下最近的会话
        matching_sessions = [
            s for s in sessions
            if s.project_path == working_directory
        ]

        if not matching_sessions:
            return None

        # 获取最近的
        latest_session = max(matching_sessions, key=lambda s: s.last_used)

        # 继续会话
        return await self.run_command(
            prompt=prompt or "",
            working_directory=working_directory,
            user_id=user_id,
            session_id=latest_session.session_id
        )

    async def get_session_info(
        self,
        session_id: str
    ) -> Optional[Dict[str, Any]]:
        """获取会话信息"""
        session = self.active_sessions.get(session_id)

        if not session:
            session = await self.storage.load_session(session_id)

        if session:
            return {
                'session_id': session.session_id,
                'project': str(session.project_path),
                'created': session.created_at.isoformat(),
                'last_used': session.last_used.isoformat(),
                'cost': session.total_cost,
                'turns': session.total_turns,
                'messages': session.message_count,
                'tools_used': session.tools_used
            }

        return None
```

## 错误处理

### 自定义异常
```python
# src/claude/exceptions.py
"""
Claude 相关异常
"""

class ClaudeError(Exception):
    """Claude 基础错误"""
    pass

class ClaudeTimeoutError(ClaudeError):
    """操作超时"""
    pass

class ClaudeProcessError(ClaudeError):
    """进程执行失败"""
    pass

class ClaudeParsingError(ClaudeError):
    """解析输出失败"""
    pass

class ClaudeSessionError(ClaudeError):
    """会话管理错误"""
    pass
```

## 测试

### 集成测试
```python
# tests/test_claude_integration.py
"""
测试 Claude Code 集成
"""

@pytest.fixture
async def mock_claude_process():
    """模拟 Claude Code 进程"""
    # 返回输出测试 JSON 的模拟进程
    pass

async def test_execute_command_success():
    """测试命令执行成功"""

async def test_execute_command_timeout():
    """测试超时处理"""

async def test_stream_parsing():
    """测试流式 JSON 解析"""

async def test_session_management():
    """测试会话创建和持久化"""

async def test_tool_validation():
    """测试工具调用校验"""

async def test_cost_tracking():
    """测试费用累计"""
```

## 配置

### Claude 专用设置
```python
# Claude 集成的额外设置
claude_binary_path: str = "claude"  # Claude CLI 路径
claude_allowed_tools: List[str] = [
    "create_file",
    "edit_file",
    "read_file",
    "bash"
]
claude_disallowed_tools: List[str] = [
    "git commit",
    "git push"
]
claude_system_prompt_append: Optional[str] = None
claude_mcp_enabled: bool = False
claude_mcp_config: Optional[Dict] = None
```

## 验收标准

- [ ] Claude Code 子进程成功执行
- [ ] 流式更新实时工作
- [ ] 会话状态跨命令持久化
- [ ] 超时得到正确处理
- [ ] 输出解析能处理所有格式
- [ ] 工具使用被追踪和校验
- [ ] 费用追踪正确累计
- [ ] 会话过期和清理正常工作
- [ ] 错误处理提供有用反馈
- [ ] 集成测试通过
- [ ] 内存使用保持在限制范围内
- [ ] 并发会话正常工作
