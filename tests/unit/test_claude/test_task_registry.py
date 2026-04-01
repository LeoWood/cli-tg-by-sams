"""Tests for TaskRegistry scoped behavior."""

import asyncio
from contextlib import suppress

import pytest

from src.claude.task_registry import TaskRegistry


async def _long_running() -> None:
    """Keep task alive until cancelled."""
    await asyncio.sleep(3600)


@pytest.mark.asyncio
async def test_cancel_only_current_scope_task() -> None:
    """Cancelling one scope should not affect other scopes for same user."""
    registry = TaskRegistry()
    user_id = 101
    scope_a = "101:-100:1"
    scope_b = "101:-100:2"

    task_a = asyncio.create_task(_long_running())
    task_b = asyncio.create_task(_long_running())
    await registry.register(user_id=user_id, task=task_a, scope_key=scope_a)
    await registry.register(user_id=user_id, task=task_b, scope_key=scope_b)

    cancelled = await registry.cancel(user_id, scope_key=scope_a)
    await asyncio.sleep(0)

    assert cancelled is True
    assert task_a.cancelled() is True
    assert task_b.cancelled() is False
    assert await registry.is_busy(user_id, scope_key=scope_a) is False
    assert await registry.is_busy(user_id, scope_key=scope_b) is True

    task_b.cancel()
    with suppress(asyncio.CancelledError):
        await task_b


@pytest.mark.asyncio
async def test_cancel_without_scope_cancels_all_user_tasks() -> None:
    """Legacy cancel(user_id) should still cancel all scopes for that user."""
    registry = TaskRegistry()

    user_1 = 1
    user_2 = 2
    task_u1_a = asyncio.create_task(_long_running())
    task_u1_b = asyncio.create_task(_long_running())
    task_u2 = asyncio.create_task(_long_running())

    await registry.register(user_id=user_1, task=task_u1_a, scope_key="1:-100:10")
    await registry.register(user_id=user_1, task=task_u1_b, scope_key="1:-100:11")
    await registry.register(user_id=user_2, task=task_u2, scope_key="2:-200:20")

    cancelled = await registry.cancel(user_1)
    await asyncio.sleep(0)

    assert cancelled is True
    assert task_u1_a.cancelled() is True
    assert task_u1_b.cancelled() is True
    assert task_u2.cancelled() is False
    assert await registry.is_busy(user_1) is False
    assert await registry.is_busy(user_2) is True

    task_u2.cancel()
    with suppress(asyncio.CancelledError):
        await task_u2


@pytest.mark.asyncio
async def test_list_running_returns_only_running_tasks() -> None:
    """list_running should exclude tasks already marked completed/failed."""
    registry = TaskRegistry()
    task_running = asyncio.create_task(_long_running())
    task_done = asyncio.create_task(_long_running())

    await registry.register(user_id=11, task=task_running, scope_key="11:-1:1")
    await registry.register(user_id=12, task=task_done, scope_key="12:-1:1")
    await registry.complete(user_id=12, scope_key="12:-1:1")

    running = await registry.list_running()

    assert len(running) == 1
    assert running[0].user_id == 11
    assert running[0].scope_key == "11:-1:1"

    task_running.cancel()
    task_done.cancel()
    with suppress(asyncio.CancelledError):
        await task_running
    with suppress(asyncio.CancelledError):
        await task_done


@pytest.mark.asyncio
async def test_try_start_prevents_second_running_scope() -> None:
    """Atomic scope reservation should reject a concurrent second start."""
    registry = TaskRegistry()
    scope_key = "77:-100:1"

    first_started = await registry.try_start(77, scope_key=scope_key)
    second_started = await registry.try_start(77, scope_key=scope_key)

    assert first_started is True
    assert second_started is False
    assert await registry.is_busy(77, scope_key=scope_key) is True


@pytest.mark.asyncio
async def test_attach_task_cancels_when_slot_was_cancelled() -> None:
    """Binding a task after cancellation should immediately cancel it."""
    registry = TaskRegistry()
    scope_key = "88:-100:1"

    assert await registry.try_start(88, scope_key=scope_key) is True
    assert await registry.cancel(88, scope_key=scope_key) is True

    task = asyncio.create_task(_long_running())
    attached = await registry.attach_task(88, task, scope_key=scope_key)
    await asyncio.sleep(0)

    assert attached is False
    assert task.cancelled() is True
    assert await registry.is_busy(88, scope_key=scope_key) is False
