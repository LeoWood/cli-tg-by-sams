"""Monitor Claude's tool usage.

Features:
- Track tool calls
- Security validation
- Usage analytics
"""

import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import structlog

from ..config.settings import Settings
from ..security.validators import SecurityValidator

logger = structlog.get_logger()


class ToolMonitor:
    """Monitor and validate Claude's tool usage."""

    _OPERATIONAL_COMMAND_PATTERNS: List[Tuple[str, str]] = [
        (r"\bmake\s+run(?:-debug|-local)?\b", "make run/run-debug/run-local"),
        (r"\bmake\s+bot-stop\b", "make bot-stop"),
        (
            r"(?:^|[\s'\"`])(?:\.\/)?scripts\/tmux-bot\.sh\s+(?:restart|start|stop)\b",
            "scripts/tmux-bot.sh restart/start/stop",
        ),
        (
            r"(?:^|[\s'\"`])(?:\.\/)?scripts\/restart-bot\.sh(?:\s|$)",
            "scripts/restart-bot.sh",
        ),
        (r"\btmux\s+kill-session\b", "tmux kill-session"),
        (
            r"\bpkill\s+-f\b.*\b(?:cli-tg|cli-tg-bot|claude-telegram-bot)\b",
            "pkill -f cli-tg*",
        ),
    ]

    def __init__(
        self, config: Settings, security_validator: Optional[SecurityValidator] = None
    ):
        """Initialize tool monitor."""
        self.config = config
        self.security_validator = security_validator
        self.tool_usage: Dict[str, int] = defaultdict(int)
        self.security_violations: List[Dict[str, Any]] = []

    @classmethod
    def _match_operational_command_block(cls, command: str) -> Optional[str]:
        """Return blocked operational command label when matched."""
        lowered = command.lower()
        for pattern, label in cls._OPERATIONAL_COMMAND_PATTERNS:
            if re.search(pattern, lowered):
                return label
        return None

    async def validate_tool_call(
        self,
        tool_name: str,
        tool_input: Dict[str, Any],
        working_directory: Path,
        user_id: int,
    ) -> Tuple[bool, Optional[str]]:
        """Validate tool call for security concerns.

        Note: Tool authorization (allowed/denied) is handled pre-execution
        by the SDK's can_use_tool callback. This method only performs
        post-execution security auditing (disallowed tools, dangerous
        commands, path traversal, etc.).
        """
        logger.debug(
            "Validating tool call",
            tool_name=tool_name,
            working_directory=str(working_directory),
            user_id=user_id,
        )

        # Check if tool is explicitly disallowed
        if (
            hasattr(self.config, "claude_disallowed_tools")
            and self.config.claude_disallowed_tools
        ):
            if tool_name in self.config.claude_disallowed_tools:
                violation = {
                    "type": "explicitly_disallowed_tool",
                    "tool_name": tool_name,
                    "user_id": user_id,
                    "working_directory": str(working_directory),
                }
                self.security_violations.append(violation)
                logger.warning("Tool explicitly disallowed", **violation)
                return False, f"Tool explicitly disallowed: {tool_name}"

        # Validate file operations
        if tool_name in [
            "create_file",
            "edit_file",
            "read_file",
            "Write",
            "Edit",
            "Read",
        ]:
            file_path = tool_input.get("path") or tool_input.get("file_path")
            if not file_path:
                return False, "File path required"

            # Validate path security
            if self.security_validator:
                valid, resolved_path, error = self.security_validator.validate_path(
                    file_path, working_directory
                )

                if not valid:
                    violation = {
                        "type": "invalid_file_path",
                        "tool_name": tool_name,
                        "file_path": file_path,
                        "user_id": user_id,
                        "working_directory": str(working_directory),
                        "error": error,
                    }
                    self.security_violations.append(violation)
                    logger.warning("Invalid file path in tool call", **violation)
                    return False, error

        # Validate shell commands
        if tool_name in ["bash", "shell", "Bash"]:
            command = tool_input.get("command", "")
            cmd_lower = command.lower()

            blocked_label = self._match_operational_command_block(command)
            if blocked_label:
                violation = {
                    "type": "blocked_operational_command",
                    "tool_name": tool_name,
                    "command": command,
                    "pattern": blocked_label,
                    "user_id": user_id,
                    "working_directory": str(working_directory),
                }
                self.security_violations.append(violation)
                logger.warning("Operational command blocked", **violation)
                return (
                    False,
                    "Operational command blocked in remote Telegram session: "
                    f"{blocked_label}. Use /restartbot for restart or /opsstatus for diagnostics.",
                )

            # Truly dangerous patterns (regex for precision)
            dangerous_regex_patterns = [
                (r"\brm\s+-rf\s+/", "rm -rf /"),
                (r"\bsudo\b", "sudo"),
                (r"\bchmod\s+777\b", "chmod 777"),
                (r"\bnetcat\b", "netcat"),
                (r"\bnc\s+-[elp]", "nc (reverse shell)"),
                (r"\bmkfs\b", "mkfs"),
                (r"\bdd\s+if=", "dd"),
                (r":\(\)\s*\{.*\|.*&\s*\}\s*;", "fork bomb"),
            ]

            for pattern, label in dangerous_regex_patterns:
                if re.search(pattern, cmd_lower):
                    violation = {
                        "type": "dangerous_command",
                        "tool_name": tool_name,
                        "command": command,
                        "pattern": label,
                        "user_id": user_id,
                        "working_directory": str(working_directory),
                    }
                    self.security_violations.append(violation)
                    logger.warning("Dangerous command detected", **violation)
                    return False, f"Dangerous command pattern detected: {label}"

        # Track usage
        self.tool_usage[tool_name] += 1

        logger.debug("Tool call validated successfully", tool_name=tool_name)
        return True, None

    def get_tool_stats(self) -> Dict[str, Any]:
        """Get tool usage statistics."""
        return {
            "total_calls": sum(self.tool_usage.values()),
            "by_tool": dict(self.tool_usage),
            "unique_tools": len(self.tool_usage),
            "security_violations": len(self.security_violations),
        }

    def get_security_violations(self) -> List[Dict[str, Any]]:
        """Get security violations."""
        return self.security_violations.copy()

    def reset_stats(self) -> None:
        """Reset statistics."""
        self.tool_usage.clear()
        self.security_violations.clear()
        logger.info("Tool monitor statistics reset")

    def get_user_tool_usage(self, user_id: int) -> Dict[str, Any]:
        """Get tool usage for specific user."""
        user_violations = [
            v for v in self.security_violations if v.get("user_id") == user_id
        ]

        return {
            "user_id": user_id,
            "security_violations": len(user_violations),
            "violation_types": list(set(v.get("type") for v in user_violations)),
        }

    def is_tool_allowed(self, tool_name: str) -> bool:
        """Check if tool is allowed without validation."""
        # Check allowed list
        if (
            hasattr(self.config, "claude_allowed_tools")
            and self.config.claude_allowed_tools
        ):
            if tool_name not in self.config.claude_allowed_tools:
                return False

        # Check disallowed list
        if (
            hasattr(self.config, "claude_disallowed_tools")
            and self.config.claude_disallowed_tools
        ):
            if tool_name in self.config.claude_disallowed_tools:
                return False

        return True
