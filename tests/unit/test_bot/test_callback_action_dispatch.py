"""Tests for generic action callback dispatch."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.bot.handlers import callback as callback_handler


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
