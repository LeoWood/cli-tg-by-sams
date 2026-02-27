> ⚠️ **归档说明**：本文档为历史设计/阶段性记录，可能与当前实现不一致。请以 README.md、AGENTS.md、docs/development.md、docs/configuration.md 为准。

# TODO-4: Telegram Bot 核心

## 目标
构建核心的 Telegram Bot 基础设施，实现完善的命令处理、消息路由、内联键盘和错误管理，同时保持清晰的架构和可扩展性。

## Bot 架构

### 组件结构
```
Bot 核心
├── 主 Bot 类（协调器）
├── 命令处理器
│   ├── 导航命令 (/cd, /ls, /pwd)
│   ├── 会话命令 (/new, /continue, /status)
│   ├── 工具命令 (/help, /start, /projects)
│   └── 管理员命令 (/stats, /users)
├── 消息处理器
│   ├── 文本消息处理器
│   ├── 文档处理器
│   └── 图片处理器
├── 回调处理器
│   ├── 项目选择
│   ├── 快捷操作
│   └── 确认对话框
└── 响应格式化
    ├── 代码格式化
    ├── 错误格式化
    └── 进度指示器
```

## 主 Bot 实现

### 核心 Bot 类
```python
# src/bot/core.py
"""
Telegram Bot 主类

功能特性：
- 命令注册
- 处理器管理
- 上下文注入
- 优雅关闭
"""

from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from telegram import Update, BotCommand
from typing import Dict, List, Callable
import asyncio

class ClaudeCodeBot:
    """Bot 主协调器"""

    def __init__(self, config: Settings, dependencies: Dict[str, Any]):
        self.config = config
        self.deps = dependencies
        self.app: Optional[Application] = None
        self.handlers: Dict[str, Callable] = {}

    async def initialize(self):
        """初始化 Bot 应用"""
        # 创建应用
        self.app = Application.builder().token(
            self.config.telegram_bot_token.get_secret_value()
        ).build()

        # 设置 Bot 命令菜单
        await self._set_bot_commands()

        # 注册处理器
        self._register_handlers()

        # 添加中间件
        self._add_middleware()

        # 初始化 Webhook 或轮询
        if self.config.webhook_url:
            await self._setup_webhook()

    async def _set_bot_commands(self):
        """设置 Bot 命令菜单"""
        commands = [
            BotCommand("start", "Start bot and show help"),
            BotCommand("help", "Show available commands"),
            BotCommand("new", "Start new Claude session"),
            BotCommand("continue", "Continue last session"),
            BotCommand("ls", "List files in current directory"),
            BotCommand("cd", "Change directory"),
            BotCommand("pwd", "Show current directory"),
            BotCommand("projects", "Show all projects"),
            BotCommand("status", "Show session status"),
            BotCommand("export", "Export current session"),
        ]

        await self.app.bot.set_my_commands(commands)

    def _register_handlers(self):
        """注册所有命令和消息处理器"""
        # 导入处理器
        from .handlers import command, message, callback

        # 命令处理器
        self.app.add_handler(CommandHandler("start", self._inject_deps(command.start_command)))
        self.app.add_handler(CommandHandler("help", self._inject_deps(command.help_command)))
        self.app.add_handler(CommandHandler("new", self._inject_deps(command.new_session)))
        self.app.add_handler(CommandHandler("continue", self._inject_deps(command.continue_session)))
        self.app.add_handler(CommandHandler("ls", self._inject_deps(command.list_files)))
        self.app.add_handler(CommandHandler("cd", self._inject_deps(command.change_directory)))
        self.app.add_handler(CommandHandler("pwd", self._inject_deps(command.print_working_directory)))
        self.app.add_handler(CommandHandler("projects", self._inject_deps(command.show_projects)))
        self.app.add_handler(CommandHandler("status", self._inject_deps(command.session_status)))

        # 消息处理器
        self.app.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            self._inject_deps(message.handle_text_message)
        ))
        self.app.add_handler(MessageHandler(
            filters.Document.ALL,
            self._inject_deps(message.handle_document)
        ))
        self.app.add_handler(MessageHandler(
            filters.PHOTO,
            self._inject_deps(message.handle_photo)
        ))

        # 回调查询处理器
        self.app.add_handler(CallbackQueryHandler(
            self._inject_deps(callback.handle_callback_query)
        ))

        # 错误处理器
        self.app.add_error_handler(self._error_handler)

    def _inject_deps(self, handler: Callable) -> Callable:
        """向处理器注入依赖"""
        async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE):
            # 将依赖添加到上下文
            context.user_data['deps'] = self.deps
            return await handler(update, context)
        return wrapped

    def _add_middleware(self):
        """向应用添加中间件"""
        # 中间件按顺序执行
        self.app.add_handler(
            MessageHandler(filters.ALL, self._inject_deps(auth_middleware)),
            group=-2  # 认证优先
        )
        self.app.add_handler(
            MessageHandler(filters.ALL, self._inject_deps(rate_limit_middleware)),
            group=-1  # 限流其次
        )

    async def start(self):
        """启动 Bot"""
        await self.initialize()

        if self.config.webhook_url:
            # Webhook 模式
            await self.app.run_webhook(
                listen="0.0.0.0",
                port=self.config.webhook_port,
                url_path=self.config.webhook_path,
                webhook_url=self.config.webhook_url
            )
        else:
            # 轮询模式
            await self.app.run_polling(
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True
            )

    async def stop(self):
        """优雅关闭 Bot"""
        if self.app:
            await self.app.stop()
```

