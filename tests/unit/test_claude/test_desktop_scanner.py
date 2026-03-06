"""Tests for desktop session scanner."""

import json
import os
import time

import pytest

from src.claude.desktop_scanner import DesktopSessionScanner


def _write_session_jsonl(
    path,
    cwd,
    session_id,
    first_message="hello",
    previous_message=None,
    last_message=None,
):
    """Write a minimal valid session JSONL file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        {"cwd": str(cwd), "sessionId": session_id},
        {
            "type": "user",
            "message": {"content": first_message},
            "timestamp": "2026-02-12T12:00:00.000Z",
        },
    ]
    if previous_message:
        lines.append(
            {
                "type": "assistant",
                "message": {"content": "ok"},
                "timestamp": "2026-02-12T12:00:01.000Z",
            }
        )
        lines.append(
            {
                "type": "user",
                "message": {"content": previous_message},
                "timestamp": "2026-02-12T12:00:02.000Z",
            }
        )
    if last_message:
        lines.append(
            {
                "type": "assistant",
                "message": {"content": "ok"},
                "timestamp": "2026-02-12T12:00:03.000Z",
            }
        )
        lines.append(
            {
                "type": "user",
                "message": {"content": last_message},
                "timestamp": "2026-02-12T12:00:04.000Z",
            }
        )
    with open(path, "w", encoding="utf-8") as f:
        for line in lines:
            f.write(json.dumps(line, ensure_ascii=True) + "\n")


@pytest.mark.asyncio
async def test_list_projects_sorts_by_latest_activity(tmp_path):
    """Projects should be ordered by latest session file mtime descending."""
    approved = tmp_path / "approved"
    approved.mkdir(parents=True)

    project_old = approved / "proj-old"
    project_new = approved / "proj-new"
    project_old.mkdir()
    project_new.mkdir()

    projects_dir = tmp_path / ".claude" / "projects"
    old_file = projects_dir / "a" / "old.jsonl"
    new_file = projects_dir / "b" / "new.jsonl"
    _write_session_jsonl(old_file, project_old, "session-old")
    _write_session_jsonl(new_file, project_new, "session-new")

    now = time.time()
    os.utime(old_file, (now - 3600, now - 3600))
    os.utime(new_file, (now - 10, now - 10))

    scanner = DesktopSessionScanner(
        approved_directory=approved,
        projects_dir=projects_dir,
        cache_ttl_sec=0,
    )
    projects = await scanner.list_projects()

    assert projects[0] == project_new.resolve()
    assert projects[1] == project_old.resolve()


@pytest.mark.asyncio
async def test_list_projects_filters_outside_approved_directory(tmp_path):
    """Scanner should return only projects under approved directory."""
    approved = tmp_path / "approved"
    approved.mkdir(parents=True)

    inside = approved / "inside-project"
    outside = tmp_path / "outside-project"
    inside.mkdir()
    outside.mkdir()

    projects_dir = tmp_path / ".claude" / "projects"
    _write_session_jsonl(projects_dir / "in" / "in.jsonl", inside, "session-in")
    _write_session_jsonl(projects_dir / "out" / "out.jsonl", outside, "session-out")

    scanner = DesktopSessionScanner(
        approved_directory=approved,
        projects_dir=projects_dir,
        cache_ttl_sec=0,
    )
    projects = await scanner.list_projects()

    assert projects == [inside.resolve()]


@pytest.mark.asyncio
async def test_list_sessions_extracts_last_user_message(tmp_path):
    """Session candidate should expose latest user message preview."""
    approved = tmp_path / "approved"
    approved.mkdir(parents=True)
    project = approved / "proj-a"
    project.mkdir()

    projects_dir = tmp_path / ".claude" / "projects"
    session_file = projects_dir / "a" / "session-a.jsonl"
    _write_session_jsonl(
        session_file,
        project,
        "session-a",
        first_message="first prompt",
        previous_message="previous prompt",
        last_message="latest prompt",
    )

    scanner = DesktopSessionScanner(
        approved_directory=approved,
        projects_dir=projects_dir,
        cache_ttl_sec=0,
    )
    sessions = await scanner.list_sessions(project_cwd=project, active_window_sec=5)

    assert len(sessions) == 1
    assert sessions[0].first_message == "first prompt"
    assert sessions[0].last_user_message == "latest prompt"
    assert sessions[0].previous_user_message == "previous prompt"
