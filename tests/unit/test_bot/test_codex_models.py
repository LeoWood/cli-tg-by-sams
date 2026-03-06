"""Tests for Codex model discovery helpers."""

import json

from src.bot.utils.codex_models import (
    _CODEX_MODEL_CACHE,
    build_codex_model_keyboard,
    discover_codex_model_candidates,
)


def test_discover_codex_model_candidates_merges_runtime_config_and_recent_sessions(
    tmp_path,
):
    """Discovery should prefer runtime hints, then local config and recent sessions."""
    _CODEX_MODEL_CACHE.clear()
    codex_root = tmp_path / ".codex"
    sessions_root = codex_root / "sessions" / "2026" / "03" / "06"
    sessions_root.mkdir(parents=True)
    config_path = codex_root / "config.toml"
    config_path.write_text(
        'model = "gpt-5.4"\nmodel_reasoning_effort = "xhigh"\n',
        encoding="utf-8",
    )
    session_file = sessions_root / "session-1.jsonl"
    session_file.write_text(
        "\n".join(
            [
                json.dumps(
                    {"type": "session_meta", "payload": {"model": "gpt-5.2-codex"}}
                ),
                json.dumps(
                    {"type": "turn_context", "payload": {"model": "gpt-5.3-codex"}}
                ),
            ]
        ),
        encoding="utf-8",
    )

    candidates = discover_codex_model_candidates(
        selected_model="gpt-5.1-codex-mini",
        resolved_model="gpt-5.4-codex",
        config_path=config_path,
        sessions_root=sessions_root,
        limit=6,
    )

    assert candidates == [
        "gpt-5.4-codex",
        "gpt-5.1-codex-mini",
        "gpt-5.4",
        "gpt-5.3-codex",
        "gpt-5.2-codex",
        "gpt-5",
    ]


def test_build_codex_model_keyboard_marks_selected_model(monkeypatch):
    """Keyboard should render discovered models and mark the active selection."""

    def _fake_discover(**_: object) -> list[str]:
        return ["gpt-5.4", "gpt-5.3-codex"]

    monkeypatch.setattr(
        "src.bot.utils.codex_models.discover_codex_model_candidates",
        _fake_discover,
    )

    keyboard = build_codex_model_keyboard(selected_model="gpt-5.3-codex")
    rows = keyboard.inline_keyboard

    assert rows[0][0].text == "gpt-5.4"
    assert rows[0][0].callback_data == "model:codex:gpt-5.4"
    assert rows[1][0].text == "✅ gpt-5.3-codex"
    assert rows[1][0].callback_data == "model:codex:gpt-5.3-codex"
    assert rows[-1][0].text == "default"
    assert rows[-1][0].callback_data == "model:codex:default"
