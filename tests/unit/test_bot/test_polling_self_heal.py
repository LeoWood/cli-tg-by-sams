"""Tests for polling self-heal and watchdog behavior."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.bot import core as core_module
from src.bot.core import ClaudeCodeBot
from src.exceptions import ClaudeCodeTelegramError


def test_polling_error_callback_flags_restart_after_threshold() -> None:
    """Repeated polling network errors should flag self-recovery."""
    bot = ClaudeCodeBot(settings=SimpleNamespace(), dependencies={})

    for _ in range(core_module._POLLING_RECOVERY_ERROR_THRESHOLD):
        bot._polling_error_callback(RuntimeError("network failure"))

    assert bot._polling_restart_requested is True


def test_transport_failure_report_flags_restart_after_threshold() -> None:
    """Repeated Telegram transport failures should also flag self-recovery."""
    bot = ClaudeCodeBot(settings=SimpleNamespace(), dependencies={})

    for _ in range(core_module._POLLING_RECOVERY_ERROR_THRESHOLD):
        bot.report_telegram_transport_failure(
            error=RuntimeError("Pool timeout: request timed out"),
            source="unit_test",
        )

    assert bot._polling_restart_requested is True


@pytest.mark.asyncio
async def test_polling_health_probe_failure_flags_restart_after_threshold() -> None:
    """Health probe transport failures should flow into the same recovery gate."""
    bot = ClaudeCodeBot(
        settings=SimpleNamespace(webhook_url=None),
        dependencies={},
    )
    bot.app = SimpleNamespace(
        updater=SimpleNamespace(running=True),
        bot=SimpleNamespace(
            get_me=AsyncMock(side_effect=RuntimeError("Pool timeout: request timed out")),
            get_webhook_info=AsyncMock(),
        ),
    )

    for idx in range(core_module._POLLING_RECOVERY_ERROR_THRESHOLD):
        bot._last_health_probe_monotonic = -1000.0
        await bot._run_polling_health_probe(now=1000.0 + (idx * 61.0))

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
    """Watchdog should self-heal stalled progress only when pending updates exist."""
    now = asyncio.get_running_loop().time()
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
    bot._last_pending_update_count = 2
    bot._pending_update_nonzero_since_monotonic = now - 11.0
    bot._last_update_progress_monotonic = now - 11.0
    bot._restart_polling = AsyncMock(return_value=True)  # type: ignore[method-assign]

    await bot._polling_watchdog_tick()

    bot._restart_polling.assert_awaited_once_with(reason="update_stall_watchdog")


@pytest.mark.asyncio
async def test_watchdog_skips_update_stall_without_pending_updates() -> None:
    """Update-stall watchdog should ignore normal idle periods without queue backlog."""
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
    bot._last_pending_update_count = 0
    bot._pending_update_nonzero_since_monotonic = 0.0
    bot._last_update_progress_monotonic = asyncio.get_running_loop().time() - 1000.0
    bot._restart_polling = AsyncMock(return_value=True)  # type: ignore[method-assign]

    await bot._polling_watchdog_tick()

    bot._restart_polling.assert_not_awaited()


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


@pytest.mark.asyncio
async def test_restart_polling_timeout_fails_fast_and_escalates() -> None:
    """Hung polling restart should escalate and raise instead of staying fake-alive."""

    gate = asyncio.Event()

    async def _hung_stop() -> None:
        await gate.wait()

    updater = SimpleNamespace(
        running=True,
        stop=AsyncMock(side_effect=_hung_stop),
        start_polling=AsyncMock(),
    )
    bot = ClaudeCodeBot(
        settings=SimpleNamespace(webhook_url=None),
        dependencies={},
    )
    bot.app = SimpleNamespace(updater=updater)
    bot._trigger_escalated_restart = AsyncMock(return_value=True)  # type: ignore[method-assign]
    bot._get_polling_restart_timeout_seconds = lambda: 0.01  # type: ignore[method-assign]

    with pytest.raises(ClaudeCodeTelegramError, match="Unrecoverable polling"):
        await bot._restart_polling(reason="timeout_case")

    updater.stop.assert_awaited_once()
    updater.start_polling.assert_not_awaited()
    bot._trigger_escalated_restart.assert_awaited_once_with(
        reason="fatal:timeout_case"
    )
    assert bot._fast_fail_shutdown_requested is True


@pytest.mark.asyncio
async def test_stop_skips_graceful_shutdown_after_fast_fail() -> None:
    """Fast-fail shutdown path should not block on updater/application shutdown."""
    updater = SimpleNamespace(running=True, stop=AsyncMock())
    app = SimpleNamespace(
        updater=updater,
        stop=AsyncMock(),
        shutdown=AsyncMock(),
    )
    bot = ClaudeCodeBot(
        settings=SimpleNamespace(webhook_url=None),
        dependencies={},
    )
    bot.app = app
    bot.is_running = True
    bot._fast_fail_shutdown_requested = True

    await bot.stop()

    updater.stop.assert_not_awaited()
    app.stop.assert_not_awaited()
    app.shutdown.assert_not_awaited()
