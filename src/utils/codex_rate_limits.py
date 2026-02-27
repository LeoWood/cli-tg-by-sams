"""Utilities for parsing Codex token-count rate limit payloads."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .beijing_time import format_iso_datetime_beijing, format_unix_timestamp_beijing


def _format_unix_timestamp(value: Any) -> str:
    return format_unix_timestamp_beijing(value)


def _window_label(window_minutes: int) -> str:
    if window_minutes % 10_080 == 0:
        days = window_minutes // 1_440
        return f"{days}d window"
    if window_minutes % 60 == 0:
        hours = window_minutes // 60
        return f"{hours}h window"
    return f"{window_minutes}m window"


def normalize_codex_rate_limits(
    payload: Any, *, event_timestamp: str = ""
) -> Optional[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return None

    def _normalize_entry(entry: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(entry, dict):
            return None
        used_percent_raw = entry.get("used_percent")
        window_minutes_raw = entry.get("window_minutes")
        resets_at_raw = entry.get("resets_at")
        try:
            used_percent = float(used_percent_raw)
        except (TypeError, ValueError):
            return None
        try:
            window_minutes = int(window_minutes_raw)
        except (TypeError, ValueError):
            return None
        if window_minutes <= 0:
            return None

        normalized: Dict[str, Any] = {
            "used_percent": used_percent,
            "window_minutes": window_minutes,
        }
        try:
            resets_at = int(resets_at_raw)
            if resets_at > 0:
                normalized["resets_at"] = resets_at
        except (TypeError, ValueError):
            pass
        return normalized

    primary = _normalize_entry(payload.get("primary"))
    secondary = _normalize_entry(payload.get("secondary"))
    if primary is None and secondary is None:
        return None

    result: Dict[str, Any] = {}
    if primary is not None:
        result["primary"] = primary
    if secondary is not None:
        result["secondary"] = secondary

    timestamp_text = event_timestamp.strip()
    if timestamp_text:
        try:
            parsed_at = datetime.fromisoformat(timestamp_text.replace("Z", "+00:00"))
            result["updated_at"] = (
                parsed_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
            )
        except ValueError:
            result["updated_at"] = timestamp_text

    return result


def iter_rate_limit_entries(rate_limits: Any) -> List[Dict[str, Any]]:
    if not isinstance(rate_limits, dict):
        return []

    entries: List[Dict[str, Any]] = []
    for key in ("primary", "secondary"):
        entry = rate_limits.get(key)
        if not isinstance(entry, dict):
            continue

        used_percent = entry.get("used_percent")
        window_minutes = entry.get("window_minutes")
        try:
            used_percent = float(used_percent)
            window_minutes = int(window_minutes)
        except (TypeError, ValueError):
            continue
        if window_minutes <= 0:
            continue

        entries.append(
            {
                "label": _window_label(window_minutes),
                "window_minutes": window_minutes,
                "used_percent": used_percent,
                "reset_text": _format_unix_timestamp(entry.get("resets_at")),
            }
        )

    entries.sort(key=lambda item: item["window_minutes"])
    return entries


def format_rate_limit_summary(rate_limits: Any) -> Optional[str]:
    entries = iter_rate_limit_entries(rate_limits)
    if not entries:
        return None

    parts = []
    for entry in entries:
        used_percent = max(min(float(entry["used_percent"]), 100.0), 0.0)
        remaining_percent = max(min(100.0 - used_percent, 100.0), 0.0)
        line = f"{entry['label']}: {remaining_percent:.1f}% remaining"
        if entry.get("reset_text"):
            line += f" (resets {entry['reset_text']})"
        parts.append(line)

    summary = "\n".join(parts)
    updated = format_iso_datetime_beijing(rate_limits.get("updated_at"))
    if updated:
        summary += f"\n(updated {updated})"
    return summary
