"""Tests for Claude integration facade fallback behavior."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.claude.exceptions import (
    ClaudeProcessError,
    ClaudeTimeoutError,
    ClaudeToolValidationError,
)
from src.claude.facade import ClaudeIntegration
from src.claude.integration import ClaudeResponse, StreamUpdate
from src.config.settings import Settings


def _build_config(tmp_path, use_sdk: bool, **overrides) -> Settings:
    """Create test config for facade tests."""
    payload = dict(
        telegram_bot_token="test:token",
        telegram_bot_username="testbot",
        approved_directory=tmp_path,
        use_sdk=use_sdk,
        claude_timeout_seconds=2,
    )
    payload.update(overrides)
    return Settings(**payload)


def _build_facade(config: Settings, sdk_manager, process_manager) -> ClaudeIntegration:
    """Build facade with mocked managers."""
    return ClaudeIntegration(
        config=config,
        process_manager=process_manager,
        sdk_manager=sdk_manager,
        session_manager=MagicMock(),
        tool_monitor=MagicMock(),
        permission_manager=MagicMock(),
    )


class TestClaudeIntegrationFacade:
    """Test fallback behavior in ClaudeIntegration."""

    async def test_images_require_sdk_mode(self, tmp_path):
        """Image requests should fail fast when SDK mode is disabled."""
        config = _build_config(tmp_path, use_sdk=False)
        process_manager = MagicMock()
        process_manager.execute_command = AsyncMock()

        facade = _build_facade(
            config=config, sdk_manager=None, process_manager=process_manager
        )

        with pytest.raises(ClaudeProcessError) as exc_info:
            await facade._execute_with_fallback(
                prompt="Analyze this image",
                working_directory=tmp_path,
                images=[
                    {
                        "base64_data": "dGVzdA==",
                        "media_type": "image/jpeg",
                    }
                ],
            )

        assert "USE_SDK=true" in str(exc_info.value)
        process_manager.execute_command.assert_not_awaited()

    async def test_images_can_use_codex_subprocess_when_supported(self, tmp_path):
        """Image requests should pass through when subprocess advertises image support."""
        config = _build_config(tmp_path, use_sdk=False)
        response = ClaudeResponse(
            content="ok",
            session_id="codex-session",
            cost=0.0,
            duration_ms=8,
            num_turns=1,
        )
        process_manager = MagicMock()
        process_manager.supports_image_inputs = MagicMock(return_value=True)
        process_manager.execute_command = AsyncMock(return_value=response)

        facade = _build_facade(
            config=config, sdk_manager=None, process_manager=process_manager
        )

        result = await facade._execute_with_fallback(
            prompt="Analyze this image",
            working_directory=tmp_path,
            images=[
                {
                    "file_path": "/tmp/upload.png",
                    "media_type": "image/png",
                }
            ],
        )

        assert result is response
        kwargs = process_manager.execute_command.await_args.kwargs
        assert kwargs["images"][0]["file_path"] == "/tmp/upload.png"

    async def test_images_do_not_fallback_to_subprocess_on_sdk_error(self, tmp_path):
        """Image requests should not silently degrade to text-only subprocess mode."""
        config = _build_config(tmp_path, use_sdk=True)

        sdk_manager = MagicMock()
        sdk_manager.execute_command = AsyncMock(
            side_effect=ClaudeTimeoutError("SDK timeout")
        )
        sdk_manager.execute_with_client = AsyncMock()

        process_manager = MagicMock()
        process_manager.execute_command = AsyncMock()

        facade = _build_facade(
            config=config, sdk_manager=sdk_manager, process_manager=process_manager
        )

        with pytest.raises(ClaudeProcessError) as exc_info:
            await facade._execute_with_fallback(
                prompt="Analyze this image",
                working_directory=tmp_path,
                images=[
                    {
                        "base64_data": "dGVzdA==",
                        "media_type": "image/jpeg",
                    }
                ],
            )

        assert "cannot fall back to CLI text mode" in str(exc_info.value)
        process_manager.execute_command.assert_not_awaited()

    async def test_text_request_can_fallback_to_subprocess(self, tmp_path):
        """Non-image requests keep existing SDK->subprocess fallback behavior."""
        config = _build_config(tmp_path, use_sdk=True)

        sdk_manager = MagicMock()
        sdk_manager.execute_command = AsyncMock(
            side_effect=ClaudeTimeoutError("SDK timeout")
        )
        sdk_manager.execute_with_client = AsyncMock()

        fallback_response = ClaudeResponse(
            content="fallback ok",
            session_id="fallback-session",
            cost=0.0,
            duration_ms=10,
            num_turns=1,
        )
        process_manager = MagicMock()
        process_manager.execute_command = AsyncMock(return_value=fallback_response)

        facade = _build_facade(
            config=config, sdk_manager=sdk_manager, process_manager=process_manager
        )

        result = await facade._execute_with_fallback(
            prompt="hello",
            working_directory=tmp_path,
            images=None,
        )

        assert result is fallback_response
        process_manager.execute_command.assert_awaited_once()

    async def test_text_request_with_permission_callback_uses_client_mode(
        self, tmp_path
    ):
        """Permission callback must use SDK client mode for tool approval."""
        config = _build_config(tmp_path, use_sdk=True)

        client_response = ClaudeResponse(
            content="client ok",
            session_id="client-session",
            cost=0.0,
            duration_ms=12,
            num_turns=1,
        )

        sdk_manager = MagicMock()
        sdk_manager.execute_command = AsyncMock()
        sdk_manager.execute_with_client = AsyncMock(return_value=client_response)

        process_manager = MagicMock()
        process_manager.execute_command = AsyncMock()

        facade = _build_facade(
            config=config, sdk_manager=sdk_manager, process_manager=process_manager
        )

        permission_callback = AsyncMock()
        result = await facade._execute_with_fallback(
            prompt="hello",
            working_directory=tmp_path,
            permission_callback=permission_callback,
            images=None,
        )

        assert result is client_response
        sdk_manager.execute_with_client.assert_awaited_once()
        sdk_manager.execute_command.assert_not_awaited()
        process_manager.execute_command.assert_not_awaited()

    async def test_permission_callback_retryable_sdk_error_denies_subprocess_fallback(
        self, tmp_path
    ):
        """Permission-gated failures should deny by default instead of bypassing approval."""
        config = _build_config(tmp_path, use_sdk=True)

        sdk_manager = MagicMock()
        sdk_manager.execute_command = AsyncMock()
        sdk_manager.execute_with_client = AsyncMock(
            side_effect=ClaudeTimeoutError("SDK timeout")
        )

        process_manager = MagicMock()
        process_manager.execute_command = AsyncMock()

        facade = _build_facade(
            config=config, sdk_manager=sdk_manager, process_manager=process_manager
        )

        with pytest.raises(ClaudeProcessError) as exc_info:
            await facade._execute_with_fallback(
                prompt="hello",
                working_directory=tmp_path,
                permission_callback=AsyncMock(),
                images=None,
            )

        assert "denied by default" in str(exc_info.value)
        sdk_manager.execute_with_client.assert_awaited_once()
        sdk_manager.execute_command.assert_not_awaited()
        process_manager.execute_command.assert_not_awaited()

    async def test_get_precise_context_usage_parses_and_uses_cache(self, tmp_path):
        """Exact context probe should parse /context output and cache by session."""
        config = _build_config(
            tmp_path,
            use_sdk=True,
            status_context_probe_ttl_seconds=60,
        )
        sdk_manager = MagicMock()
        sdk_manager.execute_command = AsyncMock()
        process_manager = MagicMock()
        process_manager.execute_command = AsyncMock(
            return_value=ClaudeResponse(
                content=(
                    "Context usage: 27.5% (55,000 / 200,000 tokens)\n"
                    "Remaining: 145,000 tokens"
                ),
                session_id="session-1",
                cost=0.0,
                duration_ms=1,
                num_turns=0,
            )
        )

        facade = _build_facade(config, sdk_manager, process_manager)
        first = await facade.get_precise_context_usage(
            session_id="session-1",
            working_directory=tmp_path,
            model="sonnet",
        )
        second = await facade.get_precise_context_usage(
            session_id="session-1",
            working_directory=tmp_path,
            model="sonnet",
        )

        assert first is not None
        assert first["used_tokens"] == 55_000
        assert first["total_tokens"] == 200_000
        assert first["remaining_tokens"] == 145_000
        assert first["used_percent"] == 27.5
        assert first["cached"] is False

        assert second is not None
        assert second["cached"] is True
        process_manager.execute_command.assert_awaited_once()
        sdk_manager.execute_command.assert_not_awaited()

    async def test_tool_validation_notice_appends_without_overriding_result(
        self, tmp_path
    ):
        """Validation failures should append notice when a main result exists."""
        config = _build_config(tmp_path, use_sdk=False)
        session = MagicMock(
            session_id="session-local",
            is_new_session=False,
            source="bot",
        )
        session_manager = MagicMock()
        session_manager.get_or_create_session = AsyncMock(return_value=session)
        session_manager.update_session = AsyncMock()
        session_manager.remove_session = AsyncMock()

        tool_monitor = MagicMock()
        tool_monitor.validate_tool_call = AsyncMock(
            return_value=(False, "Tool not allowed: mcp__plugin_Notion_notion__move")
        )

        facade = ClaudeIntegration(
            config=config,
            process_manager=MagicMock(),
            sdk_manager=None,
            session_manager=session_manager,
            tool_monitor=tool_monitor,
            permission_manager=MagicMock(),
        )

        async def _fake_execute(**kwargs):
            await kwargs["stream_callback"](
                StreamUpdate(
                    type="assistant",
                    tool_calls=[
                        {
                            "name": "mcp__plugin_Notion_notion__move",
                            "input": {},
                        }
                    ],
                )
            )
            return ClaudeResponse(
                content="步骤已完成，以下是最终结果。",
                session_id="session-local",
                cost=0.0,
                duration_ms=1,
                num_turns=1,
            )

        facade._execute_with_fallback = AsyncMock(side_effect=_fake_execute)

        result = await facade.run_command(
            prompt="run",
            working_directory=tmp_path,
            user_id=1001,
            session_id="session-local",
        )

        assert "步骤已完成，以下是最终结果。" in result.content
        assert "Tool Validation Notice" in result.content
        assert result.is_error is False
        assert result.error_type is None

    async def test_tool_validation_without_result_returns_error_primary(self, tmp_path):
        """Validation failures should become primary message when no result exists."""
        config = _build_config(tmp_path, use_sdk=False)
        session = MagicMock(
            session_id="session-local",
            is_new_session=False,
            source="bot",
        )
        session_manager = MagicMock()
        session_manager.get_or_create_session = AsyncMock(return_value=session)
        session_manager.update_session = AsyncMock()
        session_manager.remove_session = AsyncMock()

        tool_monitor = MagicMock()
        tool_monitor.validate_tool_call = AsyncMock(
            return_value=(False, "Tool not allowed: mcp__plugin_Notion_notion__move")
        )

        facade = ClaudeIntegration(
            config=config,
            process_manager=MagicMock(),
            sdk_manager=None,
            session_manager=session_manager,
            tool_monitor=tool_monitor,
            permission_manager=MagicMock(),
        )

        async def _fake_execute(**kwargs):
            await kwargs["stream_callback"](
                StreamUpdate(
                    type="assistant",
                    tool_calls=[
                        {
                            "name": "mcp__plugin_Notion_notion__move",
                            "input": {},
                        }
                    ],
                )
            )
            return ClaudeResponse(
                content="",
                session_id="session-local",
                cost=0.0,
                duration_ms=1,
                num_turns=1,
            )

        facade._execute_with_fallback = AsyncMock(side_effect=_fake_execute)

        result = await facade.run_command(
            prompt="run",
            working_directory=tmp_path,
            user_id=1002,
            session_id="session-local",
        )

        assert "Tool Validation Failed" in result.content
        assert result.is_error is True
        assert result.error_type == "tool_validation_failed"

    async def test_bash_tool_validation_failure_fails_fast(self, tmp_path):
        """Blocked bash commands should raise immediately to stop execution."""
        config = _build_config(tmp_path, use_sdk=False)
        session = MagicMock(
            session_id="session-local",
            is_new_session=False,
            source="bot",
        )
        session_manager = MagicMock()
        session_manager.get_or_create_session = AsyncMock(return_value=session)
        session_manager.update_session = AsyncMock()
        session_manager.remove_session = AsyncMock()

        tool_monitor = MagicMock()
        tool_monitor.validate_tool_call = AsyncMock(
            return_value=(
                False,
                "Operational command blocked in remote Telegram session: "
                "make run/run-debug/run-local. Use /restartbot for restart "
                "or /opsstatus for diagnostics.",
            )
        )

        facade = ClaudeIntegration(
            config=config,
            process_manager=MagicMock(),
            sdk_manager=None,
            session_manager=session_manager,
            tool_monitor=tool_monitor,
            permission_manager=MagicMock(),
        )

        async def _fake_execute(**kwargs):
            await kwargs["stream_callback"](
                StreamUpdate(
                    type="progress",
                    tool_calls=[
                        {
                            "name": "Bash",
                            "input": {"command": "make run"},
                        }
                    ],
                )
            )
            return ClaudeResponse(
                content="should-not-return",
                session_id="session-local",
                cost=0.0,
                duration_ms=1,
                num_turns=1,
            )

        facade._execute_with_fallback = AsyncMock(side_effect=_fake_execute)

        with pytest.raises(ClaudeToolValidationError) as exc_info:
            await facade.run_command(
                prompt="restart",
                working_directory=tmp_path,
                user_id=1003,
                session_id="session-local",
            )

        assert "Action Blocked by Security Policy" in str(exc_info.value)
        assert "execution engine attempted an action" in str(exc_info.value)
        session_manager.update_session.assert_not_awaited()

    async def test_get_precise_context_usage_probes_codex_status(self, tmp_path):
        """Codex subprocess should probe `/status` and parse context usage."""
        config = _build_config(tmp_path, use_sdk=False)
        sdk_manager = MagicMock()
        process_manager = MagicMock()
        process_manager._resolve_cli_path = MagicMock(
            return_value="/usr/local/bin/codex"
        )
        process_manager._detect_cli_kind = MagicMock(return_value="codex")
        process_manager.execute_command = AsyncMock(
            return_value=ClaudeResponse(
                content=(
                    "Status\n"
                    "Context usage: 42.0% (84,000 / 200,000 tokens)\n"
                    "Remaining: 116,000 tokens"
                ),
                session_id="thread-codex-1",
                cost=0.0,
                duration_ms=1,
                num_turns=0,
            )
        )

        facade = _build_facade(config, sdk_manager, process_manager)
        result = await facade.get_precise_context_usage(
            session_id="thread-codex-1",
            working_directory=tmp_path,
        )

        assert result is not None
        assert result["used_tokens"] == 84_000
        assert result["total_tokens"] == 200_000
        assert result["remaining_tokens"] == 116_000
        assert result["used_percent"] == 42.0
        process_manager.execute_command.assert_awaited_once()
        assert process_manager.execute_command.await_args.kwargs["prompt"] == "/status"

    async def test_get_precise_context_usage_parses_codex_live_limits(self, tmp_path):
        """Codex `/status` should surface live limit bars even without context numbers."""
        config = _build_config(tmp_path, use_sdk=False)
        sdk_manager = MagicMock()
        process_manager = MagicMock()
        process_manager._resolve_cli_path = MagicMock(
            return_value="/usr/local/bin/codex"
        )
        process_manager._detect_cli_kind = MagicMock(return_value="codex")
        process_manager.execute_command = AsyncMock(
            return_value=ClaudeResponse(
                content=(
                    "Model: gpt-5.4 (reasoning xhigh, summaries auto)\n"
                    "5h limit: [█████] 20% left (resets 13:45)\n"
                    "Weekly limit: [████] 19% left (resets 10:10 on 10 Mar)"
                ),
                session_id="thread-codex-2",
                cost=0.0,
                duration_ms=1,
                num_turns=0,
            )
        )

        facade = _build_facade(config, sdk_manager, process_manager)
        result = await facade.get_precise_context_usage(
            session_id="thread-codex-2",
            working_directory=tmp_path,
        )

        assert result is not None
        assert result["resolved_model"] == "gpt-5.4"
        assert result["reasoning_effort"] == "xhigh"
        assert result["rate_limits"]["primary"]["used_percent"] == 80.0
        assert result["rate_limits"]["primary"]["resets_text"] == "13:45"
        assert result["rate_limits"]["secondary"]["used_percent"] == 81.0
        assert result["rate_limits"]["secondary"]["resets_text"] == "10:10 on 10 Mar"

    async def test_get_precise_context_usage_returns_none_when_unparseable(
        self, tmp_path
    ):
        """Unparseable /context output should fail safely without cache hit."""
        config = _build_config(
            tmp_path,
            use_sdk=True,
            status_context_probe_ttl_seconds=60,
        )
        sdk_manager = MagicMock()
        sdk_manager.execute_command = AsyncMock(
            return_value=ClaudeResponse(
                content="No context details available",
                session_id="session-1",
                cost=0.0,
                duration_ms=1,
                num_turns=0,
            )
        )
        process_manager = MagicMock()
        process_manager.execute_command = AsyncMock(
            return_value=ClaudeResponse(
                content="No context details available",
                session_id="session-1",
                cost=0.0,
                duration_ms=1,
                num_turns=0,
            )
        )

        facade = _build_facade(config, sdk_manager, process_manager)
        first = await facade.get_precise_context_usage(
            session_id="session-1",
            working_directory=tmp_path,
        )
        second = await facade.get_precise_context_usage(
            session_id="session-1",
            working_directory=tmp_path,
        )

        assert first is None
        assert second is None
        assert sdk_manager.execute_command.await_count == 2
        assert process_manager.execute_command.await_count == 2

    async def test_get_precise_context_usage_no_cache_when_ttl_zero(self, tmp_path):
        """TTL=0 should force realtime probe on every /status call."""
        config = _build_config(
            tmp_path,
            use_sdk=True,
            status_context_probe_ttl_seconds=0,
        )
        sdk_manager = MagicMock()
        sdk_manager.execute_command = AsyncMock()
        process_manager = MagicMock()
        process_manager.execute_command = AsyncMock(
            return_value=ClaudeResponse(
                content="Context usage: 20% (40,000 / 200,000 tokens)",
                session_id="session-1",
                cost=0.0,
                duration_ms=1,
                num_turns=0,
            )
        )

        facade = _build_facade(config, sdk_manager, process_manager)
        first = await facade.get_precise_context_usage(
            session_id="session-1",
            working_directory=tmp_path,
        )
        second = await facade.get_precise_context_usage(
            session_id="session-1",
            working_directory=tmp_path,
        )

        assert first is not None
        assert second is not None
        assert first["cached"] is False
        assert second["cached"] is False
        assert process_manager.execute_command.await_count == 2
        sdk_manager.execute_command.assert_not_awaited()

    async def test_get_precise_context_usage_falls_back_to_sdk_probe(self, tmp_path):
        """SDK probe should run when subprocess probe fails/unparseable."""
        config = _build_config(
            tmp_path,
            use_sdk=True,
            status_context_probe_ttl_seconds=0,
        )
        process_manager = MagicMock()
        process_manager.execute_command = AsyncMock(
            return_value=ClaudeResponse(
                content="",
                session_id="session-1",
                cost=0.0,
                duration_ms=1,
                num_turns=0,
            )
        )
        sdk_manager = MagicMock()
        sdk_manager.execute_command = AsyncMock(
            return_value=ClaudeResponse(
                content="Context usage: 10% (20,000 / 200,000 tokens)",
                session_id="session-1",
                cost=0.0,
                duration_ms=1,
                num_turns=0,
            )
        )

        facade = _build_facade(config, sdk_manager, process_manager)
        payload = await facade.get_precise_context_usage(
            session_id="session-1",
            working_directory=tmp_path,
        )

        assert payload is not None
        assert payload["used_tokens"] == 20_000
        process_manager.execute_command.assert_awaited_once()
        sdk_manager.execute_command.assert_awaited_once()

    def test_parse_context_usage_text_supports_labeled_lines(self, tmp_path):
        """Parser should support non-slash labeled context formats."""
        config = _build_config(tmp_path, use_sdk=False)
        facade = _build_facade(config, sdk_manager=None, process_manager=MagicMock())

        payload = facade._parse_context_usage_text(
            "Context usage\nUsed: 32,536 tokens\nWindow: 200,000 tokens\nRemaining: 167,464 tokens"
        )

        assert payload is not None
        assert payload["used_tokens"] == 32_536
        assert payload["total_tokens"] == 200_000
        assert payload["remaining_tokens"] == 167_464

    def test_parse_context_usage_text_can_infer_total_from_used_and_percent(
        self, tmp_path
    ):
        """Parser should infer totals when only used+percent are provided."""
        config = _build_config(tmp_path, use_sdk=False)
        facade = _build_facade(config, sdk_manager=None, process_manager=MagicMock())

        payload = facade._parse_context_usage_text(
            "已使用 40,000 tokens (20%)，剩余 160,000 tokens"
        )

        assert payload is not None
        assert payload["used_tokens"] == 40_000
        assert payload["total_tokens"] == 200_000
        assert payload["remaining_tokens"] == 160_000
