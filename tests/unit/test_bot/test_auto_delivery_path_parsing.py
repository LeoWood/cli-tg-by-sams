"""Tests for auto-delivery path extraction from assistant text."""

from src.bot.handlers.message import _collect_candidate_file_paths_from_text


def test_collect_file_paths_accepts_backtick_wrapped_absolute_path():
    """Backtick-wrapped absolute file path should still be extracted."""
    content = (
        "已为你准备好并放到 Telegram 回传目录。\n\n"
        "文件路径: `/Users/leo/Projects/AIGC/.tg-delivery/agents.md`\n"
    )

    candidates = _collect_candidate_file_paths_from_text(content)

    assert candidates == ["/Users/leo/Projects/AIGC/.tg-delivery/agents.md"]