## 命令处理器

### 导航命令
```python
# src/bot/handlers/command.py
"""
Bot 操作的命令处理器
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from pathlib import Path

async def list_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /ls 命令"""
    deps = context.user_data['deps']
    session_manager = deps['session_manager']
    security_validator = deps['security_validator']

    # 获取用户会话
    user_id = update.effective_user.id
    session = session_manager.get_session(user_id)

    try:
        # 列出目录内容
        items = []
        for item in sorted(session.current_directory.iterdir()):
            if item.name.startswith('.'):
                continue  # 跳过隐藏文件

            if item.is_dir():
                items.append(f"📁 {item.name}/")
            else:
                # 获取文件大小
                size = item.stat().st_size
                size_str = _format_file_size(size)
                items.append(f"📄 {item.name} ({size_str})")

        # 格式化响应
        if not items:
            message = f"📂 `{session.current_directory.name}/`\n\n_(empty directory)_"
        else:
            current_path = session.current_directory.relative_to(deps['config'].approved_directory)
            message = f"📂 `{current_path}/`\n\n"

            # 限制显示条目数
            max_items = 50
            if len(items) > max_items:
                shown_items = items[:max_items]
                message += "\n".join(shown_items)
                message += f"\n\n_... and {len(items) - max_items} more items_"
            else:
                message += "\n".join(items)

        await update.message.reply_text(message, parse_mode='Markdown')

        # 记录命令
        await deps['audit_logger'].log_command(user_id, 'ls', [], True)

    except Exception as e:
        await update.message.reply_text(f"❌ Error listing directory: {str(e)}")
        await deps['audit_logger'].log_command(user_id, 'ls', [], False)

async def change_directory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /cd 命令"""
    deps = context.user_data['deps']
    session_manager = deps['session_manager']
    security_validator = deps['security_validator']

    user_id = update.effective_user.id
    session = session_manager.get_session(user_id)

    # 解析参数
    if not context.args:
        await update.message.reply_text(
            "Usage: `/cd <directory>`\n"
            "Examples:\n"
            "• `/cd myproject` - Enter subdirectory\n"
            "• `/cd ..` - Go up one level\n"
            "• `/cd /` - Go to root of approved directory",
            parse_mode='Markdown'
        )
        return

    target_path = ' '.join(context.args)

    # 校验路径
    valid, resolved_path, error = security_validator.validate_path(
        target_path,
        session.current_directory
    )

    if not valid:
        await update.message.reply_text(f"❌ {error}")
        await deps['audit_logger'].log_security_violation(
            user_id, 'path_traversal', f"Attempted: {target_path}"
        )
        return

    # 检查目录是否存在
    if not resolved_path.exists():
        await update.message.reply_text(f"❌ Directory not found: `{target_path}`", parse_mode='Markdown')
        return

    if not resolved_path.is_dir():
        await update.message.reply_text(f"❌ Not a directory: `{target_path}`", parse_mode='Markdown')
        return

    # 更新会话
    session.current_directory = resolved_path
    session.claude_session_id = None  # 切换目录时清除 Claude 会话

    # 发送确认
    relative_path = resolved_path.relative_to(deps['config'].approved_directory)
    await update.message.reply_text(
        f"✅ Changed directory to: `{relative_path}/`\n"
        f"Claude session cleared. Send a message to start new session.",
        parse_mode='Markdown'
    )

    await deps['audit_logger'].log_command(user_id, 'cd', [target_path], True)
```

