> ⚠️ **归档说明**：本文档为历史设计/阶段性记录，可能与当前实现不一致。请以 README.md、AGENTS.md、docs/development.md、docs/configuration.md 为准。

# TODO-7: 高级功能

## 目标
实现增强用户体验的高级功能，包括文件上传处理、Git 集成、快捷操作、会话导出和图片/截图支持。

## 功能拆分

### 1. 增强文件上传处理

#### 多文件支持
```python
# src/bot/features/file_handler.py
"""
高级文件处理

功能：
- 多文件处理
- Zip 压缩包解压
- 代码分析
- Diff 生成
"""

class FileHandler:
    """处理各种文件操作"""

    def __init__(self, config: Settings, security: SecurityValidator):
        self.config = config
        self.security = security
        self.temp_dir = Path("/tmp/claude_bot_files")
        self.temp_dir.mkdir(exist_ok=True)

    async def handle_document_upload(
        self,
        document: Document,
        user_id: int,
        context: str = ""
    ) -> ProcessedFile:
        """处理上传的文档"""

        # 下载文件
        file_path = await self._download_file(document)

        try:
            # 检测文件类型
            file_type = self._detect_file_type(file_path)

            # 根据类型进行处理
            if file_type == 'archive':
                return await self._process_archive(file_path, context)
            elif file_type == 'code':
                return await self._process_code_file(file_path, context)
            elif file_type == 'text':
                return await self._process_text_file(file_path, context)
            else:
                raise ValueError(f"不支持的文件类型: {file_type}")

        finally:
            # 清理
            file_path.unlink(missing_ok=True)

    async def _process_archive(self, archive_path: Path, context: str) -> ProcessedFile:
        """解压并分析压缩包内容"""
        import zipfile
        import tarfile

        # 创建解压目录
        extract_dir = self.temp_dir / f"extract_{uuid.uuid4()}"
        extract_dir.mkdir()

        try:
            # 根据类型解压
            if archive_path.suffix == '.zip':
                with zipfile.ZipFile(archive_path) as zf:
                    # 安全检查 - 防止 zip 炸弹
                    total_size = sum(f.file_size for f in zf.filelist)
                    if total_size > 100 * 1024 * 1024:  # 100MB 限制
                        raise ValueError("压缩包过大")

                    zf.extractall(extract_dir)

            # 分析内容
            file_tree = self._build_file_tree(extract_dir)
            code_files = self._find_code_files(extract_dir)

            # 创建分析提示词
            prompt = f"{context}\n\n项目结构:\n{file_tree}\n\n"

            # 添加关键文件
            for file_path in code_files[:5]:  # 限制为 5 个文件
                content = file_path.read_text(encoding='utf-8', errors='ignore')
                prompt += f"\n文件: {file_path.relative_to(extract_dir)}\n```\n{content[:1000]}...\n```\n"

            return ProcessedFile(
                type='archive',
                prompt=prompt,
                metadata={
                    'file_count': len(list(extract_dir.rglob('*'))),
                    'code_files': len(code_files)
                }
            )

        finally:
            # 清理
            shutil.rmtree(extract_dir, ignore_errors=True)

    def _build_file_tree(self, directory: Path, prefix: str = "") -> str:
        """构建可视化文件树"""
        items = sorted(directory.iterdir(), key=lambda x: (x.is_file(), x.name))
        tree_lines = []

        for i, item in enumerate(items):
            is_last = i == len(items) - 1
            current_prefix = "└── " if is_last else "├── "

            if item.is_dir():
                tree_lines.append(f"{prefix}{current_prefix}{item.name}/")
                # 递归调用，更新前缀
                sub_prefix = prefix + ("    " if is_last else "│   ")
                tree_lines.append(self._build_file_tree(item, sub_prefix))
            else:
                size = item.stat().st_size
                tree_lines.append(f"{prefix}{current_prefix}{item.name} ({self._format_size(size)})")

        return "\n".join(filter(None, tree_lines))
```

