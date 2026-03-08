"""Tests for generic action callback dispatch."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from src.bot.handlers import callback as callback_handler


def _build_main_quick_actions_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🆕 New", callback_data="action:new_session"),
                InlineKeyboardButton(
                    "📋 Projects", callback_data="action:show_projects"
                ),
                InlineKeyboardButton("📊 Status", callback_data="action:status"),
            ],
        ]
    )


def test_build_main_quick_actions_reply_markup_uses_main_menu_buttons():
    """Callback helper should generate the same three-button main menu."""
    markup = callback_handler._build_main_quick_actions_reply_markup()
    callbacks = [
        button.callback_data for row in markup.inline_keyboard for button in row
    ]

    assert callbacks == [
        "action:new_session",
        "action:show_projects",
        "action:status",
    ]


@pytest.mark.asyncio
async def test_handle_action_callback_routes_resume(monkeypatch):
    """`action:resume` should dispatch to resume action handler."""
    resume_handler = AsyncMock()
    monkeypatch.setattr(callback_handler, "_handle_resume_action", resume_handler)

    query = object()
    context = object()

    await callback_handler.handle_action_callback(query, "resume", context)

    resume_handler.assert_awaited_once_with(query, context)


@pytest.mark.asyncio
async def test_handle_action_callback_wraps_main_menu_actions(monkeypatch):
    """Main quick-action callbacks should preserve source message content."""
    status_handler = AsyncMock()
    monkeypatch.setattr(callback_handler, "_handle_status_action", status_handler)

    query = SimpleNamespace(
        message=SimpleNamespace(reply_markup=_build_main_quick_actions_keyboard())
    )
    context = SimpleNamespace()

    await callback_handler.handle_action_callback(query, "status", context)

    dispatched_query = status_handler.await_args.args[0]
    assert dispatched_query is not query
    assert isinstance(
        dispatched_query, callback_handler._PreserveSourceMessageQueryProxy
    )


@pytest.mark.asyncio
async def test_callback_dispatch_reports_transport_failure(monkeypatch):
    """Callback transport failures should be reported to the runtime recovery gate."""
    report_transport_failure = Mock()
    monkeypatch.setattr(
        callback_handler,
        "_is_callback_query_authenticated",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        callback_handler,
        "handle_action_callback",
        AsyncMock(side_effect=RuntimeError("Pool timeout: request timed out")),
    )
    monkeypatch.setattr(
        callback_handler,
        "_edit_query_message_resilient",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        callback_handler,
        "_reply_query_message_resilient",
        AsyncMock(return_value=None),
    )

    query = SimpleNamespace(
        from_user=SimpleNamespace(id=12345),
        data="action:new_session",
        answer=AsyncMock(),
    )
    update = SimpleNamespace(callback_query=query)
    context = SimpleNamespace(
        bot_data={
            "bot_runtime": SimpleNamespace(
                report_telegram_transport_failure=report_transport_failure
            )
        }
    )

    await callback_handler.handle_callback_query(update, context)

    report_transport_failure.assert_called_once()
    call_kwargs = report_transport_failure.call_args.kwargs
    assert call_kwargs["source"] == "callback:action"


@pytest.mark.asyncio
async def test_preserve_source_query_proxy_replies_once_then_edits():
    """Proxy should create a new reply bubble, then edit that bubble."""
    target_message = SimpleNamespace(
        edit_text=AsyncMock(), edit_reply_markup=AsyncMock()
    )
    source_message = SimpleNamespace(
        message_id=42,
        reply_text=AsyncMock(return_value=target_message),
    )
    query = SimpleNamespace(
        message=source_message,
        edit_message_reply_markup=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    proxy = callback_handler._PreserveSourceMessageQueryProxy(query, SimpleNamespace())

    await proxy.edit_message_text("loading", parse_mode="Markdown")
    await proxy.edit_message_text("done")

    query.edit_message_reply_markup.assert_awaited_once_with(reply_markup=None)
    source_message.reply_text.assert_awaited_once_with(
        "loading", parse_mode="Markdown", reply_to_message_id=42
    )
    query.edit_message_text.assert_not_called()
    target_message.edit_text.assert_awaited_once_with("done")


@pytest.mark.asyncio
async def test_handle_resume_action_uses_current_directory(monkeypatch, tmp_path):
    """Resume action should target current directory directly."""

    class _TokenManagerStub:
        def __init__(self):
            self.kind = ""
            self.user_id = 0
            self.payload = {}

        def issue(self, *, kind, user_id, payload):
            self.kind = kind
            self.user_id = user_id
            self.payload = payload
            return "project-token"

    token_mgr = _TokenManagerStub()
    scanner = object()
    resume_select = AsyncMock()
    current_dir = tmp_path / "demo-project"
    scope_state = {
        "current_directory": str(current_dir),
        "active_cli_engine": "claude",
    }

    monkeypatch.setattr(
        callback_handler,
        "_get_scope_state_for_query",
        lambda query, context: ("scope", scope_state),
    )
    monkeypatch.setattr(
        callback_handler,
        "_get_or_create_resume_scanner",
        lambda **kwargs: scanner,
    )
    monkeypatch.setattr(
        callback_handler,
        "_get_or_create_resume_token_manager",
        lambda context: token_mgr,
    )
    monkeypatch.setattr(callback_handler, "_resume_select_project", resume_select)

    settings = SimpleNamespace(approved_directory=tmp_path)
    context = SimpleNamespace(bot_data={"settings": settings}, user_data={})
    query = SimpleNamespace(from_user=SimpleNamespace(id=12345))

    await callback_handler._handle_resume_action(query, context)

    assert token_mgr.kind == "p"
    assert token_mgr.user_id == 12345
    assert token_mgr.payload["cwd"] == str(current_dir.resolve())
    assert token_mgr.payload["engine"] == "claude"

    resume_select.assert_awaited_once()
    args = resume_select.await_args.args
    kwargs = resume_select.await_args.kwargs
    assert args[0] is query
    assert args[1] == 12345
    assert args[2] == "project-token"
    assert args[3] is token_mgr
    assert args[4] is scanner
    assert args[5] is settings
    assert args[6] is context
    assert kwargs["engine"] == "claude"
