"""Tests for project session pagination in /resume callbacks."""

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.bot.handlers import callback as callback_handler
from src.bot.resume_tokens import ResumeTokenManager


class _ScannerStub:
    def __init__(self, candidates):
        self._candidates = candidates

    async def list_sessions(self, project_cwd: Path):
        return list(self._candidates)


def _candidate(index: int) -> SimpleNamespace:
    return SimpleNamespace(
        session_id=f"session-{index}",
        is_probably_active=False,
        thread_name=f"thread {index}",
        first_message=f"first {index}",
        last_user_message=f"latest {index}",
        previous_user_message=f"previous {index}",
        last_event_at=datetime.utcnow() - timedelta(minutes=index),
        file_mtime=datetime.utcnow() - timedelta(minutes=index),
    )


@pytest.mark.asyncio
async def test_resume_select_project_shows_five_sessions_and_more_button(
    monkeypatch, tmp_path
):
    """Project selection should show five sessions by default and a more button."""
    edited = AsyncMock()
    monkeypatch.setattr(callback_handler, "_edit_query_message_resilient", edited)

    project = tmp_path / "project"
    project.mkdir()
    settings = SimpleNamespace(approved_directory=tmp_path)
    context = SimpleNamespace()
    query = SimpleNamespace()
    token_mgr = ResumeTokenManager()
    token = token_mgr.issue(
        kind="p",
        user_id=123,
        payload={"cwd": str(project), "engine": "codex"},
    )
    scanner = _ScannerStub([_candidate(i) for i in range(8)])

    await callback_handler._resume_select_project(
        query,
        123,
        token,
        token_mgr,
        scanner,
        settings,
        context,
        engine="codex",
    )

    kwargs = edited.await_args.kwargs
    keyboard = kwargs["reply_markup"].inline_keyboard
    labels = [button.text for row in keyboard for button in row]

    assert "Showing `1-5` of `8`." in edited.await_args.args[1]
    assert sum(label.startswith("刚刚") or "分钟前" in label for label in labels) == 5
    assert any(label.startswith("📚 More Sessions") for label in labels)
    assert any(label == "🆕 Start New Session Here" for label in labels)


@pytest.mark.asyncio
async def test_resume_select_project_more_button_uses_next_offset(
    monkeypatch, tmp_path
):
    """A project token with offset should render the next session page."""
    edited = AsyncMock()
    monkeypatch.setattr(callback_handler, "_edit_query_message_resilient", edited)

    project = tmp_path / "project"
    project.mkdir()
    settings = SimpleNamespace(approved_directory=tmp_path)
    context = SimpleNamespace()
    query = SimpleNamespace()
    token_mgr = ResumeTokenManager()
    token = token_mgr.issue(
        kind="p",
        user_id=123,
        payload={"cwd": str(project), "engine": "codex", "offset": 5},
    )
    scanner = _ScannerStub([_candidate(i) for i in range(8)])

    await callback_handler._resume_select_project(
        query,
        123,
        token,
        token_mgr,
        scanner,
        settings,
        context,
        engine="codex",
    )

    labels = [
        button.text
        for row in edited.await_args.kwargs["reply_markup"].inline_keyboard
        for button in row
    ]

    assert "Showing `6-8` of `8`." in edited.await_args.args[1]
    assert sum(label.startswith("刚刚") or "分钟前" in label for label in labels) == 3
    assert not any(label.startswith("📚 More Sessions") for label in labels)
    assert any(label == "🆕 Start New Session Here" for label in labels)
