"""Tests for inbound pending-task queue behavior."""

import pytest

from src.bot.inbound_task_queue import InboundTaskQueue, QueueFullError


@pytest.mark.asyncio
async def test_inbound_queue_fifo_pop_order() -> None:
    """Queue should pop tasks in FIFO order within same scope."""
    queue = InboundTaskQueue(max_per_scope=5)
    first, _ = await queue.enqueue(
        user_id=1,
        scope_key="1:-100:0",
        kind="text",
        payload={"text": "first"},
        preview="first",
    )
    second, _ = await queue.enqueue(
        user_id=1,
        scope_key="1:-100:0",
        kind="text",
        payload={"text": "second"},
        preview="second",
    )

    popped_first = await queue.pop_next(scope_key="1:-100:0")
    popped_second = await queue.pop_next(scope_key="1:-100:0")
    popped_none = await queue.pop_next(scope_key="1:-100:0")

    assert popped_first is not None
    assert popped_second is not None
    assert popped_first.queue_id == first.queue_id
    assert popped_second.queue_id == second.queue_id
    assert popped_none is None


@pytest.mark.asyncio
async def test_inbound_queue_promote_moves_item_to_head() -> None:
    """Promote should move existing queue item to scope head."""
    queue = InboundTaskQueue(max_per_scope=5)
    first, _ = await queue.enqueue(
        user_id=1,
        scope_key="1:-100:0",
        kind="text",
        payload={"text": "a"},
        preview="a",
    )
    second, _ = await queue.enqueue(
        user_id=1,
        scope_key="1:-100:0",
        kind="text",
        payload={"text": "b"},
        preview="b",
    )

    promoted = await queue.promote(
        queue_id=second.queue_id,
        scope_key="1:-100:0",
        user_id=1,
    )
    popped = await queue.pop_next(scope_key="1:-100:0")
    popped_next = await queue.pop_next(scope_key="1:-100:0")

    assert promoted is not None
    assert popped is not None
    assert popped_next is not None
    assert popped.queue_id == second.queue_id
    assert popped_next.queue_id == first.queue_id


@pytest.mark.asyncio
async def test_inbound_queue_dequeue_respects_scope_and_user() -> None:
    """Dequeue should remove only matching item under user+scope constraint."""
    queue = InboundTaskQueue(max_per_scope=5)
    item, _ = await queue.enqueue(
        user_id=7,
        scope_key="7:-100:9",
        kind="text",
        payload={"text": "payload"},
        preview="payload",
    )

    not_removed = await queue.dequeue(
        queue_id=item.queue_id,
        scope_key="other-scope",
        user_id=7,
    )
    removed = await queue.dequeue(
        queue_id=item.queue_id,
        scope_key="7:-100:9",
        user_id=7,
    )

    assert not_removed is None
    assert removed is not None
    assert removed.queue_id == item.queue_id


@pytest.mark.asyncio
async def test_inbound_queue_raises_when_scope_full() -> None:
    """Queue should reject new items after reaching max_per_scope."""
    queue = InboundTaskQueue(max_per_scope=1)
    await queue.enqueue(
        user_id=1,
        scope_key="scope",
        kind="text",
        payload={"text": "first"},
        preview="first",
    )

    with pytest.raises(QueueFullError):
        await queue.enqueue(
            user_id=1,
            scope_key="scope",
            kind="text",
            payload={"text": "second"},
            preview="second",
        )
