"""Resume history preview helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .cli_engine import ENGINE_CODEX, ENGINE_GEMINI, normalize_cli_engine


@dataclass(frozen=True)
class ResumeHistoryMessage:
    """One chat message used in resume history preview."""

    role: str  # user | assistant
    content: str


def _sanitize_text(raw: str) -> str:
    """Normalize message text into one compact line."""
    return " ".join(str(raw or "").split()).strip()


def _extract_text(value: Any, *, depth: int = 0) -> str:
    """Best-effort extract readable text from nested JSON payloads."""
    if depth > 6:
        return ""

    if isinstance(value, str):
        return value

    if isinstance(value, list):
        parts = [_extract_text(item, depth=depth + 1) for item in value]
        return " ".join(part for part in parts if part).strip()

    if isinstance(value, dict):
        for key in ("text", "message", "content"):
            if key in value:
                extracted = _extract_text(value.get(key), depth=depth + 1)
                if extracted:
                    return extracted
        return ""

    return ""


def _parse_claude_record(record: dict[str, Any]) -> ResumeHistoryMessage | None:
    """Parse one Claude JSONL record into preview message."""
    record_type = str(record.get("type") or "").strip().lower()
    if record_type not in {"user", "assistant"}:
        return None

    role = "assistant" if record_type == "assistant" else "user"
    message_obj = record.get("message")
    content = ""
    if isinstance(message_obj, dict):
        content = _extract_text(message_obj.get("content"))
        if not content:
            content = _extract_text(message_obj)
    elif isinstance(message_obj, str):
        content = message_obj

    if not content:
        content = _extract_text(record.get("content"))

    normalized = _sanitize_text(content)
    if not normalized:
        return None
    return ResumeHistoryMessage(role=role, content=normalized)


def _parse_codex_record(record: dict[str, Any]) -> ResumeHistoryMessage | None:
    """Parse one Codex JSONL record into preview message."""
    record_type = str(record.get("type") or "").strip().lower()

    payload = record.get("payload")
    if record_type == "event_msg" and isinstance(payload, dict):
        payload_type = str(payload.get("type") or "").strip().lower()
        if payload_type == "user_message":
            content = _sanitize_text(_extract_text(payload.get("message")))
            if content:
                return ResumeHistoryMessage(role="user", content=content)
            return None
        if payload_type in {"assistant_message", "assistant_output"}:
            content = _sanitize_text(_extract_text(payload.get("message")))
            if content:
                return ResumeHistoryMessage(role="assistant", content=content)
            return None
        return None

    if record_type == "user_message":
        content = _sanitize_text(_extract_text(record.get("message")))
        if content:
            return ResumeHistoryMessage(role="user", content=content)
        return None

    if record_type in {"assistant_message", "assistant_output"}:
        content = _sanitize_text(_extract_text(record.get("message")))
        if content:
            return ResumeHistoryMessage(role="assistant", content=content)
        return None

    return None


def _parse_recent_history_from_gemini_json(
    *,
    session_file: Path,
    limit: int,
) -> list[ResumeHistoryMessage]:
    """Parse recent chat messages from one Gemini session JSON file."""
    try:
        raw = json.loads(session_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    messages = raw.get("messages")
    if not isinstance(messages, list):
        return []

    parsed: list[ResumeHistoryMessage] = []
    for message in messages:
        if not isinstance(message, dict):
            continue

        role = str(message.get("type") or "").strip().lower()
        if role == "gemini":
            normalized_role = "assistant"
        elif role == "user":
            normalized_role = "user"
        else:
            continue

        content = _sanitize_text(_extract_text(message.get("content")))
        if not content:
            continue
        parsed.append(ResumeHistoryMessage(role=normalized_role, content=content))

    if not parsed:
        return []

    safe_limit = max(1, min(limit, 20))
    return parsed[-safe_limit:]


def _read_tail_lines(
    path: Path,
    *,
    max_bytes: int = 1024 * 1024,
    max_lines: int = 4000,
) -> list[str]:
    """Read file tail lines without loading full file into memory."""
    try:
        size = path.stat().st_size
        if size <= 0:
            return []

        chunk_size = min(size, max_bytes)
        with open(path, "rb") as handle:
            handle.seek(max(0, size - chunk_size))
            data = handle.read().decode("utf-8", errors="replace")
        lines = data.splitlines()
        if len(lines) > max_lines:
            return lines[-max_lines:]
        return lines
    except OSError:
        return []


def _parse_recent_history_from_jsonl(
    *,
    session_file: Path,
    engine: str,
    limit: int,
) -> list[ResumeHistoryMessage]:
    """Parse recent chat messages from one local session jsonl file."""
    lines = _read_tail_lines(session_file)
    if not lines:
        return []

    preferred_codex = normalize_cli_engine(engine) == ENGINE_CODEX
    parsed: list[ResumeHistoryMessage] = []

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue

        if preferred_codex:
            message = _parse_codex_record(record) or _parse_claude_record(record)
        else:
            message = _parse_claude_record(record) or _parse_codex_record(record)

        if message:
            parsed.append(message)

    if not parsed:
        return []

    safe_limit = max(1, min(limit, 20))
    return parsed[-safe_limit:]


async def _load_from_storage(
    *,
    storage: Any,
    session_id: str,
    user_id: int,
    limit: int,
) -> list[ResumeHistoryMessage]:
    """Load recent history from SQLite storage if available."""
    sessions_repo = getattr(storage, "sessions", None)
    messages_repo = getattr(storage, "messages", None)
    if sessions_repo is None or messages_repo is None:
        return []

    try:
        session = await sessions_repo.get_session(session_id)
    except Exception:
        return []

    if session is None:
        return []

    session_user_id = getattr(session, "user_id", None)
    if not isinstance(session_user_id, int) or session_user_id != user_id:
        return []

    try:
        rows = await messages_repo.get_session_messages(session_id, limit=max(1, limit))
    except Exception:
        return []

    if not rows:
        return []

    parsed: list[ResumeHistoryMessage] = []
    for row in reversed(rows):
        prompt = _sanitize_text(str(getattr(row, "prompt", "") or ""))
        response = _sanitize_text(str(getattr(row, "response", "") or ""))
        if prompt:
            parsed.append(ResumeHistoryMessage(role="user", content=prompt))
        if response:
            parsed.append(ResumeHistoryMessage(role="assistant", content=response))

    if not parsed:
        return []

    safe_limit = max(1, min(limit, 20))
    return parsed[-safe_limit:]


async def _resolve_session_file(
    *,
    scanner: Any,
    project_cwd: Path,
    session_id: str,
) -> Path | None:
    """Resolve source jsonl file for a desktop session."""
    if scanner is None:
        return None

    list_sessions = getattr(scanner, "list_sessions", None)
    if not callable(list_sessions):
        return None

    try:
        candidates = await list_sessions(project_cwd=project_cwd)
    except Exception:
        return None

    for candidate in candidates or []:
        candidate_sid = str(getattr(candidate, "session_id", "") or "").strip()
        if candidate_sid != session_id:
            continue
        source = getattr(candidate, "source_file", None)
        if source is None:
            return None
        source_path = Path(source)
        if source_path.exists():
            return source_path
        return None

    return None


async def load_resume_history_preview(
    *,
    session_id: str,
    user_id: int,
    project_cwd: Path,
    engine: str,
    limit: int = 6,
    storage: Any = None,
    scanner: Any = None,
) -> list[ResumeHistoryMessage]:
    """Load recent history preview for resumed session."""
    sid = str(session_id or "").strip()
    if not sid:
        return []

    safe_limit = max(1, min(limit, 20))

    if storage is not None:
        from_storage = await _load_from_storage(
            storage=storage,
            session_id=sid,
            user_id=user_id,
            limit=safe_limit,
        )
        if from_storage:
            return from_storage

    session_file = await _resolve_session_file(
        scanner=scanner,
        project_cwd=project_cwd,
        session_id=sid,
    )
    if session_file is None:
        return []

    if normalize_cli_engine(engine) == ENGINE_GEMINI or session_file.suffix == ".json":
        return _parse_recent_history_from_gemini_json(
            session_file=session_file,
            limit=safe_limit,
        )

    return _parse_recent_history_from_jsonl(
        session_file=session_file,
        engine=engine,
        limit=safe_limit,
    )
