"""Tests for /opsstatus operational status command."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.bot.handlers.command import ops_status_command
from src.monitoring import RuntimeMetrics


def _build_update(user_id: int, chat_id: int) -> SimpleNamespace:
    """Build minimal Telegram update stub."""
    message = SimpleNamespace(
        message_id=12,
        message_thread_id=None,
        reply_text=AsyncMock(),
    )
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        effective_chat=SimpleNamespace(id=chat_id),
        effective_message=message,
        message=message,
    )


@pytest.mark.asyncio
async def test_opsstatus_reports_healthy_snapshot(tmp_path, monkeypatch):
    """Should render healthy snapshot when tmux status and process count are valid."""
    project_root = tmp_path / "repo"
    scripts_dir = project_root / "scripts"
    logs_dir = project_root / "logs"
    scripts_dir.mkdir(parents=True)
    logs_dir.mkdir(parents=True)
    (scripts_dir / "tmux-bot.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    (logs_dir / "restart-events.log").write_text(
        "2026-02-27 event=restart_requested\n"
        "2026-02-27 event=restart_begin\n"
        "2026-02-27 event=restart_succeeded\n",
        encoding="utf-8",
    )

    update = _build_update(user_id=801, chat_id=901)
    audit_logger = SimpleNamespace(log_command=AsyncMock())
    runtime_metrics = RuntimeMetrics(enabled=False, host="127.0.0.1", port=9464)
    runtime_metrics.set_gauge("clitg_bot_running", 1.0)
    runtime_metrics.set_gauge("clitg_polling_up", 1.0)
    runtime_metrics.set_gauge("clitg_polling_restart_requested", 0.0)
    runtime_metrics.set_gauge("clitg_watchdog_tick_age_seconds", 2.5)
    runtime_metrics.set_gauge("clitg_last_health_probe_age_seconds", 5.0)
    runtime_metrics.set_gauge("clitg_pending_update_count", 3.0)
    runtime_metrics.set_gauge("clitg_storage_up", 1.0)
    runtime_metrics.set_gauge("clitg_active_tasks", 2.0)
    context = SimpleNamespace(
        bot_data={
            "settings": SimpleNamespace(approved_directory=tmp_path),
            "audit_logger": audit_logger,
            "runtime_metrics": runtime_metrics,
            "cli_integrations": {
                "claude": SimpleNamespace(
                    process_manager=SimpleNamespace(active_processes={"a": object()})
                )
            },
        },
        user_data={},
    )

    capture = AsyncMock(
        side_effect=[
            (
                0,
                "[tmux-bot] tmux session 'cli_tg_bot': running\n"
                "[tmux-bot] bot process count: 1",
                "",
            ),
            (
                0,
                "  PID  PPID COMMAND\n" " 111 1 /Users/me/.venv/bin/cli-tg-bot",
                "",
            ),
        ]
    )
    monkeypatch.setattr("src.bot.handlers.command._run_command_capture", capture)
    monkeypatch.setattr(
        "src.bot.handlers.command._get_project_root",
        lambda: project_root,
    )
    monkeypatch.setattr(
        "src.bot.handlers.command.uuid.uuid4",
        lambda: uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
    )

    await ops_status_command(update, context)

    reply_call = update.message.reply_text.await_args
    rendered = reply_call.args[0]
    assert "opsstatus request_id=aaaaaaaabbbb" in rendered
    assert "ops_status: healthy" in rendered
    assert "status: healthy" in rendered
    assert "bot_processes: 1" in rendered
    assert "tmux: ok (rc=0)" in rendered
    assert "event=restart_succeeded" in rendered
    assert "metrics_address: http://127.0.0.1:9464/metricsz" in rendered
    assert "metrics_raw: http://127.0.0.1:9464/metrics" in rendered
    assert "active_tasks: 2" in rendered
    assert "cli_active_processes: 1" in rendered
    assert "ops_details:" not in rendered

    assert capture.await_count == 2
    assert capture.await_args_list[0].args[:3] == (
        "bash",
        str(project_root / "scripts" / "tmux-bot.sh"),
        "status",
    )
    assert capture.await_args_list[1].args[:3] == ("ps", "-Ao", "pid,ppid,command")

    audit_logger.log_command.assert_awaited_once()
    assert audit_logger.log_command.await_args.kwargs["command"] == "opsstatus"
    assert audit_logger.log_command.await_args.kwargs["success"] is True


@pytest.mark.asyncio
async def test_opsstatus_handles_missing_tmux_script(tmp_path, monkeypatch):
    """Should report unhealthy status when tmux control script is missing."""
    project_root = tmp_path / "repo-no-script"
    project_root.mkdir(parents=True)

    update = _build_update(user_id=802, chat_id=902)
    audit_logger = SimpleNamespace(log_command=AsyncMock())
    context = SimpleNamespace(
        bot_data={
            "settings": SimpleNamespace(approved_directory=tmp_path),
            "audit_logger": audit_logger,
        },
        user_data={},
    )

    capture = AsyncMock(
        return_value=(
            0,
            "PID PPID COMMAND\n",
            "",
        )
    )
    monkeypatch.setattr("src.bot.handlers.command._run_command_capture", capture)
    monkeypatch.setattr(
        "src.bot.handlers.command._get_project_root",
        lambda: project_root,
    )

    await ops_status_command(update, context)

    rendered = update.message.reply_text.await_args.args[0]
    assert "ops_status: degraded" in rendered
    assert "status: unavailable" in rendered
    assert "tmux: error (rc=127)" in rendered
    assert "ops_details:" in rendered
    assert "script not found" in rendered

    capture.assert_awaited_once()
    assert capture.await_args.args[:3] == ("ps", "-Ao", "pid,ppid,command")

    audit_logger.log_command.assert_awaited_once()
    assert audit_logger.log_command.await_args.kwargs["command"] == "opsstatus"
    assert audit_logger.log_command.await_args.kwargs["success"] is False