#### 代码分析功能
```python
async def analyze_codebase(self, directory: Path) -> CodebaseAnalysis:
    """分析整个代码库"""

    analysis = CodebaseAnalysis()

    # 语言检测
    language_stats = defaultdict(int)
    file_extensions = defaultdict(int)

    for file_path in directory.rglob('*'):
        if file_path.is_file():
            ext = file_path.suffix.lower()
            file_extensions[ext] += 1

            language = self._detect_language(ext)
            if language:
                language_stats[language] += 1

    # 查找入口文件
    entry_points = self._find_entry_points(directory)

    # 检测框架
    frameworks = self._detect_frameworks(directory)

    # 查找 TODO 和 FIXME
    todos = await self._find_todos(directory)

    # 检查测试
    test_files = self._find_test_files(directory)

    return CodebaseAnalysis(
        languages=dict(language_stats),
        frameworks=frameworks,
        entry_points=entry_points,
        todo_count=len(todos),
        test_coverage=len(test_files) > 0,
        file_stats=dict(file_extensions)
    )
```

### 2. Git 集成

#### Git 命令
```python
# src/bot/features/git_integration.py
"""
Git 版本控制集成

功能：
- 状态检查
- Diff 查看
- 分支管理
- 提交历史
"""

class GitIntegration:
    """处理 Git 操作"""

    def __init__(self, security: SecurityValidator):
        self.security = security

    async def get_status(self, repo_path: Path) -> GitStatus:
        """获取仓库状态"""
        if not (repo_path / '.git').exists():
            raise ValueError("不是 git 仓库")

        # 执行 git status
        result = await self._run_git_command(['status', '--porcelain'], repo_path)

        # 解析状态
        changes = self._parse_status(result)

        # 获取当前分支
        branch = await self._get_current_branch(repo_path)

        # 获取最近提交
        commits = await self._get_recent_commits(repo_path, limit=5)

        return GitStatus(
            branch=branch,
            changes=changes,
            recent_commits=commits,
            has_changes=len(changes) > 0
        )

    async def get_diff(self, repo_path: Path, staged: bool = False) -> str:
        """获取变更差异"""
        cmd = ['diff']
        if staged:
            cmd.append('--staged')

        diff = await self._run_git_command(cmd, repo_path)

        # 格式化显示
        return self._format_diff(diff)

    async def get_file_history(self, repo_path: Path, file_path: str) -> List[CommitInfo]:
        """获取文件的提交历史"""
        cmd = ['log', '--follow', '--pretty=format:%H|%an|%ae|%ai|%s', '--', file_path]

        result = await self._run_git_command(cmd, repo_path)

        commits = []
        for line in result.strip().split('\n'):
            if line:
                parts = line.split('|')
                if len(parts) >= 5:
                    commits.append(CommitInfo(
                        hash=parts[0],
                        author=parts[1],
                        email=parts[2],
                        date=parts[3],
                        message=parts[4]
                    ))

        return commits

    async def _run_git_command(self, args: List[str], cwd: Path) -> str:
        """安全执行 git 命令"""
        # 安全检查 - 只允许安全的 git 命令
        safe_commands = ['status', 'diff', 'log', 'branch', 'remote', 'show']
        if args[0] not in safe_commands:
            raise SecurityError(f"不允许的 git 命令: {args[0]}")

        cmd = ['git'] + args

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd)
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            raise GitError(f"Git 命令执行失败: {stderr.decode()}")

        return stdout.decode()

    def _format_diff(self, diff: str) -> str:
        """格式化 diff 以适配 Telegram 显示"""
        lines = diff.split('\n')
        formatted = []

        for line in lines[:100]:  # 限制输出
            if line.startswith('+'):
                formatted.append(f"+ {line[1:]}")
            elif line.startswith('-'):
                formatted.append(f"- {line[1:]}")
            elif line.startswith('@@'):
                formatted.append(f"@@ {line}")
            else:
                formatted.append(line)

        if len(lines) > 100:
            formatted.append(f"\n... 还有 {len(lines) - 100} 行")

        return '\n'.join(formatted)
```

### 3. 快捷操作系统

