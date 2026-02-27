"""Unit tests for SessionExporter."""

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.bot.features.session_export import ExportFormat, SessionExporter
from src.storage.models import MessageModel, SessionModel
from src.utils.constants import MAX_SESSION_LENGTH


def _build_storage(session, messages):
    """Build minimal storage stub matching repository shape."""
    return SimpleNamespace(
        sessions=SimpleNamespace(get_session=AsyncMock(return_value=session)),
        messages=SimpleNamespace(get_session_messages=AsyncMock(return_value=messages)),
    )


def _sample_session() -> SessionModel:
    """Create a sample session model."""
    return SessionModel(
        session_id="session-abc-123",
        user_id=1001,
        project_path="/tmp/project",
        created_at=datetime(2026, 2, 20, 10, 0, 0),
        last_used=datetime(2026, 2, 20, 10, 30, 0),
        total_cost=0.12,
        total_turns=3,
        message_count=1,
        is_active=True,
    )


def _sample_messages() -> list[MessageModel]:
    """Create sample message records."""
    return [
        MessageModel(
            message_id=10,
            session_id="session-abc-123",
            user_id=1001,
            timestamp=datetime(2026, 2, 20, 10, 5, 0),
            prompt="Please review this patch.",
            response="Patch reviewed.",
            cost=0.01,
            duration_ms=321,
            error=None,
        )
    ]


@pytest.mark.asyncio
async def test_export_session_markdown_uses_repository_interfaces():
    """Exporter should read via repositories and produce markdown output."""
    session = _sample_session()
    messages = _sample_messages()
    storage = _build_storage(session, messages)
    exporter = SessionExporter(storage=storage)

    result = await exporter.export_session(
        user_id=1001,
        session_id=session.session_id,
        format=ExportFormat.MARKDOWN,
    )

    storage.sessions.get_session.assert_awaited_once_with(session.session_id)
    storage.messages.get_session_messages.assert_awaited_once_with(
        session.session_id, limit=MAX_SESSION_LENGTH
    )
    assert result.format == ExportFormat.MARKDOWN
    assert "CLITG Session Export" in result.content
    assert "Please review this patch." in result.content
    assert "Patch reviewed." in result.content
    assert result.filename.endswith(".md")
    assert result.size_bytes > 0


@pytest.mark.asyncio
async def test_export_session_json_maps_storage_fields():
    """Exporter JSON should include session + prompt/response data."""
    session = _sample_session()
    messages = _sample_messages()
    storage = _build_storage(session, messages)
    exporter = SessionExporter(storage=storage)

    result = await exporter.export_session(
        user_id=1001,
        session_id=session.session_id,
        format=ExportFormat.JSON,
    )
    payload = json.loads(result.content)

    assert payload["session"]["id"] == session.session_id
    assert payload["session"]["project_path"] == session.project_path
    assert payload["messages"][0]["prompt"] == messages[0].prompt
    assert payload["messages"][0]["response"] == messages[0].response
    assert result.filename.endswith(".json")


@pytest.mark.asyncio
async def test_export_session_html_escapes_message_content():
    """HTML export should escape raw message content safely."""
    session = _sample_session()
    messages = _sample_messages()
    messages[0].prompt = "<script>alert(1)</script>"
    storage = _build_storage(session, messages)
    exporter = SessionExporter(storage=storage)

    result = await exporter.export_session(
        user_id=1001,
        session_id=session.session_id,
        format=ExportFormat.HTML,
    )

    assert result.filename.endswith(".html")
    assert "<html" in result.content.lower()
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in result.content


@pytest.mark.asyncio
async def test_export_session_raises_when_session_missing_or_not_owned():
    """Exporter should reject unknown sessions and cross-user access."""
    storage_missing = _build_storage(None, [])
    exporter_missing = SessionExporter(storage=storage_missing)
    with pytest.raises(ValueError, match="not found"):
        await exporter_missing.export_session(
            user_id=1001,
            session_id="missing-session",
            format=ExportFormat.MARKDOWN,
        )

    foreign_session = _sample_session()
    foreign_session.user_id = 9999
    storage_foreign = _build_storage(foreign_session, _sample_messages())
    exporter_foreign = SessionExporter(storage=storage_foreign)
    with pytest.raises(ValueError, match="not found"):
        await exporter_foreign.export_session(
            user_id=1001,
            session_id=foreign_session.session_id,
            format=ExportFormat.MARKDOWN,
        )
