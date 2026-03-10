"""Tests for thinking expand/collapse callback behavior."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.bot.handlers.callback import _truncate_thinking, handle_thinking_callback


@pytest.mark.asyncio
async def test_thinking_expand_uses_cached_lines_and_collapse_button() -> None:
    """Expand action should render cached lines with a collapse button."""
    query = SimpleNamespace(edit_message_text=AsyncMock())
    context = SimpleNamespace(
        user_data={
            "thinking:123": {
                "lines": ["🔄 *step 1*", "✅ *done*"],
                "summary": "Thinking done -- 1 completed",
            }
        }
    )

    await handle_thinking_callback(query, "expand:123", context)

    query.edit_message_text.assert_awaited_once()
    call = query.edit_message_text.await_args
    assert call.args[0] == "🔄 *step 1*\n✅ *done*"
    assert call.kwargs["parse_mode"] == "Markdown"
    keyboard = call.kwargs["reply_markup"]
    assert keyboard.inline_keyboard[0][0].text == "Collapse"
    assert keyboard.inline_keyboard[0][0].callback_data == "thinking:collapse:123"


@pytest.mark.asyncio
async def test_thinking_expand_truncates_when_content_is_too_long() -> None:
    """Expand action should truncate long thinking content safely."""
    query = SimpleNamespace(edit_message_text=AsyncMock())
    long_lines = [f"line-{idx}-" + ("x" * 120) for idx in range(120)]
    context = SimpleNamespace(
        user_data={
            "thinking:456": {
                "lines": long_lines,
                "summary": "Thinking done",
            }
        }
    )

    await handle_thinking_callback(query, "expand:456", context)

    call = query.edit_message_text.await_args
    rendered = call.args[0]
    assert len(rendered) <= 3800
    assert "earlier entries omitted" in rendered


def test_truncate_thinking_prioritizes_textual_reasoning_lines() -> None:
    """Truncation should prefer assistant narration over pure command noise."""
    lines = [
        "🤖 *Codex is working...*\n\n先确认当前分支和远端，再决定是否直接 push。",
        "🔧 *Running command*\n\n`git remote -v`",
        "✅ *Command completed* \\(exit 0\\)\n\n`git remote -v`",
        "🔧 *Running command*\n\n`git status --short --branch`",
        "✅ *Command completed* \\(exit 0\\)\n\n`git status --short --branch`",
        "🔧 *Running command*\n\n`git push origin master`",
        "❌ *Command failed* \\(exit 128\\)\n\n`git push origin master`",
    ]

    rendered = _truncate_thinking(lines, max_chars=220)

    assert "先确认当前分支和远端" in rendered
    assert "git push origin master" in rendered
    assert "earlier entries omitted" in rendered


@pytest.mark.asyncio
async def test_thinking_collapse_restores_summary_and_expand_button() -> None:
    """Collapse action should restore summary text and expand button."""
    query = SimpleNamespace(edit_message_text=AsyncMock())
    context = SimpleNamespace(
        user_data={
            "thinking:789": {
                "lines": ["a", "b"],
                "summary": "Thinking done -- 2 completed",
            }
        }
    )

    await handle_thinking_callback(query, "collapse:789", context)

    call = query.edit_message_text.await_args
    assert call.args[0] == "Thinking done -- 2 completed"
    assert call.kwargs["parse_mode"] == "Markdown"
    keyboard = call.kwargs["reply_markup"]
    assert keyboard.inline_keyboard[0][0].text == "View thinking process"
    assert keyboard.inline_keyboard[0][0].callback_data == "thinking:expand:789"


@pytest.mark.asyncio
async def test_thinking_expand_returns_expired_message_when_cache_missing() -> None:
    """Missing cache entry should return an expiration message."""
    query = SimpleNamespace(edit_message_text=AsyncMock())
    context = SimpleNamespace(user_data={})

    await handle_thinking_callback(query, "expand:404", context)

    query.edit_message_text.assert_awaited_once_with(
        "Thinking process cache has expired and cannot be expanded."
    )


@pytest.mark.asyncio
async def test_thinking_expand_falls_back_to_plain_text_when_markdown_fails() -> None:
    """Expand should retry without parse_mode when markdown entity parsing fails."""
    query = SimpleNamespace(
        edit_message_text=AsyncMock(
            side_effect=[
                Exception("Bad Request: can't parse entities"),
                None,
            ]
        )
    )
    context = SimpleNamespace(
        user_data={
            "thinking:500": {
                "lines": ["[raw] line"],
                "summary": "Thinking done",
            }
        }
    )

    await handle_thinking_callback(query, "expand:500", context)

    assert query.edit_message_text.await_count == 2
    first_call = query.edit_message_text.await_args_list[0]
    second_call = query.edit_message_text.await_args_list[1]
    assert first_call.kwargs["parse_mode"] == "Markdown"
    assert "parse_mode" not in second_call.kwargs


@pytest.mark.asyncio
async def test_thinking_expand_treats_noop_edit_as_success() -> None:
    """Expand should treat Telegram 'not modified' error as success."""
    query = SimpleNamespace(
        edit_message_text=AsyncMock(
            side_effect=[
                Exception("Bad Request: message is not modified"),
            ]
        )
    )
    context = SimpleNamespace(
        user_data={
            "thinking:601": {
                "lines": ["line 1"],
                "summary": "Thinking done",
            }
        }
    )

    await handle_thinking_callback(query, "expand:601", context)

    # No fallback second call should be triggered for noop edits.
    assert query.edit_message_text.await_count == 1
