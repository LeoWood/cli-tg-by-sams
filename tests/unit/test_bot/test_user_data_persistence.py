"""Tests for Telegram user_data persistence wiring."""

from types import SimpleNamespace

from telegram.ext import PicklePersistence

from src.bot.core import ClaudeCodeBot


def test_build_user_data_persistence_creates_pickle_persistence(tmp_path):
    """Configured path should enable PicklePersistence for user_data."""
    persistence_path = tmp_path / "state" / "user-data.pkl"
    bot = ClaudeCodeBot(
        settings=SimpleNamespace(
            telegram_user_data_persistence_path=persistence_path,
        ),
        dependencies={},
    )

    persistence = bot._build_user_data_persistence()

    assert isinstance(persistence, PicklePersistence)
    assert persistence_path.parent.exists()
    assert persistence.filepath == persistence_path
    assert persistence.store_data.user_data is True
    assert persistence.store_data.chat_data is False
    assert persistence.store_data.bot_data is False
    assert persistence.store_data.callback_data is False


def test_build_user_data_persistence_can_be_disabled_with_none() -> None:
    """None path should disable persistence."""
    bot = ClaudeCodeBot(
        settings=SimpleNamespace(telegram_user_data_persistence_path=None),
        dependencies={},
    )

    persistence = bot._build_user_data_persistence()

    assert persistence is None


def test_build_user_data_persistence_can_be_disabled_with_blank_string() -> None:
    """Blank string path should disable persistence."""
    bot = ClaudeCodeBot(
        settings=SimpleNamespace(telegram_user_data_persistence_path="   "),
        dependencies={},
    )

    persistence = bot._build_user_data_persistence()

    assert persistence is None
