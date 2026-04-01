"""Tests for text-request runtime metrics instrumentation."""

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.bot.handlers import message as message_handler
from src.bot.inbound_task_queue import InboundTaskQueue
from src.claude.task_registry import TaskRegistry
from src.monitoring import RuntimeMetrics


class _FakeFormatter:
    def __init__(self, settings):
        self.settings = settings

    def format_claude_response(self, text):
        return [
            SimpleNamespace(
                text=text,
                parse_mode="Markdown",
                reply_markup=None,
            )
        ]


def _build_settings(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        approved_directory=tmp_path,
        stream_render_debounce_ms=0,
        stream_render_min_edit_interval_ms=0,
        telegram_noncritical_failure_threshold=3,
        telegram_noncritical_cooldown_seconds=60.0,
        enable_quick_actions=False,
    )


def _build_update(*, text: str) -> SimpleNamespace:
    message = SimpleNamespace(
        text=text,
        message_id=11,
        message_thread_id=None,
        date=datetime.now(timezone.utc) - timedelta(seconds=2),
    )
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=101),
        effective_chat=SimpleNamespace(id=202, type="private"),
        effective_message=message,
        message=message,
    )


@pytest.mark.asyncio
async def test_handle_text_message_records_success_metrics(tmp_path: Path) -> None:
    """Successful text requests should observe stage latency metrics."""
    runtime_metrics = RuntimeMetrics(enabled=False)
    progress_msg = SimpleNamespace(
        message_id=88,
        edit_text=AsyncMock(),
        edit_reply_markup=AsyncMock(),
        delete=AsyncMock(),
    )
    fake_integration = SimpleNamespace(
        run_command=AsyncMock(
            return_value=SimpleNamespace(
                content="final answer",
                session_id="sess-1",
                cost=0.0,
                num_turns=1,
            )
        )
    )
    context = SimpleNamespace(
        bot_data={
            "settings": _build_settings(tmp_path),
            "runtime_metrics": runtime_metrics,
            "task_registry": TaskRegistry(metrics=runtime_metrics),
            "storage": None,
        },
        bot=SimpleNamespace(),
        user_data={},
    )
    update = _build_update(text="hello")

    async def _fake_reply(*args, **kwargs):
        if not hasattr(_fake_reply, "calls"):
            _fake_reply.calls = 0
        _fake_reply.calls += 1
        return (
            progress_msg if _fake_reply.calls == 1 else SimpleNamespace(message_id=99)
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            message_handler,
            "get_scope_state_from_update",
            lambda **kwargs: ("101:202:0", {}),
        )
        mp.setattr(
            message_handler,
            "get_cli_integration",
            lambda **kwargs: ("claude", fake_integration),
        )
        mp.setattr(message_handler, "_reply_text_resilient", _fake_reply)
        mp.setattr(message_handler, "_send_chat_action_heartbeat", AsyncMock())
        mp.setattr(message_handler, "_set_message_reaction_safe", AsyncMock())
        mp.setattr(message_handler, "build_permission_handler", lambda **kwargs: None)
        mp.setattr(
            message_handler,
            "_resolve_pending_reaction_feedback",
            lambda **kwargs: (None, None),
        )
        mp.setattr(
            message_handler,
            "_compose_prompt_with_reaction_feedback",
            lambda text, feedback: text,
        )
        mp.setattr(
            message_handler,
            "_compose_prompt_with_telegram_remote_context",
            lambda prompt, settings: prompt,
        )
        mp.setattr(
            message_handler,
            "_update_working_directory_from_claude_response",
            lambda *args, **kwargs: None,
        )
        mp.setattr(
            message_handler,
            "_enforce_no_local_image_fallback_for_image_gen",
            lambda response: False,
        )
        mp.setattr(message_handler, "_build_context_tag", lambda **kwargs: None)
        mp.setattr(
            message_handler,
            "_resolve_collapsed_fallback_model",
            lambda **kwargs: None,
        )
        mp.setattr(
            message_handler,
            "dispatch_next_queued_task_if_idle",
            AsyncMock(return_value=False),
        )
        mp.setattr(
            message_handler,
            "_send_generated_images_from_response",
            AsyncMock(),
        )
        mp.setattr(
            message_handler,
            "_send_generated_files_from_response",
            AsyncMock(),
        )
        mp.setattr(
            "src.bot.utils.formatting.ResponseFormatter",
            _FakeFormatter,
        )

        await message_handler.handle_text_message(update, context)

    rendered = runtime_metrics.render_prometheus_text()
    assert 'clitg_text_requests_total{engine="claude",result="success"} 1' in rendered
    assert 'clitg_text_end_to_first_reply_seconds_count{engine="claude"} 1' in rendered
    assert 'clitg_text_cli_exec_seconds_count{engine="claude"} 1' in rendered
    assert runtime_metrics.get_gauge_value("clitg_active_tasks") == 0.0


