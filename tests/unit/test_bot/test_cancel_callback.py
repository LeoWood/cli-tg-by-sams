"""Tests for cancel button callback behavior."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.bot.handlers.callback import handle_callback_query


def _build_query(user_id: int, chat_id: int):
    """Build callback query stub for cancel tests."""
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id),
        data="cancel:task",
        answer=AsyncMock(),
        edit_message_reply_markup=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=chat_id),
            message_thread_id=None,
        ),
    )


@pytest.mark.asyncio
async def test_cancel_button_uses_fallback_when_scope_task_not_found(tmp_path: Path):
    """Cancel button should fallback to user-level task cancellation."""
    user_id = 8201
    chat_id = 9001
    query = _build_query(user_id=user_id, chat_id=chat_id)
    update = SimpleNamespace(callback_query=query)
    task_registry = SimpleNamespace(
        cancel=AsyncMock(side_effect=[False, True]),
    )
    audit_logger = SimpleNamespace(log_command=AsyncMock())
    context = SimpleNamespace(
        bot_data={
            "task_registry": task_registry,
            "settings": SimpleNamespace(approved_directory=tmp_path),
            "audit_logger": audit_logger,
            "auth_manager": SimpleNamespace(
                is_authenticated=Mock(return_value=True),
                refresh_session=Mock(return_value=True),
            ),
        },
        user_data={},
    )

    await handle_callback_query(update, context)

    assert task_registry.cancel.await_count == 2
    first = task_registry.cancel.await_args_list[0]
    second = task_registry.cancel.await_args_list[1]
    assert first.args == (user_id,)
    assert first.kwargs["scope_key"] == f"{user_id}:{chat_id}:0"
    assert second.args == (user_id,)
    assert second.kwargs["scope_key"] is None
    query.answer.assert_awaited_once_with("Task cancellation requested.")
    query.edit_message_reply_markup.assert_awaited_once_with(reply_markup=None)
    audit_logger.log_command.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancel_button_shows_alert_when_no_active_task(tmp_path: Path):
    """Cancel button should show explicit alert when there is no active task."""
    user_id = 8202
    chat_id = 9002
    query = _build_query(user_id=user_id, chat_id=chat_id)
    update = SimpleNamespace(callback_query=query)
    task_registry = SimpleNamespace(
        cancel=AsyncMock(side_effect=[False, False]),
    )
    context = SimpleNamespace(
        bot_data={
            "task_registry": task_registry,
            "settings": SimpleNamespace(approved_directory=tmp_path),
            "auth_manager": SimpleNamespace(
                is_authenticated=Mock(return_value=True),
                refresh_session=Mock(return_value=True),
            ),
        },
        user_data={},
    )

    await handle_callback_query(update, context)

    assert task_registry.cancel.await_count == 2
    query.answer.assert_awaited_once_with(
        "No active task to cancel.",
        show_alert=True,
    )
    query.edit_message_reply_markup.assert_awaited_once_with(reply_markup=None)


@pytest.mark.asyncio
async def test_cancel_button_blocks_unauthenticated_user(tmp_path: Path):
    """Callback router should block unauthenticated users before dispatching."""
    user_id = 8203
    chat_id = 9003
    query = _build_query(user_id=user_id, chat_id=chat_id)
    update = SimpleNamespace(callback_query=query)
    task_registry = SimpleNamespace(cancel=AsyncMock())
    context = SimpleNamespace(
        bot_data={
            "task_registry": task_registry,
            "settings": SimpleNamespace(approved_directory=tmp_path),
            "auth_manager": SimpleNamespace(
                is_authenticated=Mock(return_value=False),
                authenticate_user=AsyncMock(return_value=False),
            ),
        },
        user_data={},
    )

    await handle_callback_query(update, context)

    query.answer.assert_awaited_once_with(
        "🔒 Authentication required.",
        show_alert=True,
    )
    task_registry.cancel.assert_not_awaited()
