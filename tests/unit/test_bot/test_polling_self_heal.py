"""Tests for polling self-heal and watchdog behavior."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.bot import core as core_module
from src.bot.core import ClaudeCodeBot


def test_polling_error_callback_flags_restart_after_threshold() -> None:
    """Repeated polling network errors should flag self-recovery."""
    bot = ClaudeCodeBot(settings=SimpleNamespace(), dependencies={})

    for _ in range(core_module._POLLING_RECOVERY_ERROR_THRESHOLD):
        bot._polling_error_callback(RuntimeError("network failure"))

    assert bot._polling_restart_requested is True


@pytest.mark.asyncio
async def test_restart_polling_stops_then_starts_updater() -> None:
    """Polling restart should stop current updater and start a new polling loop."""
    updater = SimpleNamespace(
        running=True,
        stop=AsyncMock(),
        start_polling=AsyncMock(),
    )
    bot = ClaudeCodeBot(
        settings=SimpleNamespace(webhook_url=None),
        dependencies={},
    )
    bot.app = SimpleNamespace(updater=updater)
    bot._polling_restart_requested = True
    bot._polling_error_count = 9

    restarted = await bot._restart_polling(reason="unit_test")

    assert restarted is True
    updater.stop.assert_awaited_once()
    updater.start_polling.assert_awaited_once()
    kwargs = updater.start_polling.await_args.kwargs
    assert kwargs["drop_pending_updates"] is False
    assert kwargs["bootstrap_retries"] == 10
    assert kwargs["error_callback"] == bot._polling_error_callback
    assert bot._polling_restart_requested is False
    assert bot._polling_error_count == 0


@pytest.mark.asyncio
async def test_restart_polling_respects_restart_cooldown() -> None:
    """Restart attempts inside cooldown window should be skipped."""
    updater = SimpleNamespace(
        running=False,
        stop=AsyncMock(),
        start_polling=AsyncMock(),
    )
    bot = ClaudeCodeBot(
        settings=SimpleNamespace(webhook_url=None),
        dependencies={},
    )
    bot.app = SimpleNamespace(updater=updater)
    bot._last_polling_restart_monotonic = asyncio.get_running_loop().time()

    restarted = await bot._restart_polling(reason="cooldown")

    assert restarted is False
    updater.stop.assert_not_awaited()
    updater.start_polling.assert_not_awaited()


@pytest.mark.asyncio
async def test_watchdog_restarts_when_updater_not_running() -> None:
    """Watchdog should prefer updater-state recovery path."""
    bot = ClaudeCodeBot(
        settings=SimpleNamespace(webhook_url=None),
        dependencies={},
    )
    bot.app = SimpleNamespace(updater=SimpleNamespace(running=False))
    bot._restart_polling = AsyncMock(return_value=True)  # type: ignore[method-assign]

    await bot._polling_watchdog_tick()

    bot._restart_polling.assert_awaited_once_with(reason="updater_not_running")


@pytest.mark.asyncio
async def test_watchdog_restarts_when_error_flag_set() -> None:
    """Watchdog should restart polling when error threshold requested recovery."""
    bot = ClaudeCodeBot(
        settings=SimpleNamespace(webhook_url=None),
        dependencies={},
    )
    bot.app = SimpleNamespace(updater=SimpleNamespace(running=True))
    bot._polling_restart_requested = True
    bot._restart_polling = AsyncMock(return_value=True)  # type: ignore[method-assign]

    await bot._polling_watchdog_tick()

    bot._restart_polling.assert_awaited_once_with(reason="network_error_threshold")


@pytest.mark.asyncio
async def test_watchdog_restarts_when_update_progress_stalls() -> None:
    """Watchdog should self-heal when no updates are processed for too long."""
    bot = ClaudeCodeBot(
        settings=SimpleNamespace(
            webhook_url=None,
            polling_update_stall_seconds=10.0,
        ),
        dependencies={},
    )
    bot.app = SimpleNamespace(updater=SimpleNamespace(running=True))
    bot._polling_restart_requested = False
    bot._last_update_id = 123
    bot._last_update_progress_monotonic = asyncio.get_running_loop().time() - 11.0
    bot._restart_polling = AsyncMock(return_value=True)  # type: ignore[method-assign]

    await bot._polling_watchdog_tick()

    bot._restart_polling.assert_awaited_once_with(reason="update_stall_watchdog")


@pytest.mark.asyncio
async def test_watchdog_skips_update_stall_when_disabled() -> None:
    """Stall watchdog should not trigger when threshold is disabled."""
    bot = ClaudeCodeBot(
        settings=SimpleNamespace(
            webhook_url=None,
            polling_update_stall_seconds=0.0,
        ),
        dependencies={},
    )
    bot.app = SimpleNamespace(updater=SimpleNamespace(running=True))
    bot._polling_restart_requested = False
    bot._last_update_id = 123
    bot._last_update_progress_monotonic = asyncio.get_running_loop().time() - 1000.0
    bot._restart_polling = AsyncMock(return_value=True)  # type: ignore[method-assign]

    await bot._polling_watchdog_tick()

    bot._restart_polling.assert_not_awaited()


@pytest.mark.asyncio
async def test_watchdog_restarts_when_pending_updates_stall() -> None:
    """Watchdog should restart when Telegram has pending updates but no progress."""
    now = asyncio.get_running_loop().time()
    bot = ClaudeCodeBot(
        settings=SimpleNamespace(
            webhook_url=None,
            polling_update_stall_seconds=0.0,
            polling_pending_update_stall_seconds=10.0,
        ),
        dependencies={},
    )
    bot.app = SimpleNamespace(updater=SimpleNamespace(running=True))
    bot._run_polling_health_probe = AsyncMock()  # type: ignore[method-assign]
    bot._polling_restart_requested = False
    bot._last_pending_update_count = 3
    bot._pending_update_nonzero_since_monotonic = now - 12.0
    bot._last_update_progress_monotonic = now - 12.0
    bot._restart_polling = AsyncMock(return_value=True)  # type: ignore[method-assign]

    await bot._polling_watchdog_tick()

    bot._restart_polling.assert_awaited_once_with(reason="pending_updates_stalled")


@pytest.mark.asyncio
async def test_watchdog_skips_pending_update_stall_when_no_pending_updates() -> None:
    """Pending-update stall path should not trigger when queue appears empty."""
    now = asyncio.get_running_loop().time()
    bot = ClaudeCodeBot(
        settings=SimpleNamespace(
            webhook_url=None,
            polling_update_stall_seconds=0.0,
            polling_pending_update_stall_seconds=10.0,
        ),
        dependencies={},
    )
    bot.app = SimpleNamespace(updater=SimpleNamespace(running=True))
    bot._run_polling_health_probe = AsyncMock()  # type: ignore[method-assign]
    bot._polling_restart_requested = False
    bot._last_pending_update_count = 0
    bot._pending_update_nonzero_since_monotonic = now - 12.0
    bot._last_update_progress_monotonic = now - 12.0
    bot._restart_polling = AsyncMock(return_value=True)  # type: ignore[method-assign]

    await bot._polling_watchdog_tick()

    bot._restart_polling.assert_not_awaited()


@pytest.mark.asyncio
async def test_restart_polling_opens_circuit_breaker_after_too_many_attempts() -> None:
    """Circuit breaker should block polling restarts after repeated attempts."""
    updater = SimpleNamespace(
        running=True,
        stop=AsyncMock(),
        start_polling=AsyncMock(),
    )
    bot = ClaudeCodeBot(
        settings=SimpleNamespace(webhook_url=None),
        dependencies={},
    )
    bot.app = SimpleNamespace(updater=updater)
    bot._trigger_escalated_restart = AsyncMock(return_value=True)  # type: ignore[method-assign]
    now = asyncio.get_running_loop().time()
    threshold = core_module._POLLING_RECOVERY_MAX_RESTARTS_PER_WINDOW
    bot._polling_restart_attempts_monotonic.extend([now - 1.0] * threshold)

    restarted = await bot._restart_polling(reason="circuit_threshold")

    assert restarted is False
    updater.stop.assert_not_awaited()
    updater.start_polling.assert_not_awaited()
    assert bot._polling_recovery_circuit_open_until_monotonic > now
    bot._trigger_escalated_restart.assert_awaited_once_with(
        reason="circuit_breaker:circuit_threshold"
    )


@pytest.mark.asyncio
async def test_restart_polling_skips_when_circuit_breaker_open() -> None:
    """Open circuit breaker should skip polling self-recovery attempts."""
    updater = SimpleNamespace(
        running=True,
        stop=AsyncMock(),
        start_polling=AsyncMock(),
    )
    bot = ClaudeCodeBot(
        settings=SimpleNamespace(webhook_url=None),
        dependencies={},
    )
    bot.app = SimpleNamespace(updater=updater)
    bot._trigger_escalated_restart = AsyncMock(return_value=True)  # type: ignore[method-assign]
    now = asyncio.get_running_loop().time()
    bot._polling_recovery_circuit_open_until_monotonic = now + 30.0

    restarted = await bot._restart_polling(reason="circuit_open")

    assert restarted is False
    updater.stop.assert_not_awaited()
    updater.start_polling.assert_not_awaited()
    bot._trigger_escalated_restart.assert_not_awaited()
