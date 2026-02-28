"""Tests for auto-delivery of generated images back to Telegram."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.bot.handlers.message import (
    _extract_generated_file_paths,
    _extract_generated_image_paths,
    _send_generated_files_from_response,
    _send_generated_images_from_response,
)
from src.config.settings import Settings


def _build_settings(approved_directory: Path) -> Settings:
    return Settings(
        telegram_bot_token="test:token",
        telegram_bot_username="testbot",
        approved_directory=approved_directory,
        use_sdk=True,
    )


def test_extract_generated_image_paths_collects_from_text_and_tool_inputs(tmp_path):
    """Should extract valid image paths from response text and tool records."""
    approved = tmp_path / "approved"
    approved.mkdir()
    current_dir = approved / "workspace"
    current_dir.mkdir()

    img_text = current_dir / "duck.png"
    img_tool = current_dir / "outputs" / "bird.jpg"
    img_tool.parent.mkdir(parents=True, exist_ok=True)
    img_text.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 128)
    img_tool.write_bytes(b"\xff\xd8\xff" + b"\x00" * 128)

    response = SimpleNamespace(
        content=f"图片已生成\n文件路径：{img_text}\n你也可以查看 `{img_text}`",
        tools_used=[{"name": "Write", "input": {"output_path": str(img_tool)}}],
    )

    resolved = _extract_generated_image_paths(
        claude_response=response,
        scope_state={"current_directory": current_dir},
        approved_directory=approved,
    )

    assert resolved == [img_text, img_tool]


def test_extract_generated_image_paths_rejects_outside_approved_directory(tmp_path):
    """Should not auto-deliver files outside approved workspace."""
    approved = tmp_path / "approved"
    approved.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_img = outside / "secret.png"
    outside_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)

    response = SimpleNamespace(
        content=f"文件路径：{outside_img}",
        tools_used=[],
    )

    resolved = _extract_generated_image_paths(
        claude_response=response,
        scope_state={"current_directory": approved},
        approved_directory=approved,
    )

    assert resolved == []


@pytest.mark.asyncio
async def test_send_generated_images_from_response_replies_with_document(tmp_path):
    """Should send detected generated image back to Telegram as document."""
    approved = tmp_path / "approved"
    approved.mkdir()
    current_dir = approved / "workspace"
    current_dir.mkdir()
    image_path = current_dir / "duck.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 256)

    response = SimpleNamespace(
        content=f"文件路径：{image_path}",
        tools_used=[],
    )

    reply_document = AsyncMock()
    message = SimpleNamespace(reply_document=reply_document)
    update = SimpleNamespace(
        message=message,
        effective_user=SimpleNamespace(id=1001),
        effective_chat=SimpleNamespace(type="private"),
    )
    context = SimpleNamespace(bot_data={"settings": _build_settings(approved)})

    sent = await _send_generated_images_from_response(
        update=update,
        context=context,
        claude_response=response,
        scope_state={"current_directory": current_dir},
        reply_to_message_id=7788,
    )

    assert sent == 1
    kwargs = reply_document.await_args.kwargs
    assert kwargs["filename"] == "duck.png"
    assert kwargs["caption"] == "🖼 已回传生成图片：duck.png"
    assert kwargs["reply_to_message_id"] == 7788


def test_extract_generated_file_paths_collects_non_image_files(tmp_path):
    """Should extract valid non-image file paths from content and tool payload."""
    approved = tmp_path / "approved"
    approved.mkdir()
    current_dir = approved / "workspace"
    current_dir.mkdir()

    report = current_dir / "report.pdf"
    log_file = current_dir / "logs" / "run.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    report.write_bytes(b"%PDF-1.4\n" + b"0" * 64)
    log_file.write_text("ok", encoding="utf-8")

    response = SimpleNamespace(
        content=f"文件路径：{report}",
        tools_used=[{"name": "Write", "input": {"output_path": str(log_file)}}],
    )

    resolved = _extract_generated_file_paths(
        claude_response=response,
        scope_state={"current_directory": current_dir},
        approved_directory=approved,
    )

    assert resolved == [report, log_file]


@pytest.mark.asyncio
async def test_send_generated_files_from_response_replies_with_document(tmp_path):
    """Should send detected non-image file back to Telegram as document."""
    approved = tmp_path / "approved"
    approved.mkdir()
    current_dir = approved / "workspace"
    current_dir.mkdir()
    report = current_dir / "report.pdf"
    report.write_bytes(b"%PDF-1.4\n" + b"1" * 128)

    response = SimpleNamespace(
        content=f"文件路径：{report}",
        tools_used=[],
    )

    reply_document = AsyncMock()
    message = SimpleNamespace(reply_document=reply_document)
    update = SimpleNamespace(
        message=message,
        effective_user=SimpleNamespace(id=1002),
        effective_chat=SimpleNamespace(type="private"),
    )
    context = SimpleNamespace(bot_data={"settings": _build_settings(approved)})

    sent = await _send_generated_files_from_response(
        update=update,
        context=context,
        claude_response=response,
        scope_state={"current_directory": current_dir},
        reply_to_message_id=9901,
    )

    assert sent == 1
    kwargs = reply_document.await_args.kwargs
    assert kwargs["filename"] == "report.pdf"
    assert kwargs["caption"] == "📎 已回传文件：report.pdf"
    assert kwargs["reply_to_message_id"] == 9901
