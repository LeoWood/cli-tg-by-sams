"""Tests for Codex desktop resume scanner."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.bot.utils.codex_resume_scanner import CodexSessionScanner


def _write_session_index(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_codex_session(
    *,
    file_path: Path,
    session_id: str,
    cwd: Path,
    first_message: str,
    previous_message: str | None = None,
    last_message: str | None,
    timestamp: datetime,
) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "timestamp": timestamp.isoformat() + "Z",
            "type": "session_meta",
            "payload": {"id": session_id, "cwd": str(cwd)},
        },
        {
            "timestamp": (timestamp + timedelta(seconds=1)).isoformat() + "Z",
            "type": "event_msg",
            "payload": {"type": "task_started"},
        },
        {
            "timestamp": (timestamp + timedelta(seconds=2)).isoformat() + "Z",
            "type": "event_msg",
            "payload": {"type": "user_message", "message": first_message},
        },
    ]
    if previous_message:
        rows.append(
            {
                "timestamp": (timestamp + timedelta(seconds=3)).isoformat() + "Z",
                "type": "event_msg",
                "payload": {"type": "assistant_message", "message": "ok"},
            }
        )
        rows.append(
            {
                "timestamp": (timestamp + timedelta(seconds=4)).isoformat() + "Z",
                "type": "event_msg",
                "payload": {"type": "user_message", "message": previous_message},
            }
        )
    if last_message:
        rows.append(
            {
                "timestamp": (timestamp + timedelta(seconds=5)).isoformat() + "Z",
                "type": "event_msg",
                "payload": {"type": "assistant_message", "message": "ok"},
            }
        )
        rows.append(
            {
                "timestamp": (timestamp + timedelta(seconds=6)).isoformat() + "Z",
                "type": "event_msg",
                "payload": {"type": "user_message", "message": last_message},
            }
        )
    with open(file_path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    ts = timestamp.replace(tzinfo=timezone.utc).timestamp()
    os.utime(file_path, (ts, ts))


@pytest.mark.asyncio
async def test_codex_scanner_list_projects_filters_by_approved_directory(tmp_path):
    """Only projects under approved root should be listed."""
    approved = tmp_path / "approved"
    approved.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    sessions_dir = tmp_path / ".codex" / "sessions"

    now = datetime.utcnow()
    _write_codex_session(
        file_path=sessions_dir / "2026/02/14/rollout-a.jsonl",
        session_id="session-a",
        cwd=approved / "proj-a",
        first_message="hello a",
        last_message=None,
        timestamp=now,
    )
    _write_codex_session(
        file_path=sessions_dir / "2026/02/14/rollout-b.jsonl",
        session_id="session-b",
        cwd=outside / "proj-b",
        first_message="hello b",
        last_message=None,
        timestamp=now + timedelta(seconds=10),
    )

    scanner = CodexSessionScanner(
        approved_directory=approved,
        cache_ttl_sec=0,
        sessions_dir=sessions_dir,
    )
    projects = await scanner.list_projects()

    assert projects == [(approved / "proj-a").resolve()]


@pytest.mark.asyncio
async def test_codex_scanner_list_sessions_extracts_message_and_activity(tmp_path):
    """Session list should include parsed message preview and activity marker."""
    approved = tmp_path / "approved"
    project = approved / "proj-a"
    project.mkdir(parents=True)
    sessions_dir = tmp_path / ".codex" / "sessions"
    session_index = tmp_path / ".codex" / "session_index.jsonl"

    old_ts = datetime.utcnow() - timedelta(hours=1)
    new_ts = datetime.utcnow() - timedelta(seconds=2)
    old_file = sessions_dir / "2026/02/14/rollout-old.jsonl"
    new_file = sessions_dir / "2026/02/14/rollout-new.jsonl"

    _write_codex_session(
        file_path=old_file,
        session_id="session-old",
        cwd=project,
        first_message="old message",
        previous_message="old previous",
        last_message="old latest",
        timestamp=old_ts,
    )
    _write_codex_session(
        file_path=new_file,
        session_id="session-new",
        cwd=project,
        first_message="new message",
        previous_message="new previous",
        last_message="new latest",
        timestamp=new_ts,
    )
    _write_session_index(
        session_index,
        [
            {"id": "session-old", "thread_name": "旧会话标题"},
            {"id": "session-new", "thread_name": "新的会话标题"},
        ],
    )

    scanner = CodexSessionScanner(
        approved_directory=approved,
        cache_ttl_sec=0,
        sessions_dir=sessions_dir,
        session_index_path=session_index,
    )
    sessions = await scanner.list_sessions(project_cwd=project, active_window_sec=5)

    assert len(sessions) == 2
    assert sessions[0].session_id == "session-new"
    assert sessions[0].thread_name == "新的会话标题"
    assert sessions[0].first_message == "new message"
    assert sessions[0].last_user_message == "new latest"
    assert sessions[0].previous_user_message == "new previous"
    assert sessions[0].is_probably_active is True
    assert sessions[1].session_id == "session-old"
    assert sessions[1].thread_name == "旧会话标题"
    assert sessions[1].last_user_message == "old latest"
    assert sessions[1].previous_user_message == "old previous"
    assert sessions[1].is_probably_active is False


@pytest.mark.asyncio
async def test_codex_scanner_ignores_invalid_session_index_rows(tmp_path):
    """Broken session index rows should not prevent session discovery."""
    approved = tmp_path / "approved"
    project = approved / "proj-a"
    project.mkdir(parents=True)
    sessions_dir = tmp_path / ".codex" / "sessions"
    session_index = tmp_path / ".codex" / "session_index.jsonl"

    _write_codex_session(
        file_path=sessions_dir / "2026/02/14/rollout.jsonl",
        session_id="session-a",
        cwd=project,
        first_message="first message",
        last_message="latest message",
        timestamp=datetime.utcnow(),
    )
    session_index.parent.mkdir(parents=True, exist_ok=True)
    with open(session_index, "w", encoding="utf-8") as fh:
        fh.write("{invalid json}\n")
        fh.write(json.dumps({"id": "session-a", "thread_name": "  标题摘要  "}) + "\n")

    scanner = CodexSessionScanner(
        approved_directory=approved,
        cache_ttl_sec=0,
        sessions_dir=sessions_dir,
        session_index_path=session_index,
    )
    sessions = await scanner.list_sessions(project_cwd=project, active_window_sec=5)

    assert len(sessions) == 1
    assert sessions[0].thread_name == "标题摘要"
