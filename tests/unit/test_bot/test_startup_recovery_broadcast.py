"""Tests for startup recovery broadcast notifications."""

import pickle
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.bot import core as core_module
from src.bot.core import ClaudeCodeBot


def test_collect_startup_notification_targets_deduplicates_scopes() -> None:
    """Startup targets should be unique by chat/topic pair."""
    bot = ClaudeCodeBot(
        settings=SimpleNamespace(telegram_user_data_persistence_path=None),
        dependencies={},
    )
    bot.app = SimpleNamespace(
        user_data={
            1001: {
                "scope_state": {
                    "1001:-2001:0": {},
                    "1001:-2001:55": {},
                    "1001:3001:1": {},
                }
            },
            1002: {
                "scope_state": {
                    "1002:-2001:55": {},
                    "1002:3001:0": {},
                    "1002:-2002:0": {},
                }
            },
        }
    )

    targets = bot._collect_startup_notification_targets()

    assert targets == [(-2002, None), (-2001, None), (-2001, 55), (3001, None)]


@pytest.mark.asyncio
async def test_broadcast_startup_recovery_notification_sends_all_targets(
    monkeypatch,
) -> None:
    """Broadcast should send one message to each unique scope target."""
    send_mock = AsyncMock()
    monkeypatch.setattr(core_module, "send_message_resilient", send_mock)

    bot = ClaudeCodeBot(
        settings=SimpleNamespace(telegram_user_data_persistence_path=None),
        dependencies={},
    )
    bot.app = SimpleNamespace(
        bot=SimpleNamespace(),
        user_data={
            1001: {
                "scope_state": {
                    "1001:-2001:0": {},
                    "1001:-2001:55": {},
                }
            }
        },
    )

    await bot._broadcast_startup_recovery_notification()

    assert send_mock.await_count == 2
    first = send_mock.await_args_list[0].kwargs
    second = send_mock.await_args_list[1].kwargs
    assert first["chat_id"] == -2001 and first["message_thread_id"] is None
    assert second["chat_id"] == -2001 and second["message_thread_id"] == 55


def test_collect_startup_notification_targets_reads_persistence_file(tmp_path) -> None:
    """When app user_data is empty, startup targets should fallback to pickle file."""
    persistence_path = tmp_path / "telegram-user-data.pkl"
    payload = {
        "user_data": {
            1001: {"scope_state": {"1001:-3001:0": {}, "1001:-3001:7": {}}},
            1002: {"scope_state": {"1002:5001:0": {}}},
        }
    }
    with persistence_path.open("wb") as f:
        pickle.dump(payload, f)

    bot = ClaudeCodeBot(
        settings=SimpleNamespace(
            telegram_user_data_persistence_path=persistence_path,
        ),
        dependencies={},
    )
    bot.app = SimpleNamespace(user_data={})

    targets = bot._collect_startup_notification_targets()

    assert targets == [(-3001, None), (-3001, 7), (5001, None)]