#### 操作定义
```python
# src/bot/features/quick_actions.py
"""
常用任务快捷操作系统

功能：
- 预定义操作
- 自定义操作
- 上下文感知建议
"""

@dataclass
class QuickAction:
    """快捷操作定义"""
    id: str
    name: str
    icon: str
    prompt: str
    requires_confirmation: bool = False
    context_requirements: List[str] = None

class QuickActionManager:
    """管理快捷操作"""

    def __init__(self):
        self.actions = self._load_default_actions()

    def _load_default_actions(self) -> Dict[str, QuickAction]:
        """加载默认快捷操作"""
        return {
            'test': QuickAction(
                id='test',
                name='运行测试',
                icon='🧪',
                prompt='运行当前目录下的所有测试并显示结果',
                context_requirements=['test_framework']
            ),
            'install': QuickAction(
                id='install',
                name='安装依赖',
                icon='📦',
                prompt='根据包管理文件安装项目依赖',
                context_requirements=['package_file']
            ),
            'format': QuickAction(
                id='format',
                name='格式化代码',
                icon='🎨',
                prompt='使用合适的格式化工具格式化所有代码文件'
            ),
            'lint': QuickAction(
                id='lint',
                name='代码检查',
                icon='🔍',
                prompt='运行代码检查工具并显示问题'
            ),
            'security': QuickAction(
                id='security',
                name='安全检查',
                icon='🔒',
                prompt='检查依赖中的安全漏洞'
            ),
            'optimize': QuickAction(
                id='optimize',
                name='优化代码',
                icon='⚡',
                prompt='分析并建议当前代码的优化方案'
            ),
            'document': QuickAction(
                id='document',
                name='添加文档',
                icon='📝',
                prompt='为当前代码添加或改进文档'
            ),
            'refactor': QuickAction(
                id='refactor',
                name='重构代码',
                icon='🔧',
                prompt='建议重构改进以提升代码质量'
            )
        }

    async def get_context_actions(self, directory: Path) -> List[QuickAction]:
        """获取当前上下文可用的操作"""
        available = []

        # 检查上下文
        context = await self._analyze_context(directory)

        for action in self.actions.values():
            if self._is_action_available(action, context):
                available.append(action)

        return available

    async def _analyze_context(self, directory: Path) -> Dict[str, bool]:
        """分析目录上下文"""
        context = {
            'test_framework': False,
            'package_file': False,
            'git_repo': False,
            'has_code': False
        }

        # 检查测试框架
        test_indicators = ['pytest.ini', 'jest.config.js', 'test/', 'tests/', '__tests__']
        for indicator in test_indicators:
            if (directory / indicator).exists():
                context['test_framework'] = True
                break

        # 检查包管理文件
        package_files = ['package.json', 'requirements.txt', 'Pipfile', 'Cargo.toml', 'go.mod']
        for pf in package_files:
            if (directory / pf).exists():
                context['package_file'] = True
                break

        # 检查 git
        context['git_repo'] = (directory / '.git').exists()

        # 检查代码文件
        code_extensions = {'.py', '.js', '.ts', '.java', '.cpp', '.go', '.rs'}
        for file in directory.iterdir():
            if file.suffix in code_extensions:
                context['has_code'] = True
                break

        return context

    def create_action_keyboard(self, actions: List[QuickAction]) -> InlineKeyboardMarkup:
        """创建操作的内联键盘"""
        keyboard = []

        # 每行 2 个按钮
        for i in range(0, len(actions), 2):
            row = []
            for j in range(2):
                if i + j < len(actions):
                    action = actions[i + j]
                    row.append(InlineKeyboardButton(
                        f"{action.icon} {action.name}",
                        callback_data=f"quick:{action.id}"
                    ))
            keyboard.append(row)

        return InlineKeyboardMarkup(keyboard)
```

### 4. 会话导出功能

