"""Tests for /resume UI rendering."""

from pathlib import Path

from src.bot.resume_tokens import ResumeTokenManager
from src.bot.utils.resume_ui import build_resume_project_selector


def _button_texts(reply_markup) -> list[str]:
    """Collect button labels from an inline keyboard."""
    labels = []
    for row in reply_markup.inline_keyboard:
        for button in row:
            labels.append(button.text)
    return labels


def test_resume_selector_hides_root_when_other_projects_exist(tmp_path):
    """Approved root should be hidden when there are other project choices."""
    approved = tmp_path / "approved"
    approved.mkdir(parents=True)
    project = approved / "work" / "cc-telegram"
    project.mkdir(parents=True)

    token_mgr = ResumeTokenManager()
    text, keyboard = build_resume_project_selector(
        projects=[approved, project],
        approved_root=approved,
        token_mgr=token_mgr,
        user_id=1,
        current_directory=project,
        show_all=False,
    )

    labels = _button_texts(keyboard)
    assert all("Approved Root" not in label for label in labels)
    assert "work/" in text


def test_resume_selector_uses_compact_name_parent_label(tmp_path):
    """Project buttons should use compact 'name · parent' style."""
    approved = tmp_path / "approved"
    approved.mkdir(parents=True)
    project = approved / "PycharmProjects" / "cc-telegram"
    project.mkdir(parents=True)

    token_mgr = ResumeTokenManager()
    _, keyboard = build_resume_project_selector(
        projects=[project],
        approved_root=approved,
        token_mgr=token_mgr,
        user_id=1,
        current_directory=None,
        show_all=False,
    )

    labels = _button_texts(keyboard)
    assert any("cc-telegram · PycharmProjects" in label for label in labels)


def test_resume_selector_labels_projects_under_secondary_root(tmp_path):
    """Project labels should be relative to the matching approved root."""
    primary = tmp_path / "primary"
    secondary = tmp_path / "secondary"
    primary.mkdir()
    project = secondary / "PycharmProjects" / "cc-telegram"
    project.mkdir(parents=True)

    token_mgr = ResumeTokenManager()
    text, keyboard = build_resume_project_selector(
        projects=[project],
        approved_root=primary,
        approved_roots=(primary, secondary),
        token_mgr=token_mgr,
        user_id=1,
        current_directory=None,
        show_all=False,
    )

    labels = _button_texts(keyboard)
    assert "PycharmProjects/cc\\-telegram" in text
    assert any("cc-telegram · PycharmProjects" in label for label in labels)


def test_resume_selector_has_show_all_toggle_when_projects_exceed_limit(tmp_path):
    """Selector should show a toggle button when list is truncated."""
    approved = tmp_path / "approved"
    approved.mkdir(parents=True)
    projects = []
    for i in range(8):
        p = approved / "IdeaProjects" / f"proj-{i}"
        p.mkdir(parents=True, exist_ok=True)
        projects.append(p)

    token_mgr = ResumeTokenManager()
    _, keyboard = build_resume_project_selector(
        projects=projects,
        approved_root=approved,
        token_mgr=token_mgr,
        user_id=1,
        current_directory=None,
        show_all=False,
    )

    labels = _button_texts(keyboard)
    assert any("Show All Projects" in label for label in labels)
