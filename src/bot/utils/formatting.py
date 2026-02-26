"""Format bot responses for optimal display."""

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, List, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from ...config.settings import Settings


@dataclass
class FormattedMessage:
    """Represents a formatted message for Telegram."""

    text: str
    parse_mode: str = "Markdown"
    reply_markup: Optional[InlineKeyboardMarkup] = None

    def __len__(self) -> int:
        """Return length of message text."""
        return len(self.text)


class ResponseFormatter:
    """Format Claude responses for Telegram display."""

    _INLINE_CODE_URL_PATTERN = re.compile(r"`\s*(https?://[^\s`]+)\s*`")
    _URL_PATTERN = re.compile(r"https?://[^\s<`]+")
    _THINKING_TAG_PATTERN = re.compile(
        r"<(?:antml:)?thinking>\s*[\s\S]*?</(?:antml:)?thinking>\s*",
        re.DOTALL,
    )
    # Matches a contiguous block of GFM-style table lines (header + separator + rows).
    _MD_TABLE_BLOCK_PATTERN = re.compile(
        r"(?:^[ \t]*\|.+\|[ \t]*\n){2,}",
        re.MULTILINE,
    )

    def __init__(self, settings: Settings):
        """Initialize formatter with settings."""
        self.settings = settings
        self.max_message_length = 4000  # Telegram limit is 4096, leave some buffer
        self.max_code_block_length = 3000  # Max length for code blocks

    def format_claude_response(
        self, text: str, context: Optional[dict] = None
    ) -> List[FormattedMessage]:
        """Enhanced formatting with context awareness and semantic chunking."""
        # Clean and prepare text
        text = self._clean_text(text)

        # Check if we need semantic chunking (for complex content)
        if self._should_use_semantic_chunking(text):
            # Use enhanced semantic chunking for complex content
            chunks = self._semantic_chunk(text, context)
            messages = []
            for chunk in chunks:
                formatted = self._format_chunk(chunk)
                messages.extend(formatted)
        else:
            # Use original simple formatting for basic content
            text = self._format_code_blocks(text)
            messages = self._split_message(text)

        # Add context-aware quick actions to the last message
        if messages and self.settings.enable_quick_actions:
            messages[-1].reply_markup = self._get_contextual_keyboard(context)

        return messages if messages else [FormattedMessage("_(No content to display)_")]

    def _should_use_semantic_chunking(self, text: str) -> bool:
        """Determine if semantic chunking is needed."""
        # Use semantic chunking for complex content with multiple code blocks,
        # file operations, or very long text
        code_block_count = text.count("```")
        has_file_operations = any(
            indicator in text
            for indicator in [
                "Creating file",
                "Editing file",
                "Reading file",
                "Writing to",
                "Modified file",
                "Deleted file",
                "File created",
                "File updated",
            ]
        )
        is_very_long = len(text) > self.max_message_length * 2

        return code_block_count > 2 or has_file_operations or is_very_long

    def format_error_message(
        self, error: str, error_type: str = "Error"
    ) -> FormattedMessage:
        """Format error message with appropriate styling."""
        icon = {
            "Error": "❌",
            "Warning": "⚠️",
            "Info": "ℹ️",
            "Security": "🛡️",
            "Rate Limit": "⏱️",
        }.get(error_type, "❌")

        text = f"{icon} **{error_type}**\n\n{error}"

        return FormattedMessage(text, parse_mode="Markdown")

    def format_success_message(
        self, message: str, title: str = "Success"
    ) -> FormattedMessage:
        """Format success message with appropriate styling."""
        text = f"✅ **{title}**\n\n{message}"
        return FormattedMessage(text, parse_mode="Markdown")

    def format_info_message(
        self, message: str, title: str = "Info"
    ) -> FormattedMessage:
        """Format info message with appropriate styling."""
        text = f"ℹ️ **{title}**\n\n{message}"
        return FormattedMessage(text, parse_mode="Markdown")

    def format_code_output(
        self, output: str, language: str = "", title: str = "Output"
    ) -> List[FormattedMessage]:
        """Format code output with syntax highlighting."""
        if not output.strip():
            return [FormattedMessage(f"📄 **{title}**\n\n_(empty output)_")]

        # Add language hint if provided
        code_block = (
            f"```{language}\n{output}\n```" if language else f"```\n{output}\n```"
        )

        # Check if the code block is too long
        if len(code_block) > self.max_code_block_length:
            # Truncate and add notice
            truncated = output[: self.max_code_block_length - 100]
            code_block = f"```{language}\n{truncated}\n... (output truncated)\n```"

        text = f"📄 **{title}**\n\n{code_block}"

        return self._split_message(text)

    def format_file_list(
        self, files: List[str], directory: str = ""
    ) -> FormattedMessage:
        """Format file listing with appropriate icons."""
        if not files:
            text = f"📂 **{directory}**\n\n_(empty directory)_"
        else:
            file_lines = []
            for file in files[:50]:  # Limit to 50 items
                if file.endswith("/"):
                    file_lines.append(f"📁 {file}")
                else:
                    file_lines.append(f"📄 {file}")

            file_text = "\n".join(file_lines)
            if len(files) > 50:
                file_text += f"\n\n_... and {len(files) - 50} more items_"

            text = f"📂 **{directory}**\n\n{file_text}"

        return FormattedMessage(text, parse_mode="Markdown")

    def format_progress_message(
        self, message: str, percentage: Optional[float] = None
    ) -> FormattedMessage:
        """Format progress message with optional progress bar."""
        if percentage is not None:
            # Create simple progress bar
            filled = int(percentage / 10)
            empty = 10 - filled
            progress_bar = "▓" * filled + "░" * empty
            text = f"🔄 **{message}**\n\n{progress_bar} {percentage:.0f}%"
        else:
            text = f"🔄 **{message}**"

        return FormattedMessage(text, parse_mode="Markdown")

    def _semantic_chunk(self, text: str, context: Optional[dict]) -> List[dict]:
        """Split text into semantic chunks based on content type."""
        chunks = []

        # Identify different content sections
        sections = self._identify_sections(text)

        for section in sections:
            if section["type"] == "code_block":
                chunks.extend(self._chunk_code_block(section))
            elif section["type"] == "explanation":
                chunks.extend(self._chunk_explanation(section))
            elif section["type"] == "file_operations":
                chunks.append(self._format_file_operations_section(section))
            elif section["type"] == "mixed":
                chunks.extend(self._chunk_mixed_content(section))
            else:
                # Default text chunking
                chunks.extend(self._chunk_text(section))

        return chunks

    def _identify_sections(self, text: str) -> List[dict]:
        """Identify different content types in the text."""
        sections = []
        lines = text.split("\n")
        current_section = {"type": "text", "content": "", "start_line": 0}
        in_code_block = False
        code_start = 0

        for i, line in enumerate(lines):
            # Check for code block markers
            if line.strip().startswith("```"):
                if not in_code_block:
                    # Start of code block
                    if current_section["content"].strip():
                        sections.append(current_section)
                    in_code_block = True
                    code_start = i
                    current_section = {
                        "type": "code_block",
                        "content": line + "\n",
                        "start_line": i,
                    }
                else:
                    # End of code block
                    current_section["content"] += line + "\n"
                    sections.append(current_section)
                    in_code_block = False
                    current_section = {
                        "type": "text",
                        "content": "",
                        "start_line": i + 1,
                    }
            elif in_code_block:
                current_section["content"] += line + "\n"
            else:
                # Check for file operation patterns
                if self._is_file_operation_line(line):
                    if current_section["type"] != "file_operations":
                        if current_section["content"].strip():
                            sections.append(current_section)
                        current_section = {
                            "type": "file_operations",
                            "content": line + "\n",
                            "start_line": i,
                        }
                    else:
                        current_section["content"] += line + "\n"
                else:
                    # Regular text
                    if current_section["type"] != "text":
                        if current_section["content"].strip():
                            sections.append(current_section)
                        current_section = {
                            "type": "text",
                            "content": line + "\n",
                            "start_line": i,
                        }
                    else:
                        current_section["content"] += line + "\n"

        # Add the last section
        if current_section["content"].strip():
            sections.append(current_section)

        return sections

    def _is_file_operation_line(self, line: str) -> bool:
        """Check if a line indicates file operations."""
        file_indicators = [
            "Creating file",
            "Editing file",
            "Reading file",
            "Writing to",
            "Modified file",
            "Deleted file",
            "File created",
            "File updated",
        ]
        return any(indicator in line for indicator in file_indicators)

    def _chunk_code_block(self, section: dict) -> List[dict]:
        """Handle code block chunking."""
        content = section["content"]
        if len(content) <= self.max_code_block_length:
            return [{"type": "code_block", "content": content, "format": "single"}]

        # Split large code blocks
        chunks = []
        lines = content.split("\n")
        current_chunk = lines[0] + "\n"  # Start with the ``` line

        for line in lines[1:-1]:  # Skip first and last ``` lines
            if len(current_chunk + line + "\n```\n") > self.max_code_block_length:
                current_chunk += "```"
                chunks.append(
                    {"type": "code_block", "content": current_chunk, "format": "split"}
                )
                current_chunk = "```\n" + line + "\n"
            else:
                current_chunk += line + "\n"

        current_chunk += lines[-1]  # Add the closing ```
        chunks.append(
            {"type": "code_block", "content": current_chunk, "format": "split"}
        )

        return chunks

    def _chunk_explanation(self, section: dict) -> List[dict]:
        """Handle explanation text chunking."""
        content = section["content"]
        if len(content) <= self.max_message_length:
            return [{"type": "explanation", "content": content}]

        # Split by paragraphs first
        paragraphs = content.split("\n\n")
        chunks = []
        current_chunk = ""

        for paragraph in paragraphs:
            if len(current_chunk + paragraph + "\n\n") > self.max_message_length:
                if current_chunk:
                    chunks.append(
                        {"type": "explanation", "content": current_chunk.strip()}
                    )
                current_chunk = paragraph + "\n\n"
            else:
                current_chunk += paragraph + "\n\n"

        if current_chunk:
            chunks.append({"type": "explanation", "content": current_chunk.strip()})

        return chunks

    def _chunk_mixed_content(self, section: dict) -> List[dict]:
        """Handle mixed content sections."""
        # For now, treat as regular text
        return self._chunk_text(section)

    def _chunk_text(self, section: dict) -> List[dict]:
        """Handle regular text chunking."""
        content = section["content"]
        if len(content) <= self.max_message_length:
            return [{"type": "text", "content": content}]

        # Split at natural break points
        chunks = []
        current_chunk = ""

        sentences = content.split(". ")
        for sentence in sentences:
            test_chunk = current_chunk + sentence + ". "
            if len(test_chunk) > self.max_message_length:
                if current_chunk:
                    chunks.append({"type": "text", "content": current_chunk.strip()})
                current_chunk = sentence + ". "
            else:
                current_chunk = test_chunk

        if current_chunk:
            chunks.append({"type": "text", "content": current_chunk.strip()})

        return chunks

    def _format_file_operations_section(self, section: dict) -> dict:
        """Format file operations section."""
        return {"type": "file_operations", "content": section["content"]}

    def _format_chunk(self, chunk: dict) -> List[FormattedMessage]:
        """Format individual chunks into FormattedMessage objects."""
        chunk_type = chunk["type"]
        content = chunk["content"]

        if chunk_type == "code_block":
            # Format code blocks with proper styling
            if chunk.get("format") == "split":
                title = (
                    "📄 **Code (continued)**"
                    if "continued" in content
                    else "📄 **Code**"
                )
            else:
                title = "📄 **Code**"

            text = f"{title}\n\n{content}"

        elif chunk_type == "file_operations":
            # Format file operations with icons
            text = f"📁 **File Operations**\n\n{content}"

        elif chunk_type == "explanation":
            # Regular explanation text
            text = content

        else:
            # Default text formatting
            text = content

        # Split if still too long
        return self._split_message(text)

    def _get_contextual_keyboard(
        self, context: Optional[dict]
    ) -> Optional[InlineKeyboardMarkup]:
        """Get context-aware quick action keyboard."""
        if not context:
            return self._get_quick_actions_keyboard()

        buttons = []

        # Add context-specific buttons
        if context.get("has_code"):
            buttons.append(
                [InlineKeyboardButton("💾 Save Code", callback_data="save_code")]
            )

        if context.get("has_file_operations"):
            buttons.append(
                [InlineKeyboardButton("📁 Show Files", callback_data="show_files")]
            )

        if context.get("has_errors"):
            buttons.append([InlineKeyboardButton("🔧 Debug", callback_data="debug")])

        # Add default actions
        default_buttons = [
            [InlineKeyboardButton("🔄 Continue", callback_data="continue")],
            [InlineKeyboardButton("💡 Explain", callback_data="explain")],
        ]
        buttons.extend(default_buttons)

        return InlineKeyboardMarkup(buttons) if buttons else None

    def _clean_text(self, text: str) -> str:
        """Clean text for Telegram display."""
        # Strip leaked thinking tags (e.g. <thinking>...</thinking>,
        # <thinking>...</thinking>) that may leak from model output.
        text = self._THINKING_TAG_PATTERN.sub("", text)

        # Convert GFM Markdown tables into fenced code blocks with box-drawing
        # characters so they render nicely in Telegram's monospace font.
        text = self._convert_markdown_tables(text)

        # Remove excessive whitespace
        text = re.sub(r"\n{3,}", "\n\n", text)

        # Convert URL-only inline code markers to plain links so Telegram can open them.
        text = self._unwrap_inline_code_urls(text)

        # Normalize common Markdown syntax from Claude/GFM output
        # into Telegram legacy Markdown-compatible markers.
        text = self._normalize_markdown_outside_code(text)

        # Escape special Markdown characters (but preserve intentional formatting)
        # Be careful not to escape characters inside code blocks
        text = self._escape_markdown_outside_code(text)

        return text.strip()

    def _convert_markdown_tables(self, text: str) -> str:
        """Convert GFM Markdown tables to box-drawing code blocks.

        Scans *outside* existing fenced code blocks for Markdown table syntax
        (``| col | col |``) and replaces each table with a ````` ``` ````` block
        using Unicode box-drawing characters for clean Telegram display.
        """
        parts: list[str] = []
        in_code_block = False
        buf: list[str] = []

        def _flush_buf() -> None:
            if not buf:
                return
            segment = "\n".join(buf)
            segment = self._MD_TABLE_BLOCK_PATTERN.sub(
                lambda m: self._render_table_block(m.group(0)), segment
            )
            parts.append(segment)
            buf.clear()

        for line in text.split("\n"):
            if line.strip().startswith("```"):
                if not in_code_block:
                    _flush_buf()
                in_code_block = not in_code_block
                parts.append(line)
            elif in_code_block:
                parts.append(line)
            else:
                buf.append(line)

        _flush_buf()
        return "\n".join(parts)

    @staticmethod
    def _render_table_block(raw: str) -> str:
        """Render a Markdown table string as a box-drawing code block."""
        lines = [ln.strip() for ln in raw.strip().splitlines()]
        # Parse cells per row, skip the separator line (|---|---|)
        rows: list[list[str]] = []
        for line in lines:
            cells = [c.strip() for c in line.strip("|").split("|")]
            # Detect separator row: all cells match  ---  or  :---:  etc.
            if all(re.fullmatch(r":?-{1,}:?", c) for c in cells):
                continue
            rows.append(cells)

        if not rows:
            return raw  # nothing to render

        # Determine column widths (respect wide chars like CJK)
        num_cols = max(len(r) for r in rows)
        # Pad rows that have fewer columns
        for r in rows:
            while len(r) < num_cols:
                r.append("")

        col_widths = []
        for ci in range(num_cols):
            col_widths.append(max(_display_width(r[ci]) for r in rows))

        def _pad(cell: str, width: int) -> str:
            """Pad *cell* to *width* accounting for wide characters."""
            pad = width - _display_width(cell)
            return cell + " " * pad

        # Build box-drawing table
        top = "\u250c" + "\u252c".join("\u2500" * (w + 2) for w in col_widths) + "\u2510"
        mid = "\u251c" + "\u253c".join("\u2500" * (w + 2) for w in col_widths) + "\u2524"
        bot = "\u2514" + "\u2534".join("\u2500" * (w + 2) for w in col_widths) + "\u2518"

        out_lines = [top]
        for ri, row in enumerate(rows):
            cells = " \u2502 ".join(_pad(row[ci], col_widths[ci]) for ci in range(num_cols))
            out_lines.append(f"\u2502 {cells} \u2502")
            if ri == 0 and len(rows) > 1:
                out_lines.append(mid)
        out_lines.append(bot)

        return "```\n" + "\n".join(out_lines) + "\n```"

    def _unwrap_inline_code_urls(self, text: str) -> str:
        """Remove inline-code backticks around URL-only tokens outside code blocks."""
        parts = []
        in_code_block = False

        for line in text.split("\n"):
            if line.strip().startswith("```"):
                in_code_block = not in_code_block
                parts.append(line)
                continue

            if in_code_block:
                parts.append(line)
                continue

            parts.append(
                self._INLINE_CODE_URL_PATTERN.sub(lambda m: m.group(1), line)
            )

        return "\n".join(parts)

    def _normalize_markdown_outside_code(self, text: str) -> str:
        """Normalize common Markdown markers outside of code blocks.

        Telegram legacy Markdown uses `*bold*` instead of `**bold**`.
        Also converts Markdown elements that Telegram does not support
        (headings, horizontal rules, strikethrough) into compatible formats.
        """

        def _normalize_segment(segment: str) -> str:
            # Convert GFM-style bold to Telegram legacy Markdown bold.
            segment = re.sub(
                r"\*\*(?=\S)(.+?)(?<=\S)\*\*",
                r"*\1*",
                segment,
            )
            # Convert strikethrough ~~text~~ to Telegram-compatible ~text~
            # (supported in Telegram legacy Markdown parse mode).
            segment = re.sub(
                r"~~(?=\S)(.+?)(?<=\S)~~",
                r"~\1~",
                segment,
            )
            return segment

        def _normalize_line(line: str) -> str:
            """Normalize a single non-code-block line."""
            stripped = line.strip()

            # Convert Markdown headings (# / ## / ### etc.) to bold text.
            heading_match = re.match(r"^(\s*)(#{1,6})\s+(.+)$", line)
            if heading_match:
                indent = heading_match.group(1)
                content = heading_match.group(3).rstrip()
                return f"{indent}*{content}*"

            # Convert horizontal rules (---, ***, ___) to a visual separator.
            if re.match(r"^\s*[-*_]{3,}\s*$", stripped):
                return "———"

            # Preserve inline code blocks while normalizing plain text.
            line_parts = line.split("`")
            for i, part in enumerate(line_parts):
                if i % 2 == 0:
                    line_parts[i] = _normalize_segment(part)
            return "`".join(line_parts)

        parts = []
        in_code_block = False

        for line in text.split("\n"):
            if line.strip().startswith("```"):
                in_code_block = not in_code_block
                parts.append(line)
            elif in_code_block:
                parts.append(line)
            else:
                parts.append(_normalize_line(line))

        return "\n".join(parts)

    def _escape_markdown_outside_code(self, text: str) -> str:
        """Escape Markdown characters outside of code blocks."""
        # Preserve intentional Markdown emphasis while escaping non-formatting chars.
        def _escape_segment(segment: str) -> str:
            placeholders: dict[str, str] = {}

            def _store(token_text: str) -> str:
                key = f"@@FMT{len(placeholders)}@@"
                placeholders[key] = token_text
                return key

            def _replace_url(match: re.Match[str]) -> str:
                url = match.group(0)
                stripped = url.rstrip(".,;:!?)]}")
                trailing = url[len(stripped) :]
                if not stripped:
                    return url
                return f"{_store(stripped)}{trailing}"

            # Protect URLs first, so underscores in links don't get escaped.
            segment = self._URL_PATTERN.sub(_replace_url, segment)

            def _replace_bold(match: re.Match[str]) -> str:
                inner = (
                    match.group(1)
                    .replace("_", r"\_")
                    .replace("*", r"\*")
                )
                return _store(f"*{inner}*")

            def _replace_italic(match: re.Match[str]) -> str:
                inner = (
                    match.group(1)
                    .replace("_", r"\_")
                    .replace("*", r"\*")
                )
                return _store(f"_{inner}_")

            # Protect bold/italic fragments first.
            segment = re.sub(r"\*(?=\S)(.+?)(?<=\S)\*", _replace_bold, segment)
            segment = re.sub(r"_(?=\S)(.+?)(?<=\S)_", _replace_italic, segment)

            # Escape remaining markdown symbols.
            segment = segment.replace("_", r"\_").replace("*", r"\*")

            # Restore protected formatting.
            for key, value in placeholders.items():
                segment = segment.replace(key, value)

            return segment

        parts = []
        in_code_block = False

        lines = text.split("\n")
        for line in lines:
            if line.strip().startswith("```"):
                in_code_block = not in_code_block
                parts.append(line)
            elif in_code_block:
                parts.append(line)
            else:
                # Handle inline code
                line_parts = line.split("`")
                for i, part in enumerate(line_parts):
                    if i % 2 == 0:  # Outside inline code
                        # Escape special characters while keeping bold/italic markers.
                        part = _escape_segment(part)
                    line_parts[i] = part
                parts.append("`".join(line_parts))

        return "\n".join(parts)

    def _format_code_blocks(self, text: str) -> str:
        """Ensure code blocks are properly formatted for Telegram."""
        # Handle triple backticks with language specification
        pattern = r"```(\w+)?\n(.*?)```"

        def replace_code_block(match):
            lang = match.group(1) or ""
            code = match.group(2)

            # Telegram doesn't support language hints, but we can add them as comments
            if lang and lang.lower() not in ["text", "plain"]:
                # Add language as a comment at the top
                code = f"# {lang}\n{code}"

            # Ensure code block doesn't exceed length limits
            if len(code) > self.max_code_block_length:
                code = code[: self.max_code_block_length - 50] + "\n... (truncated)"

            return f"```\n{code}\n```"

        return re.sub(pattern, replace_code_block, text, flags=re.DOTALL)

    def _split_message(self, text: str) -> List[FormattedMessage]:
        """Split long messages while preserving formatting."""
        if len(text) <= self.max_message_length:
            return [FormattedMessage(text)]

        messages = []
        current_lines = []
        current_length = 0
        in_code_block = False

        lines = text.split("\n")

        for line in lines:
            line_length = len(line) + 1  # +1 for newline

            # Check for code block markers
            if line.strip().startswith("```"):
                in_code_block = not in_code_block

            # If this is a very long line that exceeds limit by itself, split it
            if line_length > self.max_message_length:
                # Split the line into chunks
                chunks = []
                for i in range(0, len(line), self.max_message_length - 100):
                    chunks.append(line[i : i + self.max_message_length - 100])

                for chunk in chunks:
                    chunk_length = len(chunk) + 1

                    if (
                        current_length + chunk_length > self.max_message_length
                        and current_lines
                    ):
                        # Save current message
                        if in_code_block:
                            current_lines.append("```")
                        messages.append(FormattedMessage("\n".join(current_lines)))

                        # Start new message
                        current_lines = []
                        current_length = 0
                        if in_code_block:
                            current_lines.append("```")
                            current_length = 4

                    current_lines.append(chunk)
                    current_length += chunk_length
                continue

            # Check if adding this line would exceed the limit
            if current_length + line_length > self.max_message_length and current_lines:
                # Close code block if we're in one
                if in_code_block:
                    current_lines.append("```")

                # Save current message
                messages.append(FormattedMessage("\n".join(current_lines)))

                # Start new message
                current_lines = []
                current_length = 0

                # Reopen code block if needed
                if in_code_block:
                    current_lines.append("```")
                    current_length = 4  # Length of '```\n'

            current_lines.append(line)
            current_length += line_length

        # Add remaining content
        if current_lines:
            # Close code block if needed
            if in_code_block:
                current_lines.append("```")
            messages.append(FormattedMessage("\n".join(current_lines)))

        return messages

    def _get_quick_actions_keyboard(self) -> InlineKeyboardMarkup:
        """Get menu-like inline keyboard for common command entry points."""
        keyboard = [
            [
                InlineKeyboardButton("📋 Projects", callback_data="action:show_projects"),
                InlineKeyboardButton("📁 Files", callback_data="action:ls"),
                InlineKeyboardButton("📊 Status", callback_data="action:status"),
            ],
            [
                InlineKeyboardButton("🆕 New", callback_data="action:new_session"),
                InlineKeyboardButton("🌿 Git", callback_data="git:status"),
                InlineKeyboardButton("❓ Help", callback_data="action:help"),
            ],
        ]

        return InlineKeyboardMarkup(keyboard)

    def create_confirmation_keyboard(
        self, confirm_data: str, cancel_data: str = "confirm:no"
    ) -> InlineKeyboardMarkup:
        """Create a confirmation keyboard."""
        keyboard = [
            [
                InlineKeyboardButton("✅ Yes", callback_data=confirm_data),
                InlineKeyboardButton("❌ No", callback_data=cancel_data),
            ]
        ]
        return InlineKeyboardMarkup(keyboard)

    def create_navigation_keyboard(self, options: List[tuple]) -> InlineKeyboardMarkup:
        """Create navigation keyboard from options list.

        Args:
            options: List of (text, callback_data) tuples
        """
        keyboard = []
        current_row = []

        for text, callback_data in options:
            current_row.append(InlineKeyboardButton(text, callback_data=callback_data))

            # Create rows of 2 buttons
            if len(current_row) == 2:
                keyboard.append(current_row)
                current_row = []

        # Add remaining button if any
        if current_row:
            keyboard.append(current_row)

        return InlineKeyboardMarkup(keyboard)