#### 导出格式
```python
# src/bot/features/session_export.py
"""
多格式导出 Claude 会话

功能：
- Markdown 导出
- JSON 导出
- HTML 导出
- PDF 生成
"""

class SessionExporter:
    """多格式会话导出"""

    def __init__(self, storage: Storage):
        self.storage = storage

    async def export_session(
        self,
        session_id: str,
        format: str = 'markdown'
    ) -> ExportedSession:
        """按指定格式导出会话"""

        # 加载会话数据
        session = await self.storage.sessions.get_session(session_id)
        if not session:
            raise ValueError("会话未找到")

        # 加载消息
        messages = await self.storage.messages.get_session_messages(session_id)

        # 根据格式导出
        if format == 'markdown':
            content = self._export_markdown(session, messages)
            filename = f"claude_session_{session_id[:8]}.md"
        elif format == 'json':
            content = self._export_json(session, messages)
            filename = f"claude_session_{session_id[:8]}.json"
        elif format == 'html':
            content = self._export_html(session, messages)
            filename = f"claude_session_{session_id[:8]}.html"
        else:
            raise ValueError(f"不支持的格式: {format}")

        return ExportedSession(
            content=content,
            filename=filename,
            format=format,
            size=len(content.encode('utf-8'))
        )

    def _export_markdown(self, session: SessionModel, messages: List[MessageModel]) -> str:
        """导出为 Markdown"""
        lines = []

        # 头部
        lines.append(f"# Claude Code 会话导出")
        lines.append(f"\n**会话 ID:** `{session.session_id}`")
        lines.append(f"**项目:** `{session.project_path}`")
        lines.append(f"**创建时间:** {session.created_at.isoformat()}")
        lines.append(f"**消息数:** {len(messages)}")
        lines.append(f"**总费用:** ${session.total_cost:.4f}")
        lines.append("\n---\n")

        # 消息
        for msg in reversed(messages):  # 按时间顺序
            lines.append(f"## 用户 ({msg.timestamp.strftime('%H:%M:%S')})")
            lines.append(f"\n{msg.prompt}\n")

            if msg.response:
                lines.append(f"## Claude")
                lines.append(f"\n{msg.response}\n")

                if msg.cost > 0:
                    lines.append(f"*费用: ${msg.cost:.4f} | 耗时: {msg.duration_ms}ms*")

            lines.append("\n---\n")

        return '\n'.join(lines)

    def _export_html(self, session: SessionModel, messages: List[MessageModel]) -> str:
        """导出为带样式的 HTML"""
        template = """
<!DOCTYPE html>
<html>
<head>
    <title>Claude Code 会话 - {session_id}</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                max-width: 800px; margin: 0 auto; padding: 20px; }}
        .header {{ background: #f0f0f0; padding: 20px; border-radius: 8px; margin-bottom: 20px; }}
        .message {{ margin: 20px 0; padding: 15px; border-radius: 8px; }}
        .user {{ background: #e3f2fd; }}
        .assistant {{ background: #f5f5f5; }}
        .timestamp {{ color: #666; font-size: 0.9em; }}
        .cost {{ color: #666; font-size: 0.9em; font-style: italic; }}
        pre {{ background: #272822; color: #f8f8f2; padding: 10px; border-radius: 4px; overflow-x: auto; }}
        code {{ background: #f0f0f0; padding: 2px 4px; border-radius: 3px; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>Claude Code 会话导出</h1>
        <p><strong>会话 ID:</strong> <code>{session_id}</code></p>
        <p><strong>项目:</strong> <code>{project_path}</code></p>
        <p><strong>创建时间:</strong> {created}</p>
        <p><strong>总费用:</strong> ${total_cost:.4f}</p>
    </div>

    {messages_html}
</body>
</html>
        """

        messages_html = []
        for msg in reversed(messages):
            msg_html = f"""
            <div class="message user">
                <div class="timestamp">用户 - {msg.timestamp.strftime('%H:%M:%S')}</div>
                <div>{self._markdown_to_html(msg.prompt)}</div>
            </div>
            """

            if msg.response:
                msg_html += f"""
                <div class="message assistant">
                    <div class="timestamp">Claude</div>
                    <div>{self._markdown_to_html(msg.response)}</div>
                    <div class="cost">费用: ${msg.cost:.4f} | 耗时: {msg.duration_ms}ms</div>
                </div>
                """

            messages_html.append(msg_html)

        return template.format(
            session_id=session.session_id,
            project_path=session.project_path,
            created=session.created_at.isoformat(),
            total_cost=session.total_cost,
            messages_html='\n'.join(messages_html)
        )
```

### 5. 图片/截图支持

#### 图片处理
```python
# src/bot/features/image_handler.py
"""
处理图片上传用于 UI/截图分析

功能：
- OCR 文字提取
- UI 元素检测
- 图片描述
- 图表分析
"""

class ImageHandler:
    """处理图片上传"""

    def __init__(self, config: Settings):
        self.config = config
        self.supported_formats = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}

    async def process_image(
        self,
        photo: PhotoSize,
        caption: Optional[str] = None
    ) -> ProcessedImage:
        """处理上传的图片"""

        # 下载图片
        file = await photo.get_file()
        image_bytes = await file.download_as_bytearray()

        # 检测图片类型
        image_type = self._detect_image_type(image_bytes)

        # 创建合适的提示词
        if image_type == 'screenshot':
            prompt = self._create_screenshot_prompt(caption)
        elif image_type == 'diagram':
            prompt = self._create_diagram_prompt(caption)
        elif image_type == 'ui_mockup':
            prompt = self._create_ui_prompt(caption)
        else:
            prompt = self._create_generic_prompt(caption)

        # 转换为 base64（如后续 Claude 支持）
        base64_image = base64.b64encode(image_bytes).decode('utf-8')

        return ProcessedImage(
            prompt=prompt,
            image_type=image_type,
            base64_data=base64_image,
            size=len(image_bytes)
        )

    def _detect_image_type(self, image_bytes: bytes) -> str:
        """检测图片类型"""
        # 基于图片特征的简单启发式判断
        # 实际使用中可用 ML 模型提升检测精度

        # 目前返回通用类型
        return 'screenshot'

    def _create_screenshot_prompt(self, caption: Optional[str]) -> str:
        """创建截图分析提示词"""
        base_prompt = """我分享了一张截图。请帮我分析：

1. 识别这是什么应用或网站
2. 理解 UI 元素及其用途
3. 发现的任何问题或改进建议
4. 回答我的具体问题

"""
        if caption:
            base_prompt += f"具体需求: {caption}"

        return base_prompt
```

