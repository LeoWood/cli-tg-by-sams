"""Session lifecycle application service."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass
class SessionResetResult:
    """Result for new-session reset action."""

    old_session_id: Optional[str]
    changed: bool


@dataclass
class SessionEndResult:
    """Result for end-session action."""

    had_active_session: bool
    ended_session_id: Optional[str]


@dataclass
class ContinueSessionResult:
    """Result for continue-session execution."""

    status: str  # continued | not_found | integration_unavailable
    response: Optional[Any] = None
    previous_session_id: Optional[str] = None
    used_existing_session: bool = False


class SessionLifecycleService:
    """Manage session lifecycle transitions and continue-session execution."""

    def __init__(self, permission_manager: Optional[Any] = None):
        self.permission_manager = permission_manager

    def start_new_session(self, scope_state: Dict[str, Any]) -> SessionResetResult:
        """Reset current session state for /new behavior."""
        old_session_id = scope_state.get("claude_session_id")
        scope_state["claude_session_id"] = None
        scope_state["session_started"] = True
        scope_state["force_new_session"] = True
        if old_session_id and self.permission_manager:
            self.permission_manager.clear_session(old_session_id)
        return SessionResetResult(
            old_session_id=old_session_id,
            changed=bool(old_session_id),
        )

    def end_session(self, scope_state: Dict[str, Any]) -> SessionEndResult:
        """Terminate current session state for /end behavior."""
        current_session_id = scope_state.get("claude_session_id")
        if not current_session_id:
            return SessionEndResult(
                had_active_session=False,
                ended_session_id=None,
            )

        scope_state["claude_session_id"] = None
        scope_state["session_started"] = False
        scope_state["last_message"] = None
        if self.permission_manager:
            self.permission_manager.clear_session(current_session_id)
        return SessionEndResult(
            had_active_session=True,
            ended_session_id=current_session_id,
        )

    @staticmethod
    def get_active_session_id(scope_state: Dict[str, Any]) -> Optional[str]:
        """Get current active session id from scoped state."""
        return scope_state.get("claude_session_id")

    async def continue_session(
        self,
        *,
        user_id: int,
        scope_state: Dict[str, Any],
        current_dir: Path,
        claude_integration: Any,
        prompt: Optional[str],
        default_prompt: str,
        permission_handler: Optional[Any] = None,
        use_empty_prompt_when_existing: bool = False,
        allow_none_prompt_when_discover: bool = False,
        model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
    ) -> ContinueSessionResult:
        """Continue existing session or discover latest session in directory."""
        if not claude_integration:
            return ContinueSessionResult(status="integration_unavailable")

        existing_session_id = scope_state.get("claude_session_id")

        if existing_session_id:
            run_prompt = prompt or default_prompt
            if use_empty_prompt_when_existing and not prompt:
                run_prompt = ""

            response = await claude_integration.run_command(
                prompt=run_prompt,
                working_directory=current_dir,
                user_id=user_id,
                session_id=existing_session_id,
                permission_handler=permission_handler,
                model=model,
                reasoning_effort=reasoning_effort,
            )
            if response:
                scope_state["claude_session_id"] = response.session_id
                return ContinueSessionResult(
                    status="continued",
                    response=response,
                    previous_session_id=existing_session_id,
                    used_existing_session=True,
                )
            return ContinueSessionResult(
                status="not_found",
                previous_session_id=existing_session_id,
                used_existing_session=True,
            )

        discover_prompt: Optional[str]
        if prompt:
            discover_prompt = prompt
        elif allow_none_prompt_when_discover:
            discover_prompt = None
        else:
            discover_prompt = default_prompt

        response = await claude_integration.continue_session(
            user_id=user_id,
            working_directory=current_dir,
            prompt=discover_prompt,
            permission_handler=permission_handler,
            model=model,
            reasoning_effort=reasoning_effort,
        )
        if response:
            scope_state["claude_session_id"] = response.session_id
            return ContinueSessionResult(
                status="continued",
                response=response,
                previous_session_id=None,
                used_existing_session=False,
            )

        return ContinueSessionResult(
            status="not_found",
            previous_session_id=None,
            used_existing_session=False,
        )
