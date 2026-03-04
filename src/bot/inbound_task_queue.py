"""Inbound pending-task queue for per-scope Telegram messages."""

from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional
from uuid import uuid4


@dataclass
class InboundQueueItem:
    """A queued inbound task waiting for execution."""

    queue_id: str
    user_id: int
    scope_key: str
    kind: str
    payload: Any
    preview: str = ""
    created_at: datetime = field(default_factory=datetime.utcnow)


class QueueFullError(RuntimeError):
    """Raised when scope queue reaches configured limit."""

    def __init__(self, max_per_scope: int) -> None:
        super().__init__(f"Queue is full (max {max_per_scope} items per scope).")
        self.max_per_scope = max_per_scope


class InboundTaskQueue:
    """Thread-safe in-memory FIFO queue segmented by scope key."""

    def __init__(self, *, max_per_scope: int = 20) -> None:
        self._lock = asyncio.Lock()
        self._queues: dict[str, list[InboundQueueItem]] = {}
        self._max_per_scope = max(1, int(max_per_scope))

    @property
    def max_per_scope(self) -> int:
        """Return queue capacity per scope."""
        return self._max_per_scope

    def _find_item_position(
        self,
        *,
        queue_id: str,
        scope_key: Optional[str],
        user_id: Optional[int],
    ) -> tuple[Optional[str], int]:
        """Locate item position and return ``(scope_key, index)``."""
        target_scope_keys: list[str]
        if isinstance(scope_key, str) and scope_key:
            target_scope_keys = [scope_key]
        else:
            target_scope_keys = list(self._queues.keys())

        for key in target_scope_keys:
            queue = self._queues.get(key)
            if not queue:
                continue
            for idx, item in enumerate(queue):
                if item.queue_id != queue_id:
                    continue
                if user_id is not None and item.user_id != user_id:
                    continue
                return key, idx
        return None, -1

    def _cleanup_scope_if_empty(self, scope_key: str) -> None:
        """Drop empty queue container for scope."""
        queue = self._queues.get(scope_key)
        if queue == []:
            self._queues.pop(scope_key, None)

    async def enqueue(
        self,
        *,
        user_id: int,
        scope_key: str,
        kind: str,
        payload: Any,
        preview: str = "",
    ) -> tuple[InboundQueueItem, int]:
        """Append a new task and return ``(item, ahead_count)``."""
        async with self._lock:
            queue = self._queues.setdefault(scope_key, [])
            if len(queue) >= self._max_per_scope:
                raise QueueFullError(self._max_per_scope)

            ahead_count = len(queue)
            queue_item = InboundQueueItem(
                queue_id=uuid4().hex[:10],
                user_id=user_id,
                scope_key=scope_key,
                kind=str(kind or "").strip().lower() or "text",
                payload=payload,
                preview=str(preview or "").strip(),
            )
            queue.append(queue_item)
            return copy.copy(queue_item), ahead_count

    async def list_scope(
        self, *, scope_key: str, user_id: Optional[int] = None
    ) -> list[InboundQueueItem]:
        """Return queued items in a scope (FIFO order)."""
        async with self._lock:
            queue = self._queues.get(scope_key, [])
            if user_id is None:
                return [copy.copy(item) for item in queue]
            return [copy.copy(item) for item in queue if item.user_id == user_id]

    async def count_scope(self, *, scope_key: str, user_id: Optional[int] = None) -> int:
        """Count queued items in a scope."""
        async with self._lock:
            queue = self._queues.get(scope_key, [])
            if user_id is None:
                return len(queue)
            return sum(1 for item in queue if item.user_id == user_id)

    async def dequeue(
        self,
        *,
        queue_id: str,
        scope_key: Optional[str] = None,
        user_id: Optional[int] = None,
    ) -> Optional[InboundQueueItem]:
        """Remove a queued task by id."""
        async with self._lock:
            found_scope_key, idx = self._find_item_position(
                queue_id=queue_id,
                scope_key=scope_key,
                user_id=user_id,
            )
            if found_scope_key is None or idx < 0:
                return None

            queue = self._queues.get(found_scope_key, [])
            if idx >= len(queue):
                return None
            removed = queue.pop(idx)
            self._cleanup_scope_if_empty(found_scope_key)
            return copy.copy(removed)

    async def promote(
        self,
        *,
        queue_id: str,
        scope_key: Optional[str] = None,
        user_id: Optional[int] = None,
    ) -> Optional[InboundQueueItem]:
        """Move a queued task to the head of its scope queue."""
        async with self._lock:
            found_scope_key, idx = self._find_item_position(
                queue_id=queue_id,
                scope_key=scope_key,
                user_id=user_id,
            )
            if found_scope_key is None or idx < 0:
                return None

            queue = self._queues.get(found_scope_key, [])
            if idx >= len(queue):
                return None
            if idx != 0:
                item = queue.pop(idx)
                queue.insert(0, item)
            return copy.copy(queue[0])

    async def peek_next(
        self, *, scope_key: str, user_id: Optional[int] = None
    ) -> Optional[InboundQueueItem]:
        """Read next queued task without removing it."""
        async with self._lock:
            queue = self._queues.get(scope_key, [])
            if not queue:
                return None

            if user_id is None:
                return copy.copy(queue[0])
            for item in queue:
                if item.user_id == user_id:
                    return copy.copy(item)
            return None

    async def pop_next(
        self, *, scope_key: str, user_id: Optional[int] = None
    ) -> Optional[InboundQueueItem]:
        """Pop next queued task for execution."""
        async with self._lock:
            queue = self._queues.get(scope_key, [])
            if not queue:
                return None

            if user_id is None:
                item = queue.pop(0)
                self._cleanup_scope_if_empty(scope_key)
                return copy.copy(item)

            for idx, item in enumerate(queue):
                if item.user_id != user_id:
                    continue
                removed = queue.pop(idx)
                self._cleanup_scope_if_empty(scope_key)
                return copy.copy(removed)
            return None
