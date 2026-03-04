"""Tests for per-scope state helpers."""

from pathlib import Path
from types import SimpleNamespace

from src.bot.utils.scope_state import (
    get_scope_key_from_update,
    get_scope_state,
    get_scope_state_from_query,
)


def test_get_scope_key_from_update_includes_chat_and_topic() -> None:
    """Scope key should include user, chat, and topic(thread) id."""
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=7),
        effective_chat=SimpleNamespace(id=-100_123),
        effective_message=SimpleNamespace(message_thread_id=55),
    )

    scope_key = get_scope_key_from_update(update)

    assert scope_key == "7:-100123:55"


def test_get_scope_state_seeds_config_only_from_legacy_user_data() -> None:
    """New scoped state should inherit only config-like keys from legacy.

    Session-identity keys (claude_session_id, force_new_session, etc.) must
    NOT be inherited so that each topic gets an independent Claude session.
    """
    user_data = {
        "current_directory": Path("/tmp/project"),
        "claude_session_id": "sess-123",
        "claude_model": "sonnet",
        "codex_reasoning_effort": "high",
        "force_new_session": True,
        "session_started": True,
        "last_message": "hello",
    }

    state = get_scope_state(
        user_data=user_data,
        scope_key="1:2:3",
        default_directory=Path("/approved"),
    )

    # Config-like keys should be inherited
    assert state["current_directory"] == Path("/tmp/project")
    assert state["claude_model"] == "sonnet"
    assert state["codex_reasoning_effort"] == "high"

    # Session-identity keys must NOT be inherited
    assert "claude_session_id" not in state
    assert "session_started" not in state
    assert "last_message" not in state

    # force_new_session must be True to prevent auto-resume from another topic
    assert state["force_new_session"] is True

    # Re-fetch should return the same scoped state object.
    state_again = get_scope_state(
        user_data=user_data,
        scope_key="1:2:3",
        default_directory=Path("/approved"),
    )
    assert state_again is state


def test_different_topics_get_independent_sessions() -> None:
    """Two different topic scope keys must not share claude_session_id."""
    user_data: dict = {}
    default_dir = Path("/approved")

    state_a = get_scope_state(
        user_data=user_data, scope_key="1:100:10", default_directory=default_dir
    )
    state_a["claude_session_id"] = "session-topic-A"

    state_b = get_scope_state(
        user_data=user_data, scope_key="1:100:20", default_directory=default_dir
    )

    # Topic B must not see topic A's session
    assert state_b.get("claude_session_id") is None
    # Topic A must be unchanged
    assert state_a["claude_session_id"] == "session-topic-A"


def test_new_scope_sets_force_new_session_to_block_auto_resume() -> None:
    """New scope must have force_new_session=True to prevent facade auto-resume.

    Without this, the facade's ``_find_resumable_session`` would match another
    topic's session (by user+directory) and bind it to this scope.
    """
    user_data: dict = {}
    default_dir = Path("/approved")

    state = get_scope_state(
        user_data=user_data, scope_key="1:200:30", default_directory=default_dir
    )

    assert state.get("force_new_session") is True

    # Simulate the message handler consuming the flag (pop)
    consumed = state.pop("force_new_session", False)
    assert consumed is True

    # After consumption, re-fetching the same scope should NOT re-seed
    state_again = get_scope_state(
        user_data=user_data, scope_key="1:200:30", default_directory=default_dir
    )
    assert state_again.get("force_new_session") is None


def test_get_scope_state_from_query_uses_query_context() -> None:
    """Callback query scope should default chat_id to user_id when missing."""
    user_data: dict = {}
    query = SimpleNamespace(
        from_user=SimpleNamespace(id=9),
        message=SimpleNamespace(message_thread_id=None),
    )

    scope_key, state = get_scope_state_from_query(
        user_data=user_data,
        query=query,
        default_directory=Path("/approved"),
    )

    assert scope_key == "9:9:0"
    assert state["current_directory"] == Path("/approved")
