"""Helpers for Codex model discovery and Telegram keyboard rendering."""

from __future__ import annotations

import json
import re
import time
from heapq import nlargest
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

_DEFAULT_CODEX_MODEL_CANDIDATES = (
    "gpt-5.4",
    "gpt-5.3-codex",
    "gpt-5.1-codex-mini",
    "gpt-5",
)
_CODEX_MODEL_CACHE_TTL_SECONDS = 30.0
_MODEL_LINE_PATTERN = re.compile(r'^\s*model\s*=\s*["\']([^"\']+)["\']\s*$')
_CODEX_MODEL_CACHE: dict[tuple[str, str], tuple[float, list[str]]] = {}


def _normalize_codex_model_candidate(value: str | None) -> str:
    """Normalize a model candidate string for keyboard display."""
    normalized = str(value or "").strip().replace("`", "")
    if not normalized:
        return ""
    if normalized.lower() in {"default", "current"}:
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
    """Append model when it is valid and not already present."""
    normalized = _normalize_codex_model_candidate(value)
    if not normalized:
        return False
    if normalized in candidates:
        return False
    candidates.append(normalized)
    if limit is None:
        return False
    return len(candidates) >= limit


def _read_codex_config_model(config_path: Path) -> str:
    """Read default model from ~/.codex/config.toml using a light regex parse."""
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return ""

    for line in text.splitlines():
        match = _MODEL_LINE_PATTERN.match(line)
        if match:
            return _normalize_codex_model_candidate(match.group(1))
    return ""


def _extract_recent_models_from_session_file(
    session_file: Path,
    *,
    limit: int = 3,
) -> list[str]:
    """Extract a few recent models from a Codex session JSONL file."""
    try:
        size = session_file.stat().st_size
    except OSError:
        return []
    if size <= 0:
        return []

    try:
        chunk_size = min(size, 131_072)
        with session_file.open("rb") as fh:
            fh.seek(max(0, size - chunk_size))
            data = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return []

    candidates: list[str] = []
    lines = [line.strip() for line in data.splitlines() if line.strip()]
    for line in reversed(lines):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue

        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue

        record_type = str(record.get("type") or "").strip()
        if record_type not in {"session_meta", "turn_context"}:
            continue

        if _append_unique_model(candidates, payload.get("model"), limit=limit):
            break

    return candidates


def _discover_local_codex_models(
    *,
    config_path: Path,
    sessions_root: Path,
    limit: int,
) -> list[str]:
    """Discover Codex models from local config and recent session files."""
    cache_key = (str(config_path), str(sessions_root))
    cached = _CODEX_MODEL_CACHE.get(cache_key)
    if cached:
        cached_at, models = cached
        if time.monotonic() - cached_at <= _CODEX_MODEL_CACHE_TTL_SECONDS:
            return list(models[:limit])

    discovered: list[str] = []
    _append_unique_model(discovered, _read_codex_config_model(config_path), limit=limit)

    if sessions_root.is_dir() and len(discovered) < limit:
        session_files: list[tuple[float, Path]] = []
        try:
            for candidate in sessions_root.rglob("*.jsonl"):
                try:
                    session_files.append((candidate.stat().st_mtime, candidate))
                except OSError:
                    continue
        except OSError:
            session_files = []

        for _, session_file in nlargest(12, session_files, key=lambda item: item[0]):
            models = _extract_recent_models_from_session_file(session_file, limit=3)
            for model in models:
                if _append_unique_model(discovered, model, limit=limit):
                    break
            if len(discovered) >= limit:
                break

    _CODEX_MODEL_CACHE[cache_key] = (time.monotonic(), list(discovered))
    return list(discovered[:limit])


def discover_codex_model_candidates(
    *,
    selected_model: str | None,
    resolved_model: str | None = None,
    config_path: Path | None = None,
    sessions_root: Path | None = None,
    limit: int = 8,
) -> list[str]:
    """Build model candidates for the Codex /model keyboard."""
    if limit <= 0:
        return []

    resolved = _normalize_codex_model_candidate(resolved_model)
    selected = _normalize_codex_model_candidate(selected_model)
    local_config = config_path or (Path.home() / ".codex" / "config.toml")
    local_sessions = sessions_root or (Path.home() / ".codex" / "sessions")

    candidates: list[str] = []
    for value in (resolved, selected):
        _append_unique_model(candidates, value, limit=limit)
        if len(candidates) >= limit:
            return candidates[:limit]

    local_models = _discover_local_codex_models(
        config_path=local_config,
        sessions_root=local_sessions,
        limit=limit,
    )
    for value in (*local_models, *_DEFAULT_CODEX_MODEL_CANDIDATES):
        if _append_unique_model(candidates, value, limit=limit):
            break

    return candidates[:limit]


def build_codex_model_keyboard(
    *,
    selected_model: str | None,
    resolved_model: str | None = None,
) -> InlineKeyboardMarkup:
    """Build inline keyboard for Codex model selection."""
    selected = _normalize_codex_model_candidate(selected_model)
    rows: list[list[InlineKeyboardButton]] = []

    for value in discover_codex_model_candidates(
        selected_model=selected,
        resolved_model=resolved_model,
    ):
        label = f"✅ {value}" if value == selected else value
        rows.append([InlineKeyboardButton(label, callback_data=f"model:codex:{value}")])

    default_label = "✅ default" if not selected else "default"
    rows.append(
        [InlineKeyboardButton(default_label, callback_data="model:codex:default")]
    )
    return InlineKeyboardMarkup(rows)
