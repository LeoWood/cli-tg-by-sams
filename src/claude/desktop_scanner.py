"""Desktop Claude Code session scanner.

Scans ~/.claude/projects/ for JSONL session files created by the desktop
Claude Code CLI, extracts project paths (from JSONL cwd field) and session
metadata for the /resume command.
"""

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import structlog

logger = structlog.get_logger()

# Default Claude projects directory
CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"


@dataclass
class DesktopSessionCandidate:
    """A desktop Claude Code session available for resumption."""

    session_id: str
    cwd: Path  # from JSONL content, the authoritative project path
    source_file: Path  # ~/.claude/projects/.../*.jsonl
    last_event_at: Optional[datetime]
    file_mtime: datetime
    is_probably_active: bool  # now - mtime <= active_window
    first_message: str  # first user message preview
    last_user_message: str  # latest user message preview from file tail
    previous_user_message: str  # second latest user message preview from file tail


@dataclass
class _ScanCache:
    """Internal cache entry for scan results."""

    projects: Optional[List[Path]] = None
    projects_ts: float = 0.0
    sessions: Dict[str, Tuple[List[DesktopSessionCandidate], float]] = field(
        default_factory=dict
    )


class DesktopSessionScanner:
    """Scan ~/.claude/projects/ for desktop Claude Code sessions.

    Provides list_projects() and list_sessions() with a short-lived cache
    (default 30s TTL) to avoid repeated filesystem scans.
    """

    def __init__(
        self,
        approved_directory: Path,
        cache_ttl_sec: int = 30,
        projects_dir: Optional[Path] = None,
    ):
        self._approved = approved_directory.resolve()
        self._cache_ttl = cache_ttl_sec
        self._projects_dir = projects_dir or CLAUDE_PROJECTS_DIR
        self._cache = _ScanCache()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def list_projects(self) -> List[Path]:
        """Return deduplicated project cwds found under ~/.claude/projects/.

        Each project is identified by the *cwd* field inside its JSONL files,
        not by decoding the directory name.  Only projects whose cwd falls
        under ``approved_directory`` are returned.

        Projects are sorted by latest session-file mtime descending so that
        recently active projects appear first in /resume.
        """
        now = time.monotonic()
        if (
            self._cache.projects is not None
            and now - self._cache.projects_ts < self._cache_ttl
        ):
            return list(self._cache.projects)

        # str(resolved) -> (Path, latest_mtime)
        seen: Dict[str, Tuple[Path, float]] = {}

        if not self._projects_dir.is_dir():
            logger.warning(
                "Claude projects dir not found",
                path=str(self._projects_dir),
            )
            self._cache.projects = []
            self._cache.projects_ts = now
            return []

        for subdir in self._projects_dir.iterdir():
            if not subdir.is_dir():
                continue
            for jsonl in subdir.glob("*.jsonl"):
                cwd = self._extract_cwd_from_head(jsonl)
                if cwd is None:
                    continue
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
        logger.debug("Scanned desktop projects", count=len(projects))
        return projects

    async def list_sessions(
        self,
        project_cwd: Path,
        active_window_sec: int = 30,
    ) -> List[DesktopSessionCandidate]:
        """Return sessions whose cwd matches *project_cwd*.

        Sessions are sorted by file mtime descending (most recent first).
        ``is_probably_active`` is True when ``now - file_mtime <= active_window_sec``.
        """
        resolved_cwd = project_cwd.resolve()

        # Security: reject paths outside approved_directory
        if not resolved_cwd.is_relative_to(self._approved):
            return []

        cache_key = str(resolved_cwd)
        now = time.monotonic()

        cached = self._cache.sessions.get(cache_key)
        if cached is not None:
            cached_candidates, ts = cached
            if now - ts < self._cache_ttl:
                return list(cached_candidates)

        candidates: List[DesktopSessionCandidate] = []
        now_ts = time.time()

        if not self._projects_dir.is_dir():
            return []

        for subdir in self._projects_dir.iterdir():
            if not subdir.is_dir():
                continue
            for jsonl in subdir.glob("*.jsonl"):
                parsed = self._parse_session_file(
                    jsonl, resolved_cwd, now_ts, active_window_sec
                )
                if parsed is not None:
                    candidates.append(parsed)

        # Most recent first
        candidates.sort(key=lambda c: c.file_mtime, reverse=True)
        self._cache.sessions[cache_key] = (candidates, now)
        logger.debug(
            "Scanned desktop sessions",
            project=str(resolved_cwd),
            count=len(candidates),
        )
        return candidates

    def clear_cache(self) -> None:
        """Invalidate all cached scan results."""
        self._cache = _ScanCache()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_cwd_from_head(jsonl_path: Path) -> Optional[Path]:
        """Read the first few lines to find the cwd field.

        Skips file-history-snapshot lines.  Returns None on any error.
        """
        try:
            with open(jsonl_path, "r", encoding="utf-8") as fh:
                for _ in range(5):
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
                    cwd = record.get("cwd")
                    if cwd:
                        return Path(cwd)
        except OSError:
            pass
        return None

    @staticmethod
    def _parse_iso_timestamp(ts_str: str) -> Optional[datetime]:
        """Parse ISO 8601 timestamp (with Z suffix) to naive UTC datetime."""
        if not ts_str:
            return None
        try:
            # Handle "2026-02-11T12:10:41.735Z" format
            cleaned = ts_str.replace("Z", "+00:00")
            dt = datetime.fromisoformat(cleaned)
            return dt.replace(tzinfo=None)  # store as naive UTC
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _read_tail_lines(path: Path, max_lines: int = 10) -> List[str]:
        """Read the last *max_lines* lines from a file efficiently.

        Reads a chunk from the end of the file to avoid loading the entire
        (potentially large) JSONL into memory.
        """
        try:
            size = path.stat().st_size
            if size == 0:
                return []
            # Read last 64KB at most — enough for ~10 JSONL lines
            chunk_size = min(size, 65536)
            with open(path, "rb") as fh:
                fh.seek(max(0, size - chunk_size))
                data = fh.read().decode("utf-8", errors="replace")
            lines = data.splitlines()
            return lines[-max_lines:]
        except OSError:
            return []

    def _parse_session_file(
        self,
        jsonl_path: Path,
        target_cwd: Path,
        now_ts: float,
        active_window_sec: int,
    ) -> Optional[DesktopSessionCandidate]:
        """Parse a single JSONL file and return a candidate if cwd matches.

        Reads head for cwd/session_id/first_message, tail for last timestamp.
        Returns None if cwd doesn't match or file is unparseable.
        """
        # --- Head scan: cwd, session_id, first user message ---
        session_id: Optional[str] = None
        cwd: Optional[Path] = None
        first_message: str = ""

        try:
            with open(jsonl_path, "r", encoding="utf-8") as fh:
                for _ in range(20):  # scan up to 20 lines for metadata
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

                    # Extract cwd and session_id from first record that has them
                    if cwd is None and record.get("cwd"):
                        cwd = Path(record["cwd"])
                    if session_id is None and record.get("sessionId"):
                        session_id = record["sessionId"]

                    # Extract first user message
                    if (
                        not first_message
                        and record.get("type") == "user"
                        and record.get("message")
                    ):
                        content = record["message"].get("content", "")
                        if isinstance(content, str):
                            first_message = content[:120]
        except OSError:
            return None

        # Must have cwd and session_id
        if cwd is None or session_id is None:
            return None

        # Check cwd matches target
        if cwd.resolve() != target_cwd:
            return None

        # --- Tail scan: last timestamp ---
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
                last_event_at = self._parse_iso_timestamp(ts)

            if len(recent_user_messages) < 2 and record.get("type") == "user":
                message = record.get("message")
                content = (
                    message.get("content", "") if isinstance(message, dict) else ""
                )
                if isinstance(content, str) and content.strip():
                    recent_user_messages.append(content.strip()[:120])

            if last_event_at is not None and len(recent_user_messages) >= 2:
                break

        last_user_message = recent_user_messages[0] if recent_user_messages else ""
        previous_user_message = (
            recent_user_messages[1] if len(recent_user_messages) > 1 else ""
        )

        # --- File mtime ---
        try:
            mtime = jsonl_path.stat().st_mtime
        except OSError:
            return None
        file_mtime = datetime.utcfromtimestamp(mtime)

        # Active detection
        is_active = (now_ts - mtime) <= active_window_sec

        return DesktopSessionCandidate(
            session_id=session_id,
            cwd=cwd,
            source_file=jsonl_path,
            last_event_at=last_event_at,
            file_mtime=file_mtime,
            is_probably_active=is_active,
            first_message=first_message,
            last_user_message=last_user_message,
            previous_user_message=previous_user_message,
        )
