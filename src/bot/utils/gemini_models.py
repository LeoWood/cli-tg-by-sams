"""Helpers for Gemini model discovery and Telegram keyboard rendering."""

from __future__ import annotations

import json
import time
from heapq import nlargest
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

_DEFAULT_GEMINI_MODEL_CANDIDATES = (
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-3-flash-preview",
    "gemini-3.1-pro-preview",
)
_GEMINI_MODEL_CACHE_TTL_SECONDS = 30.0
_GEMINI_MODEL_CACHE: dict[str, tuple[float, list[str]]] = {}


def _normalize_gemini_model_candidate(value: str | None) -> str:
    """Normalize a Gemini model candidate string for keyboard display."""
    normalized = str(value or "").strip().replace("`", "")
    if not normalized:
        return ""
    if normalized.lower() in {"default", "current", "auto"}:
        return ""
    if any(ch in normalized for ch in ("\r", "\n", "\t")):
        return ""
    return normalized


def _append_unique_model(
    candidates: list[str],
    value: str | None,
    *,
    limit: int | None = None,
) -> bool:
    """Append model when valid and not already present."""
    normalized = _normalize_gemini_model_candidate(value)
    if not normalized:
        return False
    if normalized in candidates:
        return False
    candidates.append(normalized)
    if limit is None:
        return False
    return len(candidates) >= limit


def _extract_recent_models_from_session_file(
    session_file: Path,
    *,
    limit: int = 3,
) -> list[str]:
    """Extract a few recent Gemini models from one local session JSON file."""
    try:
        raw = json.loads(session_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, dict):
        return []

    messages = raw.get("messages")
    if not isinstance(messages, list):
        return []

    candidates: list[str] = []
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        if str(message.get("type") or "").strip().lower() != "gemini":
            continue
        if _append_unique_model(candidates, message.get("model"), limit=limit):
            break
    return candidates


def _discover_local_gemini_models(
    *,
    sessions_root: Path,
    limit: int,
) -> list[str]:
    """Discover Gemini models from recent local session files."""
    cache_key = str(sessions_root)
    cached = _GEMINI_MODEL_CACHE.get(cache_key)
    if cached:
        cached_at, models = cached
        if time.monotonic() - cached_at <= _GEMINI_MODEL_CACHE_TTL_SECONDS:
            return list(models[:limit])

    discovered: list[str] = []
    if sessions_root.is_dir():
        session_files: list[tuple[float, Path]] = []
        try:
            for candidate in sessions_root.rglob("session-*.json"):
                try:
                    session_files.append((candidate.stat().st_mtime, candidate))
                except OSError:
                    continue
        except OSError:
            session_files = []

        for _, session_file in nlargest(16, session_files, key=lambda item: item[0]):
            for model in _extract_recent_models_from_session_file(
                session_file, limit=3
            ):
                if _append_unique_model(discovered, model, limit=limit):
                    break
            if len(discovered) >= limit:
                break

    _GEMINI_MODEL_CACHE[cache_key] = (time.monotonic(), list(discovered))
    return list(discovered[:limit])


def discover_gemini_model_candidates(
    *,
    selected_model: str | None,
    resolved_model: str | None = None,
    sessions_root: Path | None = None,
    limit: int = 8,
) -> list[str]:
    """Build Gemini model candidates for the /model keyboard."""
    if limit <= 0:
        return []

    resolved = _normalize_gemini_model_candidate(resolved_model)
    selected = _normalize_gemini_model_candidate(selected_model)
    local_sessions = sessions_root or (Path.home() / ".gemini" / "tmp")

    candidates: list[str] = []
    for value in (resolved, selected):
        _append_unique_model(candidates, value, limit=limit)
        if len(candidates) >= limit:
            return candidates[:limit]

    local_models = _discover_local_gemini_models(
        sessions_root=local_sessions,
        limit=limit,
    )
    for value in (*local_models, *_DEFAULT_GEMINI_MODEL_CANDIDATES):
        if _append_unique_model(candidates, value, limit=limit):
            break
    return candidates[:limit]


def build_gemini_model_keyboard(
    *,
    selected_model: str | None,
    resolved_model: str | None = None,
) -> InlineKeyboardMarkup:
    """Build inline keyboard for Gemini model selection."""
    selected = _normalize_gemini_model_candidate(selected_model)
    rows: list[list[InlineKeyboardButton]] = []

    for value in discover_gemini_model_candidates(
        selected_model=selected,
        resolved_model=resolved_model,
    ):
        label = f"✅ {value}" if value == selected else value
        rows.append(
            [InlineKeyboardButton(label, callback_data=f"model:gemini:{value}")]
        )

    default_label = "✅ auto/default" if not selected else "auto/default"
    rows.append(
        [InlineKeyboardButton(default_label, callback_data="model:gemini:default")]
    )
    return InlineKeyboardMarkup(rows)
