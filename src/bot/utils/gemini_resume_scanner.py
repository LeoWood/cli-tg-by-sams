"""Desktop Gemini session scanner for /resume.

Scans ~/.gemini/tmp/*/chats/session-*.json and extracts project/session metadata
for Telegram resume workflow.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import structlog

logger = structlog.get_logger()

GEMINI_HOME_DIR = Path.home() / ".gemini"
GEMINI_PROJECTS_PATH = GEMINI_HOME_DIR / "projects.json"
GEMINI_TMP_DIR = GEMINI_HOME_DIR / "tmp"


@dataclass
class GeminiSessionCandidate:
    """A desktop Gemini session available for resumption."""

    session_id: str
    cwd: Path
    source_file: Path
    last_event_at: Optional[datetime]
    file_mtime: datetime
    is_probably_active: bool
    thread_name: str
    first_message: str
    last_user_message: str
    previous_user_message: str


@dataclass
class _ScanCache:
    """Internal cache entry for scan results."""

    projects: Optional[List[Path]] = None
    projects_ts: float = 0.0
    sessions: Dict[str, Tuple[List[GeminiSessionCandidate], float]] = field(
        default_factory=dict
    )


class GeminiSessionScanner:
    """Scan ~/.gemini session files for resumable Gemini sessions."""

    def __init__(
        self,
        approved_directory: Path,
        cache_ttl_sec: int = 30,
        projects_path: Optional[Path] = None,
        tmp_dir: Optional[Path] = None,
    ) -> None:
        self._approved = approved_directory.resolve()
        self._cache_ttl = cache_ttl_sec
        self._projects_path = projects_path or GEMINI_PROJECTS_PATH
        self._tmp_dir = tmp_dir or GEMINI_TMP_DIR
        self._cache = _ScanCache()

    async def list_projects(self) -> List[Path]:
        """Return deduplicated project roots sorted by latest session mtime desc."""
        now = time.monotonic()
        if (
            self._cache.projects is not None
            and now - self._cache.projects_ts < self._cache_ttl
        ):
            return list(self._cache.projects)

        seen: Dict[str, Tuple[Path, float]] = {}
        for project_dir, project_cwd in self._iter_project_dirs():
            if not project_cwd.is_relative_to(self._approved):
                continue

            latest_mtime = 0.0
            for session_file in (project_dir / "chats").glob("session-*.json"):
                try:
                    latest_mtime = max(latest_mtime, session_file.stat().st_mtime)
                except OSError:
                    continue
            if latest_mtime <= 0:
                continue

            key = str(project_cwd)
            existing = seen.get(key)
            if existing is None or latest_mtime > existing[1]:
                seen[key] = (project_cwd, latest_mtime)

        projects = [
            item[0]
            for item in sorted(
                seen.values(),
                key=lambda item: (-item[1], str(item[0])),
            )
        ]
        self._cache.projects = projects
        self._cache.projects_ts = now
        logger.debug("Scanned gemini desktop projects", count=len(projects))
        return projects

    async def list_sessions(
        self,
        project_cwd: Path,
        active_window_sec: int = 30,
    ) -> List[GeminiSessionCandidate]:
        """Return Gemini sessions belonging to one project."""
        resolved_cwd = project_cwd.resolve()
        if not resolved_cwd.is_relative_to(self._approved):
            return []

        cache_key = str(resolved_cwd)
        now = time.monotonic()
        cached = self._cache.sessions.get(cache_key)
        if cached is not None:
            cached_candidates, ts = cached
            if now - ts < self._cache_ttl:
                return list(cached_candidates)

        candidates: List[GeminiSessionCandidate] = []
        now_ts = time.time()
        for project_dir, cwd in self._iter_project_dirs():
            if cwd != resolved_cwd:
                continue
            for session_file in (project_dir / "chats").glob("session-*.json"):
                parsed = self._parse_session_file(
                    session_file=session_file,
                    project_cwd=resolved_cwd,
                    now_ts=now_ts,
                    active_window_sec=active_window_sec,
                )
                if parsed is not None:
                    candidates.append(parsed)

        candidates.sort(key=lambda candidate: candidate.file_mtime, reverse=True)
        self._cache.sessions[cache_key] = (candidates, now)
        logger.debug(
            "Scanned gemini desktop sessions",
            project=str(resolved_cwd),
            count=len(candidates),
        )
        return candidates

    def clear_cache(self) -> None:
        """Invalidate cached scan results."""
        self._cache = _ScanCache()

    def _iter_project_dirs(self) -> List[Tuple[Path, Path]]:
        """Enumerate Gemini tmp project dirs and resolve their project roots."""
        if not self._tmp_dir.is_dir():
            return []

        items: List[Tuple[Path, Path]] = []
        for child in self._tmp_dir.iterdir():
            if not child.is_dir():
                continue
            cwd = self._resolve_project_cwd(child)
            if cwd is None:
                continue
            items.append((child, cwd))
        return items

    def _resolve_project_cwd(self, project_dir: Path) -> Optional[Path]:
        """Resolve project root from .project_root or projects.json mapping."""
        project_root_file = project_dir / ".project_root"
        try:
            if project_root_file.is_file():
                raw = project_root_file.read_text(encoding="utf-8").strip()
                if raw:
                    return Path(raw).resolve()
        except (OSError, ValueError):
            pass

        mapping = self._load_project_aliases()
        raw_path = mapping.get(project_dir.name)
        if not raw_path:
            return None
        try:
            return Path(raw_path).resolve()
        except (OSError, ValueError):
            return None

    def _load_project_aliases(self) -> Dict[str, str]:
        """Load alias -> path mapping from ~/.gemini/projects.json."""
        try:
            raw = json.loads(self._projects_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

        projects = raw.get("projects")
        if not isinstance(projects, dict):
            return {}

        aliases: Dict[str, str] = {}
        for path_str, alias in projects.items():
            normalized_alias = str(alias or "").strip()
            normalized_path = str(path_str or "").strip()
            if normalized_alias and normalized_path:
                aliases[normalized_alias] = normalized_path
        return aliases

    @staticmethod
    def _parse_iso_timestamp(ts_str: str) -> Optional[datetime]:
        """Parse ISO8601 timestamp to naive UTC datetime."""
        if not ts_str:
            return None
        try:
            cleaned = ts_str.replace("Z", "+00:00")
            dt = datetime.fromisoformat(cleaned)
            return dt.replace(tzinfo=None)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _extract_message_text(message: dict) -> str:
        """Extract text from one Gemini message entry."""
        content = message.get("content")
        if isinstance(content, str):
            return " ".join(content.split()).strip()
        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = str(item.get("text") or "").strip()
                    if text:
                        parts.append(text)
                elif isinstance(item, str):
                    text = item.strip()
                    if text:
                        parts.append(text)
            return " ".join(parts).strip()
        return ""

    def _parse_session_file(
        self,
        *,
        session_file: Path,
        project_cwd: Path,
        now_ts: float,
        active_window_sec: int,
    ) -> Optional[GeminiSessionCandidate]:
        """Parse one Gemini session JSON file."""
        try:
            raw = json.loads(session_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

        if not isinstance(raw, dict):
            return None

        session_id = str(raw.get("sessionId") or "").strip()
        if not session_id:
            return None

        try:
            mtime = datetime.utcfromtimestamp(session_file.stat().st_mtime)
            mtime_ts = session_file.stat().st_mtime
        except OSError:
            return None

        messages = raw.get("messages")
        if not isinstance(messages, list):
            messages = []

        user_messages: List[str] = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            msg_type = str(message.get("type") or "").strip().lower()
            if msg_type != "user":
                continue
            text = self._extract_message_text(message)
            if text:
                user_messages.append(text)

        last_updated = self._parse_iso_timestamp(str(raw.get("lastUpdated") or ""))
        return GeminiSessionCandidate(
            session_id=session_id,
            cwd=project_cwd,
            source_file=session_file,
            last_event_at=last_updated,
            file_mtime=mtime,
            is_probably_active=(now_ts - mtime_ts) <= active_window_sec,
            thread_name="",
            first_message=(user_messages[0] if user_messages else ""),
            last_user_message=(user_messages[-1] if user_messages else ""),
            previous_user_message=(
                user_messages[-2] if len(user_messages) >= 2 else ""
            ),
        )
