"""Session event application service."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional

import structlog

from ..storage.facade import Storage
from ..storage.models import SessionEventModel
from ..utils.beijing_time import format_datetime_beijing

logger = structlog.get_logger()


class EventService:
    """Provide reusable session-event query and summary capabilities."""

    def __init__(self, storage: Storage):
        self.storage = storage

    async def list_recent_session_events(
        self,
        session_id: str,
        *,
        limit: int = 20,
        event_types: Optional[List[str]] = None,
    ) -> List[SessionEventModel]:
        """Fetch recent events for one session."""
        if not session_id:
            return []

        safe_limit = max(1, min(limit, 500))
        return await self.storage.session_events.get_session_events(
            session_id=session_id,
            event_types=event_types,
            limit=safe_limit,
        )

    async def get_recent_event_summary(
        self,
        session_id: str,
        *,
        limit: int = 20,
        highlight_limit: int = 3,
    ) -> Dict[str, Any]:
        """Build compact summary payload from recent session events."""
        events = await self.list_recent_session_events(session_id, limit=limit)
        if not events:
            return {
                "session_id": session_id,
                "count": 0,
                "latest_at": None,
                "by_type": {},
                "highlights": [],
            }

        by_type = Counter(event.event_type for event in events)
        latest_at = max(event.created_at for event in events)

        highlights: list[str] = []
        if highlight_limit > 0:
            highlights = [
                self._build_event_highlight(event)
                for event in events[: max(1, highlight_limit)]
            ]

        return {
            "session_id": session_id,
            "count": len(events),
            "latest_at": self._to_compact_datetime(latest_at),
            "by_type": dict(
                sorted(by_type.items(), key=lambda item: (-item[1], item[0]))
            ),
            "highlights": highlights,
        }

    @staticmethod
    def _to_compact_datetime(value: datetime) -> str:
        """Render datetime in compact sortable format."""
        return format_datetime_beijing(value)

    @staticmethod
    def _clip_text(value: str, max_chars: int = 48) -> str:
        """Clip and flatten text for one-line summary usage."""
        flat = str(value or "").replace("\n", " ").strip()
        if len(flat) <= max_chars:
            return flat
        return flat[:max_chars] + "..."

    def _build_event_highlight(self, event: SessionEventModel) -> str:
        """Convert one event to a concise readable highlight."""
        data = event.event_data or {}
        event_type = event.event_type

        if event_type == "command_exec":
            prompt = self._clip_text(str(data.get("prompt") or ""))
            return f"command_exec: {prompt or '(empty prompt)'}"

        if event_type == "assistant_text":
            content = self._clip_text(str(data.get("content") or ""))
            return f"assistant_text: {content or '(empty reply)'}"

        if event_type == "tool_call":
            tool_name = str(data.get("tool_name") or "unknown")
            return f"tool_call: {tool_name}"

        if event_type == "tool_result":
            tool_name = str(data.get("tool_name") or "unknown")
            success = bool(data.get("success", False))
            return f"tool_result: {tool_name} ({'ok' if success else 'error'})"

        if event_type == "error":
            error_type = str(data.get("error_type") or "unknown")
            return f"error: {error_type}"

        return event_type