### 6. 交互增强功能

#### 对话模式
```python
# src/bot/features/conversation_mode.py
"""
增强对话功能

功能：
- 上下文保持
- 后续建议
- 代码执行追踪
"""

class ConversationEnhancer:
    """增强对话体验"""

    def __init__(self):
        self.conversation_contexts = {}

    def generate_follow_up_suggestions(
        self,
        response: ClaudeResponse,
        context: ConversationContext
    ) -> List[str]:
        """生成相关的后续建议"""
        suggestions = []

        # 基于使用的工具
        if 'create_file' in [t['name'] for t in response.tools_used]:
            suggestions.append("为新代码添加测试")
            suggestions.append("创建文档")

        if 'edit_file' in [t['name'] for t in response.tools_used]:
            suggestions.append("审查变更")
            suggestions.append("运行测试验证")

        # 基于内容
        if 'error' in response.content.lower():
            suggestions.append("帮我调试这个错误")
            suggestions.append("建议替代方案")

        if 'todo' in response.content.lower():
            suggestions.append("完成 TODO 项")
            suggestions.append("排列任务优先级")

        return suggestions[:3]  # 限制为 3 个建议

    def create_follow_up_keyboard(self, suggestions: List[str]) -> InlineKeyboardMarkup:
        """创建后续建议的键盘"""
        keyboard = []

        for suggestion in suggestions:
            keyboard.append([InlineKeyboardButton(
                f"{suggestion}",
                callback_data=f"followup:{hash(suggestion) % 1000000}"
            )])

        keyboard.append([InlineKeyboardButton(
            "完成",
            callback_data="conversation:end"
        )])

        return InlineKeyboardMarkup(keyboard)
```

## 集成点

### 功能注册中心
```python
# src/bot/features/registry.py
"""
集中的功能注册和管理
"""

class FeatureRegistry:
    """管理所有 bot 功能"""

    def __init__(self, config: Settings, deps: Dict[str, Any]):
        self.config = config
        self.deps = deps
        self.features = {}

        # 根据配置初始化功能
        self._initialize_features()

    def _initialize_features(self):
        """初始化已启用的功能"""
        if self.config.enable_file_uploads:
            self.features['file_handler'] = FileHandler(
                self.config,
                self.deps['security']
            )

        if self.config.enable_git_integration:
            self.features['git'] = GitIntegration(
                self.deps['security']
            )

        if self.config.enable_quick_actions:
            self.features['quick_actions'] = QuickActionManager()

        self.features['session_export'] = SessionExporter(
            self.deps['storage']
        )

        self.features['image_handler'] = ImageHandler(self.config)

        self.features['conversation'] = ConversationEnhancer()

    def get_feature(self, name: str) -> Optional[Any]:
        """根据名称获取功能"""
        return self.features.get(name)

    def is_enabled(self, feature_name: str) -> bool:
        """检查功能是否已启用"""
        return feature_name in self.features
```

## 成功标准

- [ ] 文件上传处理正确，含安全验证
- [ ] 压缩包解压安全处理 zip/tar 文件
- [ ] Git 集成显示状态、差异和历史
- [ ] 快捷操作根据上下文显示
- [ ] 会话导出支持所有格式
- [ ] 图片上传创建合适的提示词
- [ ] 后续建议具有相关性
- [ ] 所有功能遵守安全边界
- [ ] 功能可通过配置开关控制
- [ ] 大文件场景下内存使用合理
- [ ] 错误处理提供清晰反馈
- [ ] 集成测试覆盖所有功能