### 会话命令
```python
async def new_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /new 命令"""
    deps = context.user_data['deps']
    session_manager = deps['session_manager']

    user_id = update.effective_user.id
    session = session_manager.get_session(user_id)

    # 清除 Claude 会话
    session.claude_session_id = None

    # 显示确认信息和当前目录
    relative_path = session.current_directory.relative_to(deps['config'].approved_directory)

    keyboard = [[
        InlineKeyboardButton("📝 Start coding", callback_data="action:start_coding"),
        InlineKeyboardButton("📁 Change project", callback_data="action:show_projects")
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f"🆕 New Claude Code session\n\n"
        f"📂 Working directory: `{relative_path}/`\n\n"
        f"Send me a message to start coding, or:",
        parse_mode='Markdown',
        reply_markup=reply_markup
    )

async def session_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /status 命令"""
    deps = context.user_data['deps']
    session_manager = deps['session_manager']
    rate_limiter = deps['rate_limiter']

    user_id = update.effective_user.id
    session = session_manager.get_session(user_id)

    # 获取会话信息
    has_claude_session = session.claude_session_id is not None
    relative_path = session.current_directory.relative_to(deps['config'].approved_directory)

    # 获取使用情况
    user_cost = rate_limiter.cost_tracker.get(user_id, 0.0)
    cost_limit = deps['config'].claude_max_cost_per_user
    cost_percentage = (user_cost / cost_limit) * 100

    # 格式化状态消息
    status_lines = [
        "📊 **Session Status**",
        "",
        f"📂 Directory: `{relative_path}/`",
        f"🤖 Claude Session: {'✅ Active' if has_claude_session else '❌ None'}",
        f"💰 Usage: ${user_cost:.2f} / ${cost_limit:.2f} ({cost_percentage:.0f}%)",
        f"⏰ Last Activity: {session.last_activity.strftime('%H:%M:%S')}",
    ]

    if has_claude_session:
        status_lines.append(f"🆔 Session ID: `{session.claude_session_id[:8]}...`")

    # 添加操作按钮
    keyboard = []
    if has_claude_session:
        keyboard.append([
            InlineKeyboardButton("🔄 Continue session", callback_data="action:continue"),
            InlineKeyboardButton("🆕 New session", callback_data="action:new")
        ])
    keyboard.append([
        InlineKeyboardButton("📤 Export session", callback_data="action:export"),
        InlineKeyboardButton("🔄 Refresh", callback_data="action:refresh_status")
    ])

    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "\n".join(status_lines),
        parse_mode='Markdown',
        reply_markup=reply_markup
    )
```

## 消息处理器

### 文本消息处理器
```python
# src/bot/handlers/message.py
"""
非命令输入的消息处理器
"""

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """将普通文本消息作为 Claude 提示词处理"""
    deps = context.user_data['deps']
    session_manager = deps['session_manager']
    claude_integration = deps['claude_integration']
    rate_limiter = deps['rate_limiter']

    user_id = update.effective_user.id
    session = session_manager.get_session(user_id)
    message_text = update.message.text

    # 检查限流，估算费用
    estimated_cost = 0.001  # 基础费用估算
    allowed, limit_message = await rate_limiter.check_rate_limit(user_id, estimated_cost)

    if not allowed:
        await update.message.reply_text(f"⏱️ {limit_message}")
        return

    # 发送正在输入的提示
    await update.message.chat.send_action('typing')

    # 创建进度消息
    progress_msg = await update.message.reply_text(
        "🤔 Thinking...",
        reply_to_message_id=update.message.message_id
    )

    try:
        # 运行 Claude Code
        result = await claude_integration.run_command(
            prompt=message_text,
            working_directory=session.current_directory,
            session_id=session.claude_session_id,
            on_stream=lambda msg: _update_progress(progress_msg, msg)
        )

        # 删除进度消息
        await progress_msg.delete()

        # 更新会话
        session.claude_session_id = result.session_id

        # 格式化并发送响应
        formatter = ResponseFormatter(deps['config'])
        messages = formatter.format_claude_response(result.content)

        for msg in messages:
            await update.message.reply_text(
                msg.text,
                parse_mode=msg.parse_mode,
                reply_markup=msg.reply_markup
            )

        # 发送元数据
        await _send_metadata(update, result)

        # 更新费用追踪
        await rate_limiter.track_cost(user_id, result.cost)

    except asyncio.TimeoutError:
        await progress_msg.edit_text("❌ Operation timed out. Try a simpler request.")
    except Exception as e:
        await progress_msg.edit_text(f"❌ Error: {str(e)}")
        logger.exception("Error handling text message")
```

