"""Tests for Codex rate-limit summary text shown in progress context tag."""

from src.utils.codex_rate_limits import format_rate_limit_summary


def test_format_rate_limit_summary_shows_remaining_percent():
    """Rate-limit summary should render remaining percentage for each window."""
    summary = format_rate_limit_summary(
        {
            "primary": {"used_percent": 12.5, "window_minutes": 300},
            "secondary": {"used_percent": 37.0, "window_minutes": 10_080},
            "updated_at": "2026-02-15T10:56:46.914000Z",
        }
    )

    assert summary is not None
    assert "5h window: 87.5% remaining" in summary
    assert "7d window: 63.0% remaining" in summary
    assert "\n" in summary
    assert "(updated 2026-02-15 18:56:46)" in summary


def test_format_rate_limit_summary_clamps_remaining_between_0_and_100():
    """Remaining percentage should be clamped for unexpected used_percent values."""
    summary = format_rate_limit_summary(
        {
            "primary": {"used_percent": 120.0, "window_minutes": 300},
            "secondary": {"used_percent": -3.0, "window_minutes": 10_080},
        }
    )

    assert summary is not None
    assert "5h window: 0.0% remaining" in summary
    assert "7d window: 100.0% remaining" in summary
    assert "\n" in summary