class ProgressIndicator:
    """Helper for creating progress indicators."""

    @staticmethod
    def create_bar(
        percentage: float,
        length: int = 10,
        filled_char: str = "▓",
        empty_char: str = "░",
    ) -> str:
        """Create a progress bar."""
        filled = int((percentage / 100) * length)
        empty = length - filled
        return filled_char * filled + empty_char * empty

    @staticmethod
    def create_spinner(step: int) -> str:
        """Create a spinning indicator."""
        spinners = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        return spinners[step % len(spinners)]

    @staticmethod
    def create_dots(step: int) -> str:
        """Create a dots indicator."""
        dots = ["", ".", "..", "..."]
        return dots[step % len(dots)]


class CodeHighlighter:
    """Simple code highlighting for common languages."""

    # Language file extensions mapping
    LANGUAGE_EXTENSIONS = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".jsx": "javascript",
        ".tsx": "typescript",
        ".java": "java",
        ".cpp": "cpp",
        ".c": "c",
        ".cs": "csharp",
        ".go": "go",
        ".rs": "rust",
        ".rb": "ruby",
        ".php": "php",
        ".swift": "swift",
        ".kt": "kotlin",
        ".scala": "scala",
        ".sh": "bash",
        ".bash": "bash",
        ".zsh": "bash",
        ".sql": "sql",
        ".json": "json",
        ".xml": "xml",
        ".html": "html",
        ".css": "css",
        ".scss": "scss",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".toml": "toml",
        ".md": "markdown",
    }

    @classmethod
    def detect_language(cls, filename: str) -> str:
        """Detect programming language from filename."""
        from pathlib import Path

        ext = Path(filename).suffix.lower()
        return cls.LANGUAGE_EXTENSIONS.get(ext, "")

    @classmethod
    def format_code(cls, code: str, language: str = "", filename: str = "") -> str:
        """Format code with language detection."""
        if not language and filename:
            language = cls.detect_language(filename)

        if language:
            return f"```{language}\n{code}\n```"
        else:
            return f"```\n{code}\n```"


def _display_width(text: str) -> int:
    """Return the display width of *text*, counting wide (CJK) chars as 2."""
    width = 0
    for ch in text:
        eaw = unicodedata.east_asian_width(ch)
        width += 2 if eaw in ("W", "F") else 1
    return width
