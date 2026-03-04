"""Per-topic/session scoped state helpers for Telegram handlers."""

from pathlib import Path
from typing import Any

from telegram import Update

SCOPE_STATE_CONTAINER_KEY = "scope_state"

# Only these keys may be inherited from legacy user_data into a new scope.
# Session-identity keys (claude_session_id, force_new_session, session_started,
# last_message) are deliberately excluded so that each topic/thread starts with
# a fresh, independent Claude session.  Using a whitelist ensures that any
# future scoped key defaults to "not inherited", which is the safer default.
_LEGACY_SEED_INHERIT_KEYS = {
    "current_directory",
    "claude_model",
    "codex_reasoning_effort",
}


def _normalize_int(value: Any, default: int = 0) -> int:
    """Convert value to int safely."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _thread_id_from_message(message: Any) -> int:
    """Extract thread id from telegram message-like object."""
    if message is None:
        return 0
    raw = getattr(message, "message_thread_id", None)
    if raw is None:
        return 0
    return _normalize_int(raw, default=0)


def build_scope_key(user_id: int, chat_id: int, thread_id: int) -> str:
    """Build stable scope key."""
    return f"{user_id}:{chat_id}:{thread_id}"


def get_scope_key_from_update(update: Update) -> str:
    """Build scope key from telegram update."""
    user = getattr(update, "effective_user", None)
    chat = getattr(update, "effective_chat", None)
    user_id = _normalize_int(getattr(user, "id", 0), default=0)
    chat_id = _normalize_int(getattr(chat, "id", user_id), default=user_id)
    thread_id = _thread_id_from_message(getattr(update, "effective_message", None))
    return build_scope_key(user_id=user_id, chat_id=chat_id, thread_id=thread_id)


def get_scope_key_from_query(query: Any) -> str:
    """Build scope key from callback query."""
    user_id = _normalize_int(getattr(getattr(query, "from_user", None), "id", 0))
    msg = getattr(query, "message", None)
    chat_id = _normalize_int(getattr(getattr(msg, "chat", None), "id", user_id))
    thread_id = _thread_id_from_message(msg)
    return build_scope_key(user_id=user_id, chat_id=chat_id, thread_id=thread_id)


def _ensure_scope_map(user_data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Ensure scope map exists in user_data."""
    scope_map = user_data.get(SCOPE_STATE_CONTAINER_KEY)
    if not isinstance(scope_map, dict):
        scope_map = {}
        user_data[SCOPE_STATE_CONTAINER_KEY] = scope_map
    return scope_map


def _seed_state_from_legacy(
    user_data: dict[str, Any],
    state: dict[str, Any],
    default_directory: Path,
) -> None:
    """Seed newly created scoped state from legacy root keys.

    Only configuration-like keys (current_directory, claude_model) are
    inherited.  Session-identity keys (claude_session_id, etc.) are
    intentionally excluded so that each topic/thread starts with a fresh,
    independent Claude session.

    We also set ``force_new_session = True`` so that the very first command
    in this scope does NOT trigger the facade's auto-resume logic (which
    matches by user + directory and would pull in another topic's session).
    The flag is consumed (popped) by the message handler on first use.
    """
    for key in _LEGACY_SEED_INHERIT_KEYS:
        if key in user_data and key not in state:
            state[key] = user_data[key]

    if "current_directory" not in state:
        state["current_directory"] = default_directory

    # Prevent auto-resume from binding another topic's session on first use.
    state.setdefault("force_new_session", True)


def get_scope_state(
    *,
    user_data: dict[str, Any],
    scope_key: str,
    default_directory: Path,
) -> dict[str, Any]:
    """Get or create scoped session state."""
    scope_map = _ensure_scope_map(user_data)
    state = scope_map.get(scope_key)
    if not isinstance(state, dict):
        state = {}
        scope_map[scope_key] = state
        _seed_state_from_legacy(
            user_data=user_data, state=state, default_directory=default_directory
        )
    return state


def get_scope_state_from_update(
    *,
    user_data: dict[str, Any],
    update: Update,
    default_directory: Path,
) -> tuple[str, dict[str, Any]]:
    """Get scope key and scoped state for normal update handlers."""
    scope_key = get_scope_key_from_update(update)
    state = get_scope_state(
        user_data=user_data,
        scope_key=scope_key,
        default_directory=default_directory,
    )
    return scope_key, state


def get_scope_state_from_query(
    *,
    user_data: dict[str, Any],
    query: Any,
    default_directory: Path,
) -> tuple[str, dict[str, Any]]:
    """Get scope key and scoped state for callback handlers."""
    scope_key = get_scope_key_from_query(query)
    state = get_scope_state(
        user_data=user_data,
        scope_key=scope_key,
        default_directory=default_directory,
    )
    return scope_key, state
