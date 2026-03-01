"""Tests for /restartbot remote restart command."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.bot.handlers.command import restart_bot_command


def _build_update(
    user_id: int, chat_id: int, message_thread_id: int | None = None
) -> SimpleNamespace:
    """Build minimal Telegram update stub."""
    message = SimpleNamespace(
        message_id=42,
        message_thread_id=message_thread_id,
        reply_text=AsyncMock(),
    )
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        effective_chat=SimpleNamespace(id=chat_id),
        effective_message=message,
        message=message,
    )


@pytest.mark.asyncio
async def test_restartbot_schedules_detached_dispatcher(tmp_path, monkeypatch):
    """`/restartbot` should ack first then spawn detached dispatcher process."""
    user_id = 9101
    chat_id = 9201
    update = _build_update(user_id=user_id, chat_id=chat_id)
    audit_logger = SimpleNamespace(log_command=AsyncMock())
    context = SimpleNamespace(
        bot_data={
            "settings": SimpleNamespace(approved_directory=tmp_path),
            "audit_logger": audit_logger,
        },
        user_data={},
    )

    dispatcher = SimpleNamespace(pid=7788)
    create_exec = AsyncMock(return_value=dispatcher)
    monkeypatch.setattr(
        "src.bot.handlers.command.asyncio.create_subprocess_exec",
        create_exec,
    )
    monkeypatch.setattr(
        "src.bot.handlers.command.shutil.which",
        lambda cmd: "/usr/bin/tmux" if cmd == "tmux" else None,
    )
    monkeypatch.setattr(
        "src.bot.handlers.command.uuid.uuid4",
        lambda: uuid.UUID("12345678-1234-5678-9abc-def012345678"),
    )

    await restart_bot_command(update, context)

    reply_call = update.message.reply_text.await_args
    assert "request_id" in reply_call.args[0]
    assert "123456781234" in reply_call.args[0]
    assert reply_call.kwargs.get("parse_mode") == "Markdown"

    create_call = create_exec.await_args
    assert create_call.args[0] == "bash"
    assert create_call.args[1].endswith("scripts/restart-from-telegram.sh")
    assert create_call.args[2] == "123456781234"
    assert create_call.args[3] == str(user_id)
    assert create_call.args[4] == str(chat_id)
    assert create_call.args[5] == "0"
    assert create_call.kwargs["start_new_session"] is True
    assert create_call.kwargs["cwd"].endswith("cli-tg-by-sams")

    audit_logger.log_command.assert_awaited_once()
    assert audit_logger.log_command.await_args.kwargs["command"] == "restartbot"
    assert audit_logger.log_command.await_args.kwargs["success"] is True


@pytest.mark.asyncio
async def test_restartbot_rejects_when_tmux_missing(tmp_path, monkeypatch):
    """`/restartbot` should fail fast with clear message when tmux is missing."""
    update = _build_update(user_id=9301, chat_id=9401)
    audit_logger = SimpleNamespace(log_command=AsyncMock())
    context = SimpleNamespace(
        bot_data={
            "settings": SimpleNamespace(approved_directory=tmp_path),
            "audit_logger": audit_logger,
        },
        user_data={},
    )

    create_exec = AsyncMock()
    monkeypatch.setattr(
        "src.bot.handlers.command.asyncio.create_subprocess_exec",
        create_exec,
    )
    monkeypatch.setattr(
        "src.bot.handlers.command.shutil.which",
        lambda cmd: None if cmd == "tmux" else "/usr/bin/mock",
    )

    await restart_bot_command(update, context)

    create_exec.assert_not_awaited()
    reply_call = update.message.reply_text.await_args
    assert "tmux" in reply_call.args[0]
    assert reply_call.kwargs.get("parse_mode") == "Markdown"

    audit_logger.log_command.assert_awaited_once()
    assert audit_logger.log_command.await_args.kwargs["command"] == "restartbot"
    assert audit_logger.log_command.await_args.kwargs["success"] is False


@pytest.mark.asyncio
async def test_restartbot_passes_message_thread_id_to_dispatcher(tmp_path, monkeypatch):
    """`/restartbot` should pass Telegram topic thread id to dispatcher script."""
    user_id = 9501
    chat_id = -1009501
    thread_id = 77
    update = _build_update(
        user_id=user_id,
        chat_id=chat_id,
        message_thread_id=thread_id,
    )
    audit_logger = SimpleNamespace(log_command=AsyncMock())
    context = SimpleNamespace(
        bot_data={
            "settings": SimpleNamespace(approved_directory=tmp_path),
            "audit_logger": audit_logger,
        },
        user_data={},
    )

    dispatcher = SimpleNamespace(pid=8899)
    create_exec = AsyncMock(return_value=dispatcher)
    monkeypatch.setattr(
        "src.bot.handlers.command.asyncio.create_subprocess_exec",
        create_exec,
    )
    monkeypatch.setattr(
        "src.bot.handlers.command.shutil.which",
        lambda cmd: "/usr/bin/tmux" if cmd == "tmux" else None,
    )

    await restart_bot_command(update, context)

    create_call = create_exec.await_args
    assert create_call.args[3] == str(user_id)
    assert create_call.args[4] == str(chat_id)
    assert create_call.args[5] == str(thread_id)


@pytest.mark.asyncio
async def test_restartbot_flushes_persistence_before_dispatch(tmp_path, monkeypatch):
    """`/restartbot` should flush PTB persistence before spawning dispatcher."""
    user_id = 9601
    chat_id = 9701
    update = _build_update(user_id=user_id, chat_id=chat_id)
    audit_logger = SimpleNamespace(log_command=AsyncMock())
    event_order: list[str] = []

    async def _flush_persistence():
        event_order.append("flush")

    application = SimpleNamespace(update_persistence=AsyncMock(side_effect=_flush_persistence))
    context = SimpleNamespace(
        bot_data={
            "settings": SimpleNamespace(approved_directory=tmp_path),
            "audit_logger": audit_logger,
        },
        user_data={},
        application=application,
    )

    dispatcher = SimpleNamespace(pid=9988)

    async def _spawn_dispatcher(*args, **kwargs):
        event_order.append("spawn")
        return dispatcher

    create_exec = AsyncMock(side_effect=_spawn_dispatcher)
    monkeypatch.setattr(
        "src.bot.handlers.command.asyncio.create_subprocess_exec",
        create_exec,
    )
    monkeypatch.setattr(
        "src.bot.handlers.command.shutil.which",
        lambda cmd: "/usr/bin/tmux" if cmd == "tmux" else None,
    )

    await restart_bot_command(update, context)

    application.update_persistence.assert_awaited_once()
    create_exec.assert_awaited_once()
    assert event_order[:2] == ["flush", "spawn"]

    reply_call = update.message.reply_text.await_args
    assert "已完成会话状态持久化" in reply_call.args[0]
