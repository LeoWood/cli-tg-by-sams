"""Tests for core update dedupe/stale guard."""

from types import SimpleNamespace

import pytest
from telegram.ext import ApplicationHandlerStop

from src.bot.core import ClaudeCodeBot


def test_build_update_offset_state_file_uses_bot_specific_suffix(tmp_path):
    """Different Telegram bots should not share the same offset file."""
    settings = SimpleNamespace(
        approved_directory=tmp_path,
        telegram_bot_token="8501544866:example-token",
        telegram_bot_username="leo_everglow_bot",
    )
    bot = ClaudeCodeBot(settings=settings, dependencies={})

    state_file = bot._build_update_offset_state_file()

    assert state_file == (
        tmp_path / "data" / "state" / "telegram" / "update-offset-8501544866.json"
    )


def test_build_update_offset_state_file_falls_back_to_username_when_token_invalid(
    tmp_path,
):
    """Username fallback keeps offset isolation when token is unavailable in tests."""
    settings = SimpleNamespace(
        approved_directory=tmp_path,
        telegram_bot_token="not-a-token",
        telegram_bot_username="@Leo Everglow Bot",
    )
    bot = ClaudeCodeBot(settings=settings, dependencies={})

    state_file = bot._build_update_offset_state_file()

    assert state_file == (
        tmp_path
        / "data"
        / "state"
        / "telegram"
        / "update-offset-leo-everglow-bot.json"
    )


@pytest.mark.asyncio
async def test_update_guard_blocks_duplicate_update():
    """Duplicate updates should be blocked by the guard."""
    bot = ClaudeCodeBot(settings=SimpleNamespace(), dependencies={})
    recorded_ids: list[int] = []
    bot._update_offset_store = SimpleNamespace(record=recorded_ids.append)

    update = SimpleNamespace(update_id=2026001)
    context = SimpleNamespace()

    await bot._handle_update_guard(update, context)
    assert recorded_ids == [2026001]

    with pytest.raises(ApplicationHandlerStop):
        await bot._handle_update_guard(update, context)

    assert recorded_ids == [2026001]


@pytest.mark.asyncio
async def test_update_guard_blocks_stale_update_before_dedupe():
    """Updates below persisted startup offset should be skipped."""
    bot = ClaudeCodeBot(settings=SimpleNamespace(), dependencies={})
    bot._startup_min_update_id = 300
    recorded_ids: list[int] = []
    bot._update_offset_store = SimpleNamespace(record=recorded_ids.append)

    stale_update = SimpleNamespace(update_id=299)

    with pytest.raises(ApplicationHandlerStop):
        await bot._handle_update_guard(stale_update, SimpleNamespace())

    assert recorded_ids == []
