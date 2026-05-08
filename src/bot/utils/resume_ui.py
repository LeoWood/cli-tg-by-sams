"""UI helpers for /resume project selection."""

from pathlib import Path
from typing import Any, Iterable, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .approved_roots import normalize_approved_roots, relative_path_for_roots

RECENT_PROJECT_LIMIT = 5


def _escape_markdown(text: str) -> str:
    """Escape Telegram Markdown special characters."""
    special_chars = [
        "_",
        "*",
        "[",
        "]",
        "(",
        ")",
        "~",
        "`",
        ">",
        "#",
        "+",
        "-",
        "=",
        "|",
        "{",
        "}",
        ".",
        "!",
    ]
    for ch in special_chars:
        text = text.replace(ch, f"\\{ch}")
    return text


def _relative_path_text(
    project: Path,
    approved_root: Path,
    approved_roots: Optional[tuple[Path, ...]] = None,
) -> str:
    """Return project path relative to approved root."""
    roots = approved_roots or (approved_root,)
    return relative_path_for_roots(project, roots)


def _button_label(
    project: Path,
    approved_root: Path,
    approved_roots: Optional[tuple[Path, ...]] = None,
) -> str:
    """Build compact button label: 'name · parent'."""
    rel = _relative_path_text(project, approved_root, approved_roots)
    if rel == ".":
        return "📁 Approved Root"

    rel_path = Path(rel)
    name = rel_path.name
    parent = rel_path.parent.name if str(rel_path.parent) not in ("", ".") else ""
    if parent:
        label = f"📁 {name} · {parent}"
    else:
        label = f"📁 {name}"

    if len(label) > 44:
        label = label[:41] + "..."
    return label


def build_resume_project_selector(
    *,
    projects: Iterable[Path],
    approved_root: Path,
    token_mgr: Any,
    user_id: int,
    current_directory: Optional[Path] = None,
    show_all: bool = False,
    payload_extra: Optional[dict] = None,
    engine: Optional[str] = None,
    approved_roots: Optional[Iterable[Path]] = None,
) -> tuple[str, InlineKeyboardMarkup]:
    """Build message text and keyboard for /resume project selection."""
    project_list = [p.resolve() for p in projects]
    root_tuple = normalize_approved_roots(
        tuple(approved_roots) if approved_roots is not None else (approved_root,)
    )

    current_resolved = None
    if current_directory is not None:
        try:
            current_resolved = Path(current_directory).resolve()
        except (OSError, TypeError):
            current_resolved = None

    if current_resolved:
        project_list = sorted(
            project_list,
            key=lambda p: (0 if p == current_resolved else 1),
        )

    # Hide approved root when there are other meaningful project options.
    non_root = [
        p
        for p in project_list
        if _relative_path_text(p, approved_root, root_tuple) != "."
    ]
    display_pool = non_root if non_root else project_list

    if show_all:
        visible_projects = display_pool
    else:
        visible_projects = display_pool[:RECENT_PROJECT_LIMIT]

    keyboard = []
    for proj in visible_projects:
        label = _button_label(proj, approved_root, root_tuple)
        if current_resolved and proj == current_resolved:
            label = f"✅ {label}"
        token = token_mgr.issue(
            kind="p",
            user_id=user_id,
            payload={
                "cwd": str(proj),
                **(payload_extra or {}),
            },
        )
        keyboard.append(
            [InlineKeyboardButton(label, callback_data=f"resume:p:{token}")]
        )

    total = len(display_pool)
    if total > RECENT_PROJECT_LIMIT:
        if show_all:
            keyboard.append(
                [
                    InlineKeyboardButton(
                        "⬅️ Show Recent Only",
                        callback_data=(
                            f"resume:show_recent:{engine}"
                            if engine
                            else "resume:show_recent"
                        ),
                    )
                ]
            )
        else:
            keyboard.append(
                [
                    InlineKeyboardButton(
                        f"📚 Show All Projects ({total})",
                        callback_data=(
                            f"resume:show_all:{engine}" if engine else "resume:show_all"
                        ),
                    )
                ]
            )

    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="resume:cancel")])

    mode_text = (
        f"Showing all `{total}` projects."
        if show_all
        else f"Showing recent `{len(visible_projects)}` of `{total}` projects."
    )

    preview_lines = []
    for proj in visible_projects:
        rel_text = _relative_path_text(proj, approved_root, root_tuple)
        if rel_text == ".":
            rel_text = "(approved root)"
        line_prefix = "•"
        if current_resolved and proj == current_resolved:
            line_prefix = "• ✅"
        preview_lines.append(f"{line_prefix} `{_escape_markdown(rel_text)}`")

    body_lines = [
        "**Resume Desktop Session**",
        "",
        "Select a project to browse its sessions.",
        mode_text,
        "",
        *preview_lines,
        "",
        "Tap a project button:",
    ]
    if not show_all and total > RECENT_PROJECT_LIMIT:
        body_lines.insert(
            4,
            "Tip: tap *Show All Projects* to browse the full list.",
        )

    return "\n".join(body_lines), InlineKeyboardMarkup(keyboard)
