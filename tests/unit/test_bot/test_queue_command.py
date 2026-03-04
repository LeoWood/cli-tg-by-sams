"""Tests for /queue and /dequeue commands."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.bot.handlers.command import dequeue_command, queue_status_command
from src.bot.inbound_task_queue import InboundTaskQueue


def _build_update(*, user_id: int, chat_id: int):
    """Build lightweight update object for queue command tests."""
    message = SimpleNamespace(
        chat_id=chat_id,
        message_thread_id=None,
        reply_text=AsyncMock(),
    )
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        effective_chat=SimpleNamespace(id=chat_id),
        effective_message=message,
        message=message,
    )


@pytest.mark.asyncio
async def test_queue_status_command_lists_pending_items(tmp_path: Path) -> None:
    """`/queue` should render queue id and preview for current scope."""
    user_id = 9001
    chat_id = 9002
    update = _build_update(user_id=user_id, chat_id=chat_id)
    queue = InboundTaskQueue(max_per_scope=10)
    queue_item, _ = await queue.enqueue(
        user_id=user_id,
        scope_key=f"{user_id}:{chat_id}:0",
        kind="text",
        payload={"text": "hello"},
        preview="hello queue",
    )
    context = SimpleNamespace(
        bot_data={
            "inbound_task_queue": queue,
            "settings": SimpleNamespace(approved_directory=tmp_path),
        },
        user_data={},
        args=[],
    )

    await queue_status_command(update, context)

    update.message.reply_text.assert_awaited_once()
    sent_text = update.message.reply_text.await_args.args[0]
    assert queue_item.queue_id in sent_text
    assert "hello queue" in sent_text


@pytest.mark.asyncio
async def test_dequeue_command_removes_target_item(tmp_path: Path) -> None:
    """`/dequeue <id>` should remove queued task in current scope."""
    user_id = 9011
    chat_id = 9012
    update = _build_update(user_id=user_id, chat_id=chat_id)
    queue = InboundTaskQueue(max_per_scope=10)
    queue_item, _ = await queue.enqueue(
        user_id=user_id,
        scope_key=f"{user_id}:{chat_id}:0",
        kind="text",
        payload={"text": "hello"},
        preview="hello queue",
    )
    context = SimpleNamespace(
        bot_data={
            "inbound_task_queue": queue,
            "settings": SimpleNamespace(approved_directory=tmp_path),
        },
        user_data={},
        args=[queue_item.queue_id],
    )

    await dequeue_command(update, context)

    update.message.reply_text.assert_awaited_once()
    sent_text = update.message.reply_text.await_args.args[0]
    assert queue_item.queue_id in sent_text
    remaining = await queue.count_scope(
        scope_key=f"{user_id}:{chat_id}:0",
        user_id=user_id,
    )
    assert remaining == 0
