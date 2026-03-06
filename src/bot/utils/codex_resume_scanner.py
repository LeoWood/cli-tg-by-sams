"""Desktop Codex session scanner for /resume.

Scans ~/.codex/sessions/ for JSONL session files produced by Codex CLI,
extracts project paths and session metadata for Telegram resume workflow.
"""

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import structlog

logger = structlog.get_logger()

# Default Codex sessions directory
CODEX_SESSIONS_DIR = Path.home() / ".codex" / "sessions"
CODEX_SESSION_INDEX_PATH = Path.home() / ".codex" / "session_index.jsonl"


@dataclass
class CodexSessionCandidate:
    """A desktop Codex session available for resumption."""

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
    thread_names: Optional[Dict[str, str]] = None
    thread_names_ts: float = 0.0
    sessions: Dict[str, Tuple[List[CodexSessionCandidate], float]] = field(
        default_factory=dict
    )


class CodexSessionScanner:
    """Scan ~/.codex/sessions/ for Codex sessions."""

    def __init__(
        self,
        approved_directory: Path,
        cache_ttl_sec: int = 30,
        sessions_dir: Optional[Path] = None,
        session_index_path: Optional[Path] = None,
    ):
        self._approved = approved_directory.resolve()
        self._cache_ttl = cache_ttl_sec
        self._sessions_dir = sessions_dir or CODEX_SESSIONS_DIR
        self._session_index_path = (
            session_index_path
            if session_index_path is not None
            else self._sessions_dir.parent / CODEX_SESSION_INDEX_PATH.name
        )
        self._cache = _ScanCache()

    async def list_projects(self) -> List[Path]:
        """Return deduplicated project cwds sorted by latest mtime desc."""
        now = time.monotonic()
        if (
            self._cache.projects is not None
            and now - self._cache.projects_ts < self._cache_ttl
        ):
            return list(self._cache.projects)

        seen: Dict[str, Tuple[Path, float]] = {}

        if not self._sessions_dir.is_dir():
            logger.warning("Codex sessions dir not found", path=str(self._sessions_dir))
            self._cache.projects = []
            self._cache.projects_ts = now
            return []

        for jsonl in self._sessions_dir.rglob("*.jsonl"):
            meta = self._extract_meta_from_head(jsonl)
            if not meta:
                continue
            _, cwd = meta
            resolved = cwd.resolve()
            if not resolved.is_relative_to(self._approved):
                continue
            try:
                mtime = jsonl.stat().st_mtime
            except OSError:
                mtime = 0.0
            key = str(resolved)
            existing = seen.get(key)
            if existing is None or mtime > existing[1]:
                seen[key] = (resolved, mtime)

        projects = [
            item[0]
            for item in sorted(
                seen.values(),
                key=lambda item: (-item[1], str(item[0])),
            )
        ]
        self._cache.projects = projects
        self._cache.projects_ts = now
        logger.debug("Scanned codex desktop projects", count=len(projects))
        return projects

    async def list_sessions(
        self,
        project_cwd: Path,
        active_window_sec: int = 30,
    ) -> List[CodexSessionCandidate]:
        """Return sessions whose cwd matches project_cwd."""
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

        candidates: List[CodexSessionCandidate] = []
        now_ts = time.time()
        thread_names = self._load_thread_names()

        if not self._sessions_dir.is_dir():
            return []

        for jsonl in self._sessions_dir.rglob("*.jsonl"):
            parsed = self._parse_session_file(
                jsonl_path=jsonl,
                target_cwd=resolved_cwd,
                now_ts=now_ts,
                active_window_sec=active_window_sec,
                thread_names=thread_names,
            )
            if parsed is not None:
                candidates.append(parsed)

        candidates.sort(key=lambda c: c.file_mtime, reverse=True)
        self._cache.sessions[cache_key] = (candidates, now)
        logger.debug(
            "Scanned codex desktop sessions",
            project=str(resolved_cwd),
            count=len(candidates),
        )
        return candidates

    def clear_cache(self) -> None:
        """Invalidate all cached scan results."""
        self._cache = _ScanCache()

    def _load_thread_names(self) -> Dict[str, str]:
        """Load session_id -> thread_name mapping from Codex session index."""
        now = time.monotonic()
        cached = self._cache.thread_names
        if cached is not None and now - self._cache.thread_names_ts < self._cache_ttl:
            return dict(cached)

        mapping: Dict[str, str] = {}
        index_path = self._session_index_path
        try:
            with open(index_path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(record, dict):
                        continue
                    session_id = str(record.get("id") or "").strip()
                    thread_name = " ".join(
                        str(record.get("thread_name") or "").split()
                    ).strip()
                    if session_id and thread_name:
                        mapping[session_id] = thread_name
        except OSError:
            mapping = {}

        self._cache.thread_names = mapping
        self._cache.thread_names_ts = now
        return dict(mapping)

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
    def _read_tail_lines(path: Path, max_lines: int = 10) -> List[str]:
        """Read last lines without loading the whole file."""
        try:
            size = path.stat().st_size
            if size == 0:
                return []
            chunk_size = min(size, 65536)
            with open(path, "rb") as fh:
                fh.seek(max(0, size - chunk_size))
                data = fh.read().decode("utf-8", errors="replace")
            lines = data.splitlines()
            return lines[-max_lines:]
        except OSError:
            return []

    @staticmethod
    def _extract_meta_from_head(jsonl_path: Path) -> Optional[Tuple[str, Path]]:
        """Extract session_id + cwd from session_meta line."""
        try:
            with open(jsonl_path, "r", encoding="utf-8") as fh:
                for _ in range(30):
                    line = fh.readline()
                    if not line:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(record, dict):
                        continue
                    if record.get("type") != "session_meta":
                        continue
                    payload = record.get("payload")
                    if not isinstance(payload, dict):
                        continue
                    session_id = str(payload.get("id") or "").strip()
                    cwd = str(payload.get("cwd") or "").strip()
                    if session_id and cwd:
                        return session_id, Path(cwd)
        except OSError:
            return None
        return None

    @staticmethod
    def _extract_first_message(jsonl_path: Path) -> str:
        """Extract first user message preview."""
        try:
            with open(jsonl_path, "r", encoding="utf-8") as fh:
                for _ in range(200):
                    line = fh.readline()
                    if not line:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if (
                        not isinstance(record, dict)
                        or record.get("type") != "event_msg"
                    ):
                        continue
                    payload = record.get("payload")
                    if not isinstance(payload, dict):
                        continue
                    if payload.get("type") != "user_message":
                        continue
                    message = str(payload.get("message") or "").strip()
                    if message:
                        return message[:120]
        except OSError:
            return ""
        return ""

    def _parse_session_file(
        self,
        *,
        jsonl_path: Path,
        target_cwd: Path,
        now_ts: float,
        active_window_sec: int,
        thread_names: Dict[str, str],
    ) -> Optional[CodexSessionCandidate]:
        """Parse one Codex session jsonl and return candidate if cwd matches."""
        meta = self._extract_meta_from_head(jsonl_path)
        if not meta:
            return None
        session_id, cwd = meta
        if cwd.resolve() != target_cwd:
            return None

        first_message = self._extract_first_message(jsonl_path)

        last_event_at: Optional[datetime] = None
        recent_user_messages: list[str] = []
        tail_lines = self._read_tail_lines(jsonl_path, max_lines=200)
        for line in reversed(tail_lines):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            ts = record.get("timestamp")
            if ts and last_event_at is None:
                last_event_at = self._parse_iso_timestamp(str(ts))

            if len(recent_user_messages) < 2 and record.get("type") == "event_msg":
                payload = record.get("payload")
                if isinstance(payload, dict) and payload.get("type") == "user_message":
                    message = str(payload.get("message") or "").strip()
                    if message:
                        recent_user_messages.append(message[:120])

            if last_event_at is not None and len(recent_user_messages) >= 2:
                break

        last_user_message = recent_user_messages[0] if recent_user_messages else ""
        previous_user_message = (
            recent_user_messages[1] if len(recent_user_messages) > 1 else ""
        )

        try:
            mtime = jsonl_path.stat().st_mtime
        except OSError:
            return None
        file_mtime = datetime.utcfromtimestamp(mtime)
        is_active = (now_ts - mtime) <= active_window_sec

        return CodexSessionCandidate(
            session_id=session_id,
            cwd=cwd,
            source_file=jsonl_path,
            last_event_at=last_event_at,
            file_mtime=file_mtime,
            is_probably_active=is_active,
            thread_name=thread_names.get(session_id, ""),
            first_message=first_message,
            last_user_message=last_user_message,
            previous_user_message=previous_user_message,
        )
