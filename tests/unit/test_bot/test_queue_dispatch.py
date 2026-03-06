"""Tests for queued task dispatch and prompt cleanup."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.bot.handlers import message as message_handler
from src.bot.inbound_task_queue import InboundTaskQueue


@pytest.mark.asyncio
async def test_dispatch_next_queued_text_cleans_queue_prompt(tmp_path) -> None:
    """Dispatch should cleanup queue prompt bubble before running queued text task."""
    user_id = 9401
    chat_id = 9402
    scope_key = f"{user_id}:{chat_id}:0"
    queue = InboundTaskQueue(max_per_scope=10)
    queued_update = SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        effective_chat=SimpleNamespace(id=chat_id, type="private"),
        message=SimpleNamespace(
            text="queued text",
            message_id=12345,
            chat_id=chat_id,
            message_thread_id=None,
        ),
    )
    await queue.enqueue(
        user_id=user_id,
        scope_key=scope_key,
        kind="text",
        payload={
            "update": queued_update,
            "message_text": "queued text",
            "source_message_id": 12345,
            "fragment_count": 1,
            "queue_prompt_chat_id": chat_id,
            "queue_prompt_message_id": 9988,
        },
        preview="queued text",
    )
    context = SimpleNamespace(
        bot_data={
            "inbound_task_queue": queue,
            "task_registry": SimpleNamespace(is_busy=AsyncMock(return_value=False)),
            "settings": SimpleNamespace(approved_directory=tmp_path),
        },
        bot=SimpleNamespace(delete_message=AsyncMock()),
        user_data={},
    )
    dispatched = asyncio.Event()

    async def _fake_handle_text_message(*args, **kwargs):
        dispatched.set()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(message_handler, "handle_text_message", _fake_handle_text_message)
        dispatched_ok = await message_handler.dispatch_next_queued_task_if_idle(
            context=context,
            scope_key=scope_key,
        )

    assert dispatched_ok is True
    await asyncio.wait_for(dispatched.wait(), timeout=1.0)
    context.bot.delete_message.assert_awaited_once_with(
        chat_id=chat_id, message_id=9988
    )
    remaining = await queue.count_scope(scope_key=scope_key, user_id=user_id)
    assert remaining == 0


@pytest.mark.asyncio
async def test_handle_photo_busy_enqueues_queue_item_instead_of_cancel(
    tmp_path,
) -> None:
    """Busy photo requests should join queue and return queue controls."""
    user_id = 9411
    chat_id = 9412
    scope_key = f"{user_id}:{chat_id}:0"
    queue = InboundTaskQueue(max_per_scope=10)
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        effective_chat=SimpleNamespace(id=chat_id, type="private"),
        message=SimpleNamespace(
            message_id=2233,
            chat_id=chat_id,
            message_thread_id=None,
            media_group_id=None,
        ),
    )
    reply_mock = AsyncMock(
        return_value=SimpleNamespace(
            message_id=8899,
            chat_id=chat_id,
            chat=SimpleNamespace(id=chat_id),
        )
    )
    context = SimpleNamespace(
        bot_data={
            "settings": SimpleNamespace(approved_directory=tmp_path),
            "features": SimpleNamespace(get_image_handler=Mock(return_value=object())),
            "task_registry": SimpleNamespace(is_busy=AsyncMock(return_value=True)),
            "inbound_task_queue": queue,
        },
        user_data={},
        bot=SimpleNamespace(),
    )

    async def _fake_collect_media_group_photos(_update, _context):
        return True, [SimpleNamespace(file_id="ph1")], "queued photo", 2233

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            message_handler,
            "_collect_media_group_photos",
            _fake_collect_media_group_photos,
        )
        mp.setattr(message_handler, "_reply_text_resilient", reply_mock)
        await message_handler.handle_photo(update, context)

    reply_mock.assert_awaited_once()
    sent_text = reply_mock.await_args.args[1]
    assert "已加入队列" in sent_text
    assert "撤回/插队执行" in sent_text

    queued_items = await queue.list_scope(scope_key=scope_key, user_id=user_id)
    assert len(queued_items) == 1
    queued_item = queued_items[0]
    assert queued_item.kind == "photo"
    assert queued_item.payload["queue_prompt_message_id"] == 8899
    assert queued_item.payload["queue_prompt_chat_id"] == chat_id
