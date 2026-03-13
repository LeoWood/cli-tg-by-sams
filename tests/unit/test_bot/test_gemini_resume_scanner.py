import json
from pathlib import Path

import pytest

from src.bot.utils.gemini_resume_scanner import GeminiSessionScanner


def _write_gemini_session(
    session_file: Path,
    *,
    session_id: str,
    last_updated: str,
    user_messages: list[str],
) -> None:
    session_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "sessionId": session_id,
        "lastUpdated": last_updated,
        "messages": [
            {"type": "user", "content": [{"text": message}]}
            for message in user_messages
        ],
    }
    session_file.write_text(json.dumps(payload), encoding="utf-8")


@pytest.mark.asyncio
async def test_gemini_scanner_lists_projects_under_approved_directory(tmp_path):
    approved = tmp_path / "approved"
    approved.mkdir()
    project = approved / "proj-a"
    project.mkdir()

    gemini_root = tmp_path / ".gemini"
    projects_path = gemini_root / "projects.json"
    tmp_dir = gemini_root / "tmp"
    project_dir = tmp_dir / "proj-a"
    project_dir.mkdir(parents=True)
    (project_dir / ".project_root").write_text(str(project), encoding="utf-8")
    _write_gemini_session(
        project_dir / "chats" / "session-1.json",
        session_id="gemini-session-1",
        last_updated="2026-03-13T07:00:38.189Z",
        user_messages=["first prompt"],
    )
    projects_path.write_text('{"projects": {}}', encoding="utf-8")

    scanner = GeminiSessionScanner(
        approved_directory=approved,
        projects_path=projects_path,
        tmp_dir=tmp_dir,
    )

    projects = await scanner.list_projects()

    assert projects == [project.resolve()]


@pytest.mark.asyncio
async def test_gemini_scanner_lists_sessions_and_extracts_previews(tmp_path):
    approved = tmp_path / "approved"
    approved.mkdir()
    project = approved / "proj-b"
    project.mkdir()

    gemini_root = tmp_path / ".gemini"
    tmp_dir = gemini_root / "tmp"
    project_dir = tmp_dir / "proj-b"
    project_dir.mkdir(parents=True)
    (project_dir / ".project_root").write_text(str(project), encoding="utf-8")
    _write_gemini_session(
        project_dir / "chats" / "session-2.json",
        session_id="gemini-session-2",
        last_updated="2026-03-13T07:05:38.189Z",
        user_messages=["first prompt", "follow up prompt"],
    )

    scanner = GeminiSessionScanner(
        approved_directory=approved,
        tmp_dir=tmp_dir,
    )

    sessions = await scanner.list_sessions(project_cwd=project)

    assert len(sessions) == 1
    candidate = sessions[0]
    assert candidate.session_id == "gemini-session-2"
    assert candidate.cwd == project.resolve()
    assert candidate.first_message == "first prompt"
    assert candidate.last_user_message == "follow up prompt"
    assert candidate.previous_user_message == "first prompt"
