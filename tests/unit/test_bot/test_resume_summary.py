"""Tests for /resume session summary selection."""

from datetime import datetime, timedelta

from src.bot.handlers.callback import _build_resume_session_button_label
from src.bot.utils.resume_summary import (
    build_resume_button_labels,
    build_resume_session_summary,
)


class _Candidate:
    def __init__(
        self,
        *,
        thread_name: str = "",
        first_message: str,
        last_user_message: str,
        previous_user_message: str = "",
        session_id: str = "019cc220-demo",
        file_mtime: datetime | None = None,
    ) -> None:
        self.session_id = session_id
        self.thread_name = thread_name
        self.first_message = first_message
        self.last_user_message = last_user_message
        self.previous_user_message = previous_user_message
        self.is_probably_active = False
        self.last_event_at = None
        self.file_mtime = file_mtime or (datetime.utcnow() - timedelta(days=2))


def test_resume_summary_prefers_previous_message_when_last_is_low_signal():
    """Short follow-up messages should fall back to the prior user prompt."""
    summary = build_resume_session_summary(
        thread_name="",
        first_message="把本次的配置方案梳理下，输出 checklist",
        last_user_message="我说不用了",
        previous_user_message="修复 /resume 菜单的摘要显示",
        max_len=18,
    )

    assert summary == "修复 /resume 菜单的摘要显示"


def test_resume_summary_keeps_latest_message_when_it_is_informative():
    """Informative latest messages should remain the preferred preview."""
    summary = build_resume_session_summary(
        thread_name="",
        first_message="继续这个项目",
        last_user_message="修复 /resume 的摘要显示逻辑",
        previous_user_message="上一条任务",
        max_len=18,
    )

    assert summary == "修复 /resume 的摘要显示逻辑"


def test_resume_summary_falls_back_to_first_when_recent_messages_are_low_signal():
    """First message should be used only when recent user messages are weak."""
    summary = build_resume_session_summary(
        thread_name="",
        first_message="把本次的配置方案梳理下，输出 checklist",
        last_user_message="好的",
        previous_user_message="继续",
        max_len=18,
    )

    assert summary == "把本次的配置方案梳理下，输出..."


def test_resume_summary_prefers_thread_name_over_messages():
    """Codex thread title should match desktop app summary when available."""
    summary = build_resume_session_summary(
        thread_name="调整 resume 菜单优先使用最后用户消息",
        first_message="继续这个项目",
        last_user_message="修复 /resume 的摘要显示逻辑",
        previous_user_message="上一条任务",
        max_len=20,
    )

    assert summary == "调整 resume 菜单优先使用最..."


def test_resume_button_label_uses_summary_instead_of_low_signal_tail():
    """Button labels should surface the topic summary for resume choices."""
    candidate = _Candidate(
        thread_name="调整 resume 菜单优先使用最后用户消息",
        first_message="把本次的配置方案梳理下，输出 checklist",
        last_user_message="我说不用了",
        previous_user_message="修复 /resume 菜单的摘要显示",
    )

    label = _build_resume_session_button_label(candidate)

    assert "我说不用了" not in label
    assert "调整 resume 菜单" in label
    assert "019cc220" not in label


def test_resume_button_labels_hide_session_suffix_when_choices_are_distinct():
    """Distinct choices should not show session id suffixes by default."""
    candidates = [
        _Candidate(
            session_id="019cc220-abcd",
            first_message="修复 tg 摘要显示",
            last_user_message="继续处理这个问题",
        ),
        _Candidate(
            session_id="019cc221-efgh",
            first_message="补充 resume 单测",
            last_user_message="把重复场景也覆盖掉",
            file_mtime=datetime.utcnow() - timedelta(hours=3),
        ),
    ]

    labels = build_resume_button_labels(
        candidates, preview_max_len=20, max_label_len=60
    )

    assert all("abcd" not in label for label in labels)
    assert all("efgh" not in label for label in labels)


def test_resume_button_labels_append_session_suffix_for_duplicate_choices():
    """Duplicate summaries should include a short id suffix for disambiguation."""
    same_time = datetime.utcnow() - timedelta(days=1)
    candidates = [
        _Candidate(
            session_id="session-1c1a",
            first_message="继续处理",
            last_user_message="你好",
            file_mtime=same_time,
        ),
        _Candidate(
            session_id="session-2c2b",
            first_message="继续处理",
            last_user_message="你好",
            file_mtime=same_time,
        ),
    ]

    labels = build_resume_button_labels(
        candidates, preview_max_len=20, max_label_len=60
    )

    assert any(label.endswith("1c1a") for label in labels)
    assert any(label.endswith("2c2b") for label in labels)
