"""Helpers for rendering user-facing timestamps in Beijing time."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python <3.9 fallback
    ZoneInfo = None  # type: ignore[assignment]


def _build_beijing_tz() -> tzinfo:
    """Prefer Asia/Shanghai zoneinfo, fallback to fixed UTC+8 offset."""
    if ZoneInfo is not None:
        try:
            return ZoneInfo("Asia/Shanghai")
        except Exception:
            pass
    return timezone(timedelta(hours=8), name="Asia/Shanghai")


BEIJING_TZ = _build_beijing_tz()


def to_beijing_datetime(value: datetime, *, naive_is_utc: bool = True) -> datetime:
    """Convert datetime to Beijing timezone.

    Naive datetime values are treated as UTC by default because storage records
    in this project are UTC-naive.
    """
    target = value
    if target.tzinfo is None:
        if naive_is_utc:
            target = target.replace(tzinfo=timezone.utc)
        else:
            target = target.replace(tzinfo=BEIJING_TZ)
    return target.astimezone(BEIJING_TZ)


def now_beijing() -> datetime:
    """Return current time in Beijing timezone."""
    return datetime.now(BEIJING_TZ)


def format_datetime_beijing(
    value: datetime,
    *,
    fmt: str = "%Y-%m-%d %H:%M:%S",
    naive_is_utc: bool = True,
) -> str:
    """Format datetime in Beijing timezone."""
    return to_beijing_datetime(value, naive_is_utc=naive_is_utc).strftime(fmt)


def format_unix_timestamp_beijing(
    value: Any, *, fmt: str = "%Y-%m-%d %H:%M"
) -> str:
    """Format unix timestamp in Beijing timezone."""
    try:
        ts = int(value)
    except (TypeError, ValueError):
        return ""
    if ts <= 0:
        return ""
    try:
        return datetime.fromtimestamp(ts, tz=BEIJING_TZ).strftime(fmt)
    except (OverflowError, OSError, ValueError):
        return ""


def format_iso_datetime_beijing(
    value: Any,
    *,
    fmt: str = "%Y-%m-%d %H:%M:%S",
    naive_is_utc: bool = True,
) -> str:
    """Parse ISO text and render in Beijing timezone.

    Returns empty string when input is empty; returns original text when parsing
    fails to avoid dropping existing diagnostics.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    return format_datetime_beijing(parsed, fmt=fmt, naive_is_utc=naive_is_utc)