@pytest.mark.asyncio
async def test_handle_text_message_busy_request_only_records_queue_metrics(
    tmp_path: Path,
) -> None:
    """Busy text requests should increment queued counter without final request count."""
    runtime_metrics = RuntimeMetrics(enabled=False)
    inbound_queue = InboundTaskQueue(max_per_scope=5)
    queue_prompt_msg = SimpleNamespace(message_id=55)
    context = SimpleNamespace(
        bot_data={
            "settings": _build_settings(tmp_path),
            "runtime_metrics": runtime_metrics,
            "task_registry": SimpleNamespace(is_busy=AsyncMock(return_value=True)),
            "inbound_task_queue": inbound_queue,
        },
        bot=SimpleNamespace(),
        user_data={},
    )
    update = _build_update(text="hello")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            message_handler,
            "get_scope_state_from_update",
            lambda **kwargs: ("101:202:0", {}),
        )
        mp.setattr(
            message_handler,
            "get_cli_integration",
            lambda **kwargs: ("claude", object()),
        )
        mp.setattr(
            message_handler,
            "_collect_text_fragments",
            AsyncMock(return_value=(True, "hello", 11, 1)),
        )
        mp.setattr(
            message_handler,
            "_reply_text_resilient",
            AsyncMock(return_value=queue_prompt_msg),
        )

        await message_handler.handle_text_message(update, context)

    rendered = runtime_metrics.render_prometheus_text()
    assert 'clitg_text_requests_queued_total{engine="claude"} 1' in rendered
    assert 'clitg_text_requests_total{engine="claude"' not in rendered
    queued_items = await inbound_queue.list_scope(scope_key="101:202:0", user_id=101)
    assert len(queued_items) == 1
    assert (
        queued_items[0].payload[message_handler._INBOUND_QUEUE_ENQUEUED_MONOTONIC_KEY]
        > 0
    )


@pytest.mark.asyncio
async def test_handle_text_message_concurrent_scope_reservation_enqueues_second_request(
    tmp_path: Path,
) -> None:
    """Concurrent text messages in the same scope should serialize via queue."""
    runtime_metrics = RuntimeMetrics(enabled=False)
    inbound_queue = InboundTaskQueue(max_per_scope=5)
    first_progress_ready = asyncio.Event()
    release_first_progress = asyncio.Event()
    fake_integration = SimpleNamespace(
        run_command=AsyncMock(
            return_value=SimpleNamespace(
                content="final answer",
                session_id="sess-2",
                cost=0.0,
                num_turns=1,
            )
        )
    )
    context = SimpleNamespace(
        bot_data={
            "settings": _build_settings(tmp_path),
            "runtime_metrics": runtime_metrics,
            "task_registry": TaskRegistry(metrics=runtime_metrics),
            "inbound_task_queue": inbound_queue,
            "storage": None,
        },
        bot=SimpleNamespace(),
        user_data={},
    )
    first_update = _build_update(text="first")
    second_update = _build_update(text="second")
    first_progress_msg = SimpleNamespace(
        message_id=188,
        edit_text=AsyncMock(),
        edit_reply_markup=AsyncMock(),
        delete=AsyncMock(),
    )
    queue_prompt_msg = SimpleNamespace(message_id=255)

    async def _fake_reply(message, text, **kwargs):
        if message is first_update.message and "正在处理你的请求" in text:
            first_progress_ready.set()
            await release_first_progress.wait()
            return first_progress_msg
        if "已加入队列" in text:
            return queue_prompt_msg
        return SimpleNamespace(message_id=299)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            message_handler,
            "get_scope_state_from_update",
            lambda **kwargs: ("101:202:0", {}),
        )
        mp.setattr(
            message_handler,
            "get_cli_integration",
            lambda **kwargs: ("claude", fake_integration),
        )
        mp.setattr(message_handler, "_reply_text_resilient", _fake_reply)
        mp.setattr(message_handler, "_send_chat_action_heartbeat", AsyncMock())
        mp.setattr(message_handler, "_set_message_reaction_safe", AsyncMock())
        mp.setattr(message_handler, "build_permission_handler", lambda **kwargs: None)
        mp.setattr(
            message_handler,
            "_resolve_pending_reaction_feedback",
            lambda **kwargs: (None, None),
        )
        mp.setattr(
            message_handler,
            "_compose_prompt_with_reaction_feedback",
            lambda text, feedback: text,
        )
        mp.setattr(
            message_handler,
            "_compose_prompt_with_telegram_remote_context",
            lambda prompt, settings: prompt,
        )
        mp.setattr(
            message_handler,
            "_update_working_directory_from_claude_response",
            lambda *args, **kwargs: None,
        )
        mp.setattr(
            message_handler,
            "_enforce_no_local_image_fallback_for_image_gen",
            lambda response: False,
        )
        mp.setattr(message_handler, "_build_context_tag", lambda **kwargs: None)
        mp.setattr(
            message_handler,
            "_resolve_collapsed_fallback_model",
            lambda **kwargs: None,
        )
        mp.setattr(
            message_handler,
            "dispatch_next_queued_task_if_idle",
            AsyncMock(return_value=False),
        )
        mp.setattr(
            message_handler,
            "_send_generated_images_from_response",
            AsyncMock(),
        )
        mp.setattr(
            message_handler,
            "_send_generated_files_from_response",
            AsyncMock(),
        )
        mp.setattr("src.bot.utils.formatting.ResponseFormatter", _FakeFormatter)

        first_task = asyncio.create_task(
            message_handler.handle_text_message(first_update, context)
        )
        await asyncio.wait_for(first_progress_ready.wait(), timeout=1.0)

        second_task = asyncio.create_task(
            message_handler.handle_text_message(second_update, context)
        )
        await asyncio.wait_for(second_task, timeout=1.0)

        queued_items = await inbound_queue.list_scope(
            scope_key="101:202:0", user_id=101
        )
        assert len(queued_items) == 1
        assert queued_items[0].preview == "second"

        release_first_progress.set()
        await asyncio.wait_for(first_task, timeout=1.0)

    rendered = runtime_metrics.render_prometheus_text()
    assert 'clitg_text_requests_queued_total{engine="claude"} 1' in rendered
