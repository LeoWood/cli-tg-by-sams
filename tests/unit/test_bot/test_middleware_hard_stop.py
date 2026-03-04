"""Tests for middleware hard-stop behavior."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from telegram.ext import ApplicationHandlerStop

from src.bot.middleware.auth import auth_middleware
from src.bot.middleware.security import security_middleware


def _build_event(*, text: str = "hello") -> SimpleNamespace:
    """Create a minimal Telegram update-like event for middleware tests."""
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=1001, username="tester"),
        effective_message=SimpleNamespace(
            text=text,
            document=None,
            reply_text=AsyncMock(),
            chat=SimpleNamespace(id=2001, type="private"),
            chat_id=2001,
            message_id=3001,
            message_thread_id=None,
        ),
    )


@pytest.mark.asyncio
async def test_auth_middleware_blocks_when_auth_manager_missing():
    """Auth middleware should stop handler chain when manager is unavailable."""
    event = _build_event()
    handler = AsyncMock()

    with pytest.raises(ApplicationHandlerStop):
        await auth_middleware(handler, event, {})

    handler.assert_not_awaited()
    event.effective_message.reply_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_auth_middleware_blocks_unauthenticated_user():
    """Auth middleware should hard-stop on authentication failure."""
    event = _build_event()
    handler = AsyncMock()
    data = {
        "auth_manager": SimpleNamespace(
            is_authenticated=Mock(return_value=False),
            authenticate_user=AsyncMock(return_value=False),
        )
    }

    with pytest.raises(ApplicationHandlerStop):
        await auth_middleware(handler, event, data)

    handler.assert_not_awaited()
    event.effective_message.reply_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_security_middleware_blocks_when_validator_missing():
    """Security middleware should fail closed when validator is missing."""
    event = _build_event()
    handler = AsyncMock()

    with pytest.raises(ApplicationHandlerStop):
        await security_middleware(handler, event, {})

    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_security_middleware_blocks_dangerous_message():
    """Security middleware should hard-stop when message is unsafe."""
    event = _build_event(text="`rm -rf /`")
    handler = AsyncMock()
    data = {
        "security_validator": SimpleNamespace(
            sanitize_command_input=lambda text: text,
        ),
        "audit_logger": None,
    }

    with pytest.raises(ApplicationHandlerStop):
        await security_middleware(handler, event, data)

    handler.assert_not_awaited()
    event.effective_message.reply_text.assert_awaited_once()
