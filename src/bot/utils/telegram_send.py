"""Telegram send helpers with parse/thread/length fallbacks."""

from __future__ import annotations

import asyncio
from typing import Any, Optional

_TELEGRAM_MESSAGE_LIMIT = 4096
_TELEGRAM_SAFE_SPLIT_LIMIT = 3800
_TELEGRAM_TRANSIENT_RETRY_DELAYS_SECONDS: tuple[float, ...] = (0.35, 0.8)


def is_markdown_parse_error(error: Exception) -> bool:
    """Whether Telegram send failure is caused by entity parsing."""
    error_text = str(error).lower()
    return "can't parse entities" in error_text or "cannot parse entities" in error_text


def is_message_too_long_error(error: Exception) -> bool:
    """Whether Telegram send failure is caused by message length overflow."""
    error_text = str(error).lower()
    return (
        "message is too long" in error_text
        or "text is too long" in error_text
        or "entity is too long" in error_text
    )


def is_thread_not_found_error(error: Exception) -> bool:
    """Whether Telegram rejected the provided topic/thread id."""
    error_text = str(error).lower()
    return "message thread not found" in error_text or "thread not found" in error_text


def is_transient_network_error(error: Exception) -> bool:
    """Whether Telegram send failure looks like a transient network/proxy issue."""
    error_text = str(error).lower()
    error_type = type(error).__name__.lower()

    if "httpx.connecterror" in error_text or "httpcore.connecterror" in error_text:
        return True
    if error_type == "networkerror":
        return True

    transient_markers = (
        "connection reset",
        "connection aborted",
        "connection refused",
        "server disconnected",
        "remote protocol error",
        "temporary failure",
        "timed out",
        "timeout",
        "tls",
    )
    return any(marker in error_text for marker in transient_markers)


async def _send_message_with_retry(bot: Any, *, send_kwargs: dict[str, Any]) -> Any:
    """Send Telegram message with short retry for transient network hiccups."""
    max_attempts = len(_TELEGRAM_TRANSIENT_RETRY_DELAYS_SECONDS) + 1
    last_error: Exception | None = None

    for attempt_idx in range(max_attempts):
        try:
            return await bot.send_message(**send_kwargs)
        except Exception as send_error:
            last_error = send_error
            if attempt_idx >= max_attempts - 1 or not is_transient_network_error(
                send_error
            ):
                raise
            await asyncio.sleep(_TELEGRAM_TRANSIENT_RETRY_DELAYS_SECONDS[attempt_idx])

    if last_error is not None:
        raise last_error
    raise RuntimeError("Failed to send Telegram message: unknown send error")


def split_text_for_telegram(
    text: str, limit: int = _TELEGRAM_SAFE_SPLIT_LIMIT
) -> list[str]:
    """Split long plain text into Telegram-safe chunks."""
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break

        split_at = remaining.rfind("\n", 0, limit + 1)
        if split_at <= 0:
            split_at = remaining.rfind(" ", 0, limit + 1)
        if split_at <= 0:
            split_at = limit

        chunk = remaining[:split_at].rstrip()
        if not chunk:
            chunk = remaining[:limit]
            split_at = len(chunk)

        chunks.append(chunk)
        remaining = remaining[split_at:].lstrip("\n")

    return chunks


def normalize_message_thread_id(
    message_thread_id: Optional[int],
    *,
    chat_type: Optional[str] = None,
) -> Optional[int]:
    """Normalize thread id with DM/general-topic safety rules."""
    normalized_chat_type = str(chat_type or "").strip().lower()
    if normalized_chat_type == "private":
        return None

    if message_thread_id is None:
        return None

    try:
        thread_id = int(message_thread_id)
    except (TypeError, ValueError):
        return None

    # Telegram forum "General" topic is id=1, should not be explicitly sent.
    if thread_id <= 1:
        return None

    return thread_id


async def send_message_resilient(
    bot: Any,
    *,
    chat_id: int,
    text: str,
    parse_mode: Optional[str] = None,
    reply_markup: Any = None,
    reply_to_message_id: Optional[int] = None,
    message_thread_id: Optional[int] = None,
    chat_type: Optional[str] = None,
) -> Any:
    """Send message with parse fallback, threadless retry and long-text split."""
    normalized_thread_id = normalize_message_thread_id(
        message_thread_id, chat_type=chat_type
    )

    send_kwargs: dict[str, Any] = {"chat_id": chat_id, "text": text}
    if parse_mode:
        send_kwargs["parse_mode"] = parse_mode
    if reply_markup is not None:
        send_kwargs["reply_markup"] = reply_markup
    if isinstance(reply_to_message_id, int) and reply_to_message_id > 0:
        send_kwargs["reply_to_message_id"] = reply_to_message_id
    if normalized_thread_id is not None:
        send_kwargs["message_thread_id"] = normalized_thread_id

    active_kwargs = dict(send_kwargs)

    try:
        return await _send_message_with_retry(bot, send_kwargs=active_kwargs)
    except Exception as send_error:
        final_error: Exception = send_error

    if "parse_mode" in active_kwargs and is_markdown_parse_error(final_error):
        no_md_kwargs = dict(active_kwargs)
        no_md_kwargs.pop("parse_mode", None)
        try:
            return await _send_message_with_retry(bot, send_kwargs=no_md_kwargs)
        except Exception as no_md_error:
            final_error = no_md_error
            active_kwargs = no_md_kwargs

    if "message_thread_id" in active_kwargs and is_thread_not_found_error(final_error):
        no_thread_kwargs = dict(active_kwargs)
        no_thread_kwargs.pop("message_thread_id", None)
        try:
            return await _send_message_with_retry(bot, send_kwargs=no_thread_kwargs)
        except Exception as no_thread_error:
            final_error = no_thread_error
            active_kwargs = no_thread_kwargs

        if "parse_mode" in active_kwargs and is_markdown_parse_error(final_error):
            no_thread_no_md_kwargs = dict(active_kwargs)
            no_thread_no_md_kwargs.pop("parse_mode", None)
            try:
                return await _send_message_with_retry(
                    bot, send_kwargs=no_thread_no_md_kwargs
                )
            except Exception as no_thread_no_md_error:
                final_error = no_thread_no_md_error
                active_kwargs = no_thread_no_md_kwargs

    if is_message_too_long_error(final_error) or len(text) > _TELEGRAM_MESSAGE_LIMIT:
        chunks = split_text_for_telegram(text)
        chunk_base_kwargs = dict(active_kwargs)
        chunk_base_kwargs.pop("parse_mode", None)

        last_message = None
        for idx, chunk in enumerate(chunks):
            chunk_kwargs = dict(chunk_base_kwargs)
            chunk_kwargs["text"] = chunk
            if idx > 0:
                chunk_kwargs.pop("reply_markup", None)
            last_message = await _send_message_with_retry(bot, send_kwargs=chunk_kwargs)
        return last_message

    raise final_error
