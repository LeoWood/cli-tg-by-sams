"""Tests for queue action callbacks."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.bot.handlers import callback as callback_handler
from src.bot.inbound_task_queue import InboundTaskQueue


def _build_query(*, user_id: int, chat_id: int, data: str):
    """Build callback query stub."""
    source_message = SimpleNamespace(
        message_id=7788,
        from_user=SimpleNamespace(id=user_id),
        delete=AsyncMock(),
    )
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id),
        data=data,
        answer=AsyncMock(),
        edit_message_reply_markup=AsyncMock(),
        message=SimpleNamespace(
            message_id=8899,
            chat=SimpleNamespace(id=chat_id),
            message_thread_id=None,
            delete=AsyncMock(),
            reply_to_message=source_message,
        ),
    )


def _build_context(*, tmp_path: Path, queue: InboundTaskQueue, task_registry=None):
    """Build context stub with authenticated callback context."""
    bot_data = {
        "inbound_task_queue": queue,
        "settings": SimpleNamespace(approved_directory=tmp_path),
        "auth_manager": SimpleNamespace(
            is_authenticated=Mock(return_value=True),
            refresh_session=Mock(return_value=True),
        ),
    }
    if task_registry is not None:
        bot_data["task_registry"] = task_registry
    return SimpleNamespace(bot_data=bot_data, user_data={})


@pytest.mark.asyncio
async def test_queue_dequeue_callback_removes_pending_item(tmp_path: Path) -> None:
    """`queue:dequeue:<id>` should remove pending item and clear button."""
    user_id = 9301
    chat_id = 9302
    queue = InboundTaskQueue(max_per_scope=10)
    item, _ = await queue.enqueue(
        user_id=user_id,
        scope_key=f"{user_id}:{chat_id}:0",
        kind="text",
        payload={"text": "queued"},
        preview="queued",
    )
    query = _build_query(
        user_id=user_id,
        chat_id=chat_id,
        data=f"queue:dequeue:{item.queue_id}",
    )
    update = SimpleNamespace(callback_query=query)
    context = _build_context(tmp_path=tmp_path, queue=queue)

    await callback_handler.handle_callback_query(update, context)

    query.answer.assert_awaited_once_with("已撤回排队任务。")
    query.message.delete.assert_awaited_once()
    query.message.reply_to_message.delete.assert_awaited_once()
    query.edit_message_reply_markup.assert_not_awaited()
    remaining = await queue.count_scope(
        scope_key=f"{user_id}:{chat_id}:0",
        user_id=user_id,
    )
    assert remaining == 0


@pytest.mark.asyncio
async def test_queue_promote_callback_requests_cancel_and_dispatch(tmp_path: Path) -> None:
    """`queue:promote:<id>` should preempt running task then dispatch queue."""
    user_id = 9311
    chat_id = 9312
    queue = InboundTaskQueue(max_per_scope=10)
    item, _ = await queue.enqueue(
        user_id=user_id,
        scope_key=f"{user_id}:{chat_id}:0",
        kind="text",
        payload={"text": "queued"},
        preview="queued",
    )
    query = _build_query(
        user_id=user_id,
        chat_id=chat_id,
        data=f"queue:promote:{item.queue_id}",
    )
    update = SimpleNamespace(callback_query=query)
    task_registry = SimpleNamespace(cancel=AsyncMock(return_value=True))
    context = _build_context(
        tmp_path=tmp_path,
        queue=queue,
        task_registry=task_registry,
    )
    dispatch_mock = AsyncMock(return_value=False)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(callback_handler, "dispatch_next_queued_task_if_idle", dispatch_mock)
        await callback_handler.handle_callback_query(update, context)

    task_registry.cancel.assert_awaited_once_with(
        user_id,
        scope_key=f"{user_id}:{chat_id}:0",
    )
    dispatch_mock.assert_awaited_once()
    query.answer.assert_awaited_once_with("已请求插队，正在中断当前任务...")
    query.message.delete.assert_awaited_once()
    query.edit_message_reply_markup.assert_not_awaited()