### 文档处理器
```python
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理文件上传"""
    deps = context.user_data['deps']
    security_validator = deps['security_validator']

    document = update.message.document

    # 校验文件名
    valid, error = security_validator.validate_filename(document.file_name)
    if not valid:
        await update.message.reply_text(f"❌ {error}")
        return

    # 检查文件大小
    max_size = 10 * 1024 * 1024  # 10MB
    if document.file_size > max_size:
        await update.message.reply_text(
            f"❌ File too large. Maximum size: {max_size // 1024 // 1024}MB"
        )
        return

    # 下载文件
    try:
        file = await document.get_file()
        file_bytes = await file.download_as_bytearray()

        # 尝试按文本解码
        try:
            content = file_bytes.decode('utf-8')
        except UnicodeDecodeError:
            await update.message.reply_text("❌ File must be text-based (UTF-8)")
            return

        # 构建带文件内容的提示词
        caption = update.message.caption or "Review this file:"
        prompt = f"{caption}\n\nFile: {document.file_name}\n```\n{content}\n```"

        # 作为普通消息处理
        update.message.text = prompt
        await handle_text_message(update, context)

    except Exception as e:
        await update.message.reply_text(f"❌ Error processing file: {str(e)}")
```

## 回调查询处理器

### 内联键盘操作
```python
# src/bot/handlers/callback.py
"""
处理内联键盘回调
"""

async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """将回调查询路由到对应的处理器"""
    query = update.callback_query
    await query.answer()  # 确认回调

    data = query.data
    deps = context.user_data['deps']

    # 解析回调数据
    if ':' in data:
        action, param = data.split(':', 1)
    else:
        action, param = data, None

    # 路由到对应的处理器
    handlers = {
        'cd': handle_cd_callback,
        'action': handle_action_callback,
        'confirm': handle_confirm_callback,
        'quick': handle_quick_action_callback,
    }

    handler = handlers.get(action)
    if handler:
        await handler(query, param, deps)
    else:
        await query.edit_message_text("❌ Unknown action")

async def handle_cd_callback(query, project_name, deps):
    """处理内联键盘中的项目选择"""
    session_manager = deps['session_manager']
    security_validator = deps['security_validator']

    user_id = query.from_user.id
    session = session_manager.get_session(user_id)

    # 校验并切换目录
    new_path = deps['config'].approved_directory / project_name

    if new_path.exists() and new_path.is_dir():
        session.current_directory = new_path
        session.claude_session_id = None

        await query.edit_message_text(
            f"✅ Changed to project: `{project_name}/`\n\n"
            f"Claude session cleared. Send a message to start coding.",
            parse_mode='Markdown'
        )
    else:
        await query.edit_message_text("❌ Project not found")

async def handle_quick_action_callback(query, action_type, deps):
    """处理快捷操作按钮"""
    quick_actions = {
        'test': "Run all tests in the current directory",
        'install': "Install dependencies (npm install or pip install)",
        'format': "Format all code files",
        'lint': "Run linter on all files",
        'git_status': "Show git status",
        'find_todos': "Find all TODO comments in the codebase",
    }

    prompt = quick_actions.get(action_type)
    if prompt:
        # 模拟发送提示词
        query.message.text = prompt
        await handle_text_message(query, {'user_data': {'deps': deps}})
```

## 响应格式化

