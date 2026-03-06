"""Helpers for concise /resume session summaries."""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime
from typing import Any, Sequence

from ...utils.beijing_time import format_datetime_beijing

_LOW_SIGNAL_EXACT = {
    "好",
    "好的",
    "嗯",
    "嗯嗯",
    "哦",
    "哦哦",
    "ok",
    "okay",
    "yes",
    "no",
    "收到",
    "行",
    "可以",
    "谢了",
    "谢谢",
    "thanks",
    "thank you",
}

_LOW_SIGNAL_FRAGMENTS = (
    "继续",
    "接着",
    "先这样",
    "不用了",
    "不用",
    "算了",
    "试试",
    "再试",
    "continue",
    "go on",
    "carry on",
    "no need",
)

_PUNCT_RE = re.compile(r"[，。！？、,.!?;:：/\-_#()]")


def normalize_resume_preview(raw: str, *, max_len: int) -> str:
    """Normalize preview text into one compact line."""
    compact = " ".join(str(raw or "").split())
    if not compact:
        return "无预览"
    if len(compact) <= max_len:
        return compact
    return compact[: max_len - 3].rstrip() + "..."


def _is_low_signal_follow_up(text: str) -> bool:
    """Detect short follow-up messages that are poor session summaries."""
    normalized = " ".join(text.lower().split()).strip()
    if not normalized:
        return True
    if normalized in _LOW_SIGNAL_EXACT:
        return True
    if len(normalized) > 18:
        return False
    return any(fragment in normalized for fragment in _LOW_SIGNAL_FRAGMENTS)


def _summary_score(text: str, *, prefer_topic: bool) -> int:
    """Score one message for use as resume summary."""
    compact = " ".join(str(text or "").split()).strip()
    if not compact:
        return -10_000

    score = min(len(compact), 48)
    if prefer_topic:
        score += 4
    if len(compact) < 6:
        score -= 18
    elif len(compact) < 10:
        score -= 8
    if _is_low_signal_follow_up(compact):
        score -= 28
    if len(compact.split()) >= 3:
        score += 3
    if _PUNCT_RE.search(compact):
        score += 2
    return score


def build_resume_session_summary(
    *,
    thread_name: str,
    first_message: str,
    last_user_message: str,
    previous_user_message: str,
    max_len: int,
) -> str:
    """Choose a compact, topic-like summary for one resumable session."""
    thread = " ".join(str(thread_name or "").split()).strip()
    first = " ".join(str(first_message or "").split()).strip()
    last = " ".join(str(last_user_message or "").split()).strip()
    previous = " ".join(str(previous_user_message or "").split()).strip()

    if not thread and not first and not last and not previous:
        return "无预览"
    if thread:
        return normalize_resume_preview(thread, max_len=max_len)
    if last and not _is_low_signal_follow_up(last):
        return normalize_resume_preview(last, max_len=max_len)
    if previous and not _is_low_signal_follow_up(previous):
        return normalize_resume_preview(previous, max_len=max_len)
    if first:
        return normalize_resume_preview(first, max_len=max_len)
    if last:
        return normalize_resume_preview(last, max_len=max_len)
    if previous:
        return normalize_resume_preview(previous, max_len=max_len)
    return "无预览"


def _candidate_event_time(candidate: Any) -> datetime | None:
    """Resolve candidate event time, fallback to file mtime."""
    last_event_at = getattr(candidate, "last_event_at", None)
    if isinstance(last_event_at, datetime):
        return last_event_at

    file_mtime = getattr(candidate, "file_mtime", None)
    if isinstance(file_mtime, datetime):
        return file_mtime
    return None


def _format_relative_time(target: datetime | None) -> str:
    """Format relative age from UTC naive datetime."""
    if target is None:
        return "时间未知"

    now = datetime.utcnow()
    delta_sec = max(0, int((now - target).total_seconds()))
    if delta_sec < 60:
        return "刚刚"
    if delta_sec < 3600:
        return f"{delta_sec // 60}分钟前"
    if delta_sec < 86400:
        return f"{delta_sec // 3600}小时前"
    if delta_sec < 86400 * 7:
        return f"{delta_sec // 86400}天前"
    return format_datetime_beijing(target, fmt="%m-%d %H:%M")


def _resume_candidate_preview(candidate: Any, *, max_len: int) -> str:
    """Build compact session preview text from scanner candidate."""
    preview = build_resume_session_summary(
        thread_name=str(getattr(candidate, "thread_name", "") or "").strip(),
        first_message=str(getattr(candidate, "first_message", "") or "").strip(),
        last_user_message=str(
            getattr(candidate, "last_user_message", "") or ""
        ).strip(),
        previous_user_message=str(
            getattr(candidate, "previous_user_message", "") or ""
        ).strip(),
        max_len=max_len,
    )
    if preview == "无预览":
        return "no preview"
    return preview


def _resume_session_suffix(session_id: str) -> str:
    """Return a short display suffix for one session id."""
    compact = str(session_id or "").strip()
    return compact[-4:] or "unknown"


def _truncate_button_label(label: str, *, max_len: int) -> str:
    """Keep button label within a compact size budget."""
    if len(label) <= max_len:
        return label
    return label[: max_len - 3].rstrip() + "..."


def build_resume_button_label(
    candidate: Any,
    *,
    preview_max_len: int = 18,
    max_label_len: int = 60,
    include_time: bool = True,
    include_session_suffix: bool = False,
) -> str:
    """Build concise resume button label for one session."""
    active = bool(getattr(candidate, "is_probably_active", False))
    preview = _resume_candidate_preview(candidate, max_len=preview_max_len)
    parts = []
    if include_time:
        parts.append(
            "活跃中"
            if active
            else _format_relative_time(_candidate_event_time(candidate))
        )
    parts.append(preview)
    if include_session_suffix:
        parts.append(_resume_session_suffix(getattr(candidate, "session_id", "")))
    return _truncate_button_label(" · ".join(parts), max_len=max_label_len)


def build_resume_button_labels(
    candidates: Sequence[Any],
    *,
    preview_max_len: int = 18,
    max_label_len: int = 60,
    include_time: bool = True,
) -> list[str]:
    """Build stable, compact button labels for a page of resume candidates."""
    base_labels = [
        build_resume_button_label(
            candidate,
            preview_max_len=preview_max_len,
            max_label_len=max_label_len,
            include_time=include_time,
            include_session_suffix=False,
        )
        for candidate in candidates
    ]
    duplicates = Counter(base_labels)
    return [
        build_resume_button_label(
            candidate,
            preview_max_len=preview_max_len,
            max_label_len=max_label_len,
            include_time=include_time,
            include_session_suffix=duplicates[base_label] > 1,
        )
        for candidate, base_label in zip(candidates, base_labels)
    ]
