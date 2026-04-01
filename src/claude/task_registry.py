"""Task registry for managing active Claude tasks per user.

Enables task cancellation by tracking asyncio.Task instances
and providing thread-safe state transitions.
"""

import asyncio
import copy
import enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Dict, Optional

import structlog

logger = structlog.get_logger()

if TYPE_CHECKING:
    from ..monitoring import RuntimeMetrics


class TaskState(enum.Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass
class ActiveTask:
    user_id: int
    task: Optional[asyncio.Task] = None
    state: TaskState = TaskState.RUNNING
    created_at: datetime = field(default_factory=datetime.now)
    prompt_summary: str = ""
    progress_message_id: Optional[int] = None
    chat_id: Optional[int] = None
    scope_key: Optional[str] = None


class TaskRegistry:
    """Manage active Claude tasks per user. Thread-safe via asyncio.Lock."""

    def __init__(self, metrics: Optional["RuntimeMetrics"] = None):
        self._tasks: Dict[str, ActiveTask] = {}
        self._lock = asyncio.Lock()
        self._metrics = metrics

    def _sync_metrics_unlocked(self) -> None:
        """Refresh active-task gauge from current state."""
        if self._metrics is None:
            return
        running_count = sum(
            1 for active in self._tasks.values() if active.state == TaskState.RUNNING
        )
        self._metrics.set_gauge("clitg_active_tasks", running_count)

    def _task_key(self, user_id: int, scope_key: Optional[str]) -> str:
        """Get internal task key (scope-first, user fallback)."""
        return scope_key or f"user:{user_id}"

    def _user_task_keys(self, user_id: int) -> list[str]:
        """Get all task keys that belong to a user."""
        return [k for k, t in self._tasks.items() if t.user_id == user_id]

    def _new_active_task(
        self,
        *,
        user_id: int,
        task: Optional[asyncio.Task] = None,
        prompt_summary: str = "",
        progress_message_id: Optional[int] = None,
        chat_id: Optional[int] = None,
        scope_key: Optional[str] = None,
    ) -> ActiveTask:
        """Build a normalized running task record."""
        return ActiveTask(
            user_id=user_id,
            task=task,
            prompt_summary=prompt_summary[:100],
            progress_message_id=progress_message_id,
            chat_id=chat_id,
            scope_key=scope_key,
        )

    async def try_start(
        self,
        user_id: int,
        *,
        prompt_summary: str = "",
        progress_message_id: Optional[int] = None,
        chat_id: Optional[int] = None,
        scope_key: Optional[str] = None,
    ) -> bool:
        """Atomically reserve a running slot for a scope."""
        async with self._lock:
            key = self._task_key(user_id, scope_key)
            active = self._tasks.get(key)
            if active and active.state == TaskState.RUNNING:
                return False

            self._tasks[key] = self._new_active_task(
                user_id=user_id,
                task=None,
                prompt_summary=prompt_summary,
                progress_message_id=progress_message_id,
                chat_id=chat_id,
                scope_key=scope_key,
            )
            self._sync_metrics_unlocked()
            return True

    async def attach_task(
        self,
        user_id: int,
        task: asyncio.Task,
        *,
        prompt_summary: str = "",
        progress_message_id: Optional[int] = None,
        chat_id: Optional[int] = None,
        scope_key: Optional[str] = None,
    ) -> bool:
        """Bind a concrete asyncio task to an existing running slot."""
        async with self._lock:
            key = self._task_key(user_id, scope_key)
            active = self._tasks.get(key)

            if active is None:
                self._tasks[key] = self._new_active_task(
                    user_id=user_id,
                    task=task,
                    prompt_summary=prompt_summary,
                    progress_message_id=progress_message_id,
                    chat_id=chat_id,
                    scope_key=scope_key,
                )
                self._sync_metrics_unlocked()
                return True

            if active.state == TaskState.CANCELLED:
                task.cancel()
                self._sync_metrics_unlocked()
                return False

            if active.state != TaskState.RUNNING:
                self._tasks[key] = self._new_active_task(
                    user_id=user_id,
                    task=task,
                    prompt_summary=prompt_summary,
                    progress_message_id=progress_message_id,
                    chat_id=chat_id,
                    scope_key=scope_key,
                )
                self._sync_metrics_unlocked()
                return True

            active.task = task
            active.prompt_summary = prompt_summary[:100]
            active.progress_message_id = progress_message_id
            active.chat_id = chat_id
            active.scope_key = scope_key
            self._sync_metrics_unlocked()
            return True

    async def register(
        self,
        user_id: int,
        task: asyncio.Task,
        prompt_summary: str = "",
        progress_message_id: Optional[int] = None,
        chat_id: Optional[int] = None,
        scope_key: Optional[str] = None,
    ) -> None:
        await self.attach_task(
            user_id,
            task,
            prompt_summary=prompt_summary,
            progress_message_id=progress_message_id,
            chat_id=chat_id,
            scope_key=scope_key,
        )

    async def cancel(self, user_id: int, scope_key: Optional[str] = None) -> bool:
        """Cancel the user's active task. Returns True if cancelled."""
        async with self._lock:
            if scope_key:
                targets = [self._task_key(user_id, scope_key)]
            else:
                targets = self._user_task_keys(user_id)

            cancelled = False
            for key in targets:
                active = self._tasks.get(key)
                if not active or active.state != TaskState.RUNNING:
                    continue
                active.state = TaskState.CANCELLED
                if active.task is not None:
                    active.task.cancel()
                cancelled = True

            if not cancelled:
                return False
            self._sync_metrics_unlocked()
            logger.info("Task cancelled", user_id=user_id, scope_key=scope_key)
            return True

    async def complete(self, user_id: int, scope_key: Optional[str] = None) -> None:
        async with self._lock:
            keys = (
                [self._task_key(user_id, scope_key)]
                if scope_key
                else self._user_task_keys(user_id)
            )
            for key in keys:
                active = self._tasks.get(key)
                if active and active.state == TaskState.RUNNING:
                    active.state = TaskState.COMPLETED
            self._sync_metrics_unlocked()

    async def fail(self, user_id: int, scope_key: Optional[str] = None) -> None:
        async with self._lock:
            keys = (
                [self._task_key(user_id, scope_key)]
                if scope_key
                else self._user_task_keys(user_id)
            )
            for key in keys:
                active = self._tasks.get(key)
                if active and active.state == TaskState.RUNNING:
                    active.state = TaskState.FAILED
            self._sync_metrics_unlocked()

    async def remove(self, user_id: int, scope_key: Optional[str] = None) -> None:
        async with self._lock:
            if scope_key:
                self._tasks.pop(self._task_key(user_id, scope_key), None)
                self._sync_metrics_unlocked()
                return
            for key in self._user_task_keys(user_id):
                self._tasks.pop(key, None)
            self._sync_metrics_unlocked()

    async def get(
        self, user_id: int, scope_key: Optional[str] = None
    ) -> Optional[ActiveTask]:
        async with self._lock:
            if scope_key:
                active = self._tasks.get(self._task_key(user_id, scope_key))
                return copy.copy(active) if active else None

            keys = self._user_task_keys(user_id)
            if not keys:
                return None
            return copy.copy(self._tasks[keys[0]])

    async def is_busy(self, user_id: int, scope_key: Optional[str] = None) -> bool:
        async with self._lock:
            if scope_key:
                active = self._tasks.get(self._task_key(user_id, scope_key))
                return active is not None and active.state == TaskState.RUNNING

            for key in self._user_task_keys(user_id):
                if self._tasks[key].state == TaskState.RUNNING:
                    return True
            return False

    async def list_running(self) -> list[ActiveTask]:
        """Return shallow copies of all running tasks."""
        async with self._lock:
            return [
                copy.copy(active)
                for active in self._tasks.values()
                if active.state == TaskState.RUNNING
            ]
