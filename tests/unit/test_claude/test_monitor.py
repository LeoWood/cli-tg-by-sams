"""Tests for ToolMonitor shell command policy guards."""

from pathlib import Path

import pytest

from src.claude.monitor import ToolMonitor
from src.config.settings import Settings


def _build_config(tmp_path: Path) -> Settings:
    """Create minimal settings for tool monitor tests."""
    return Settings(
        telegram_bot_token="test:token",
        telegram_bot_username="testbot",
        approved_directory=tmp_path,
        use_sdk=False,
    )


@pytest.mark.asyncio
async def test_validate_tool_call_blocks_operational_restart_commands(tmp_path: Path):
    """`make run`-style commands should be rejected for TG remote execution."""
    monitor = ToolMonitor(_build_config(tmp_path))

    valid, error = await monitor.validate_tool_call(
        "Bash",
        {"command": "make run"},
        tmp_path,
        user_id=12345,
    )

    assert valid is False
    assert error is not None
    assert "Operational command blocked" in error
    assert "/restartbot" in error


@pytest.mark.asyncio
async def test_validate_tool_call_allows_regular_shell_commands(tmp_path: Path):
    """Normal shell commands should keep passing validation."""
    monitor = ToolMonitor(_build_config(tmp_path))

    valid, error = await monitor.validate_tool_call(
        "Bash",
        {"command": "ls -la src"},
        tmp_path,
        user_id=12345,
    )

    assert valid is True
    assert error is None


@pytest.mark.asyncio
async def test_validate_tool_call_does_not_block_keyword_in_search_pattern(
    tmp_path: Path,
):
    """Search patterns mentioning `make run` should not be treated as restart ops."""
    monitor = ToolMonitor(_build_config(tmp_path))

    valid, error = await monitor.validate_tool_call(
        "Bash",
        {
            "command": (
                "/bin/zsh -lc 'rg -n "
                '"restart|restartbot|resume|make run|tmux-bot|status" README.md\''
            )
        },
        tmp_path,
        user_id=12345,
    )

    assert valid is True
    assert error is None