### 消息格式化器
```python
# src/bot/utils/formatting.py
"""
格式化 Bot 响应以获得最佳展示效果
"""

from dataclasses import dataclass
from typing import List, Optional
import re

@dataclass
class FormattedMessage:
    text: str
    parse_mode: str = 'Markdown'
    reply_markup: Optional[Any] = None

class ResponseFormatter:
    """将 Claude 响应格式化为 Telegram 消息"""

    def __init__(self, config: Settings):
        self.config = config
        self.max_message_length = 4000

    def format_claude_response(self, text: str) -> List[FormattedMessage]:
        """将 Claude 响应格式化为 Telegram 消息"""
        # 处理代码块
        text = self._format_code_blocks(text)

        # 分割长消息
        messages = self._split_message(text)

        # 如果启用了快捷操作，在最后一条消息添加
        if self.config.enable_quick_actions and messages:
            messages[-1].reply_markup = self._get_quick_actions_keyboard()

        return messages

    def _format_code_blocks(self, text: str) -> str:
        """确保代码块格式正确"""
        # 将三反引号转为 Telegram 格式
        # 处理语言标识
        pattern = r'```(\w+)?\n(.*?)```'

        def replace_code_block(match):
            lang = match.group(1) or ''
            code = match.group(2)

            # Telegram 不支持代码块中的语言标识
            # 但可以作为注释添加
            if lang:
                return f"```\n# {lang}\n{code}```"
            return f"```\n{code}```"

        return re.sub(pattern, replace_code_block, text, flags=re.DOTALL)

    def _split_message(self, text: str) -> List[FormattedMessage]:
        """分割长消息并保持格式完整"""
        if len(text) <= self.max_message_length:
            return [FormattedMessage(text)]

        messages = []
        current = []
        current_length = 0
        in_code_block = False

        for line in text.split('\n'):
            line_length = len(line) + 1

            # 检查代码块标记
            if line.strip() == '```':
                in_code_block = not in_code_block

            # 检查添加行后是否超出限制
            if current_length + line_length > self.max_message_length:
                # 必要时关闭代码块
                if in_code_block:
                    current.append('```')

                # 保存当前消息
                messages.append(FormattedMessage('\n'.join(current)))

                # 开始新消息
                current = []
                current_length = 0

                # 必要时重新打开代码块
                if in_code_block:
                    current.append('```')
                    current_length = 4

            current.append(line)
            current_length += line_length

        # 添加剩余内容
        if current:
            messages.append(FormattedMessage('\n'.join(current)))

        return messages

    def _get_quick_actions_keyboard(self):
        """获取快捷操作内联键盘"""
        keyboard = [
            [
                InlineKeyboardButton("🧪 Run tests", callback_data="quick:test"),
                InlineKeyboardButton("📦 Install deps", callback_data="quick:install")
            ],
            [
                InlineKeyboardButton("🎨 Format code", callback_data="quick:format"),
                InlineKeyboardButton("🔍 Find TODOs", callback_data="quick:find_todos")
            ]
        ]

        return InlineKeyboardMarkup(keyboard)
```

## 错误处理

### 全局错误处理器
```python
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """全局错误处理"""
    logger.error(f"Exception while handling update {update}: {context.error}")

    # 通知用户
    if update and update.effective_message:
        error_messages = {
            RateLimitError: "⏱️ Rate limit exceeded. Please wait a moment.",
            SecurityError: "🔒 Security violation detected.",
            ClaudeError: "🤖 Error communicating with Claude.",
            asyncio.TimeoutError: "⏰ Operation timed out.",
        }

        error_type = type(context.error)
        message = error_messages.get(error_type, "❌ An unexpected error occurred.")

        try:
            await update.effective_message.reply_text(message)
        except Exception:
            # 发送错误消息时出错 - 仅记录日志
            logger.exception("Error sending error message to user")

    # 上报到监控
    if context.user_data.get('deps', {}).get('monitoring'):
        await context.user_data['deps']['monitoring'].report_error(
            error=context.error,
            update=update,
            context=context
        )
```

## 验收标准

- [ ] Bot 成功连接到 Telegram
- [ ] 所有命令正确注册并显示在菜单中
- [ ] 导航命令可正常工作并通过校验
- [ ] 会话命令正确管理 Claude 状态
- [ ] 文本消息触发 Claude 集成
- [ ] 文件上传经过校验并正确处理
- [ ] 内联键盘正常工作
- [ ] 响应格式化能处理长消息
- [ ] 代码块显示正确
- [ ] 错误处理提供有用的反馈
- [ ] 所有处理器正确注入依赖
- [ ] 中间件按正确顺序执行
- [ ] Bot 能处理并发用户
