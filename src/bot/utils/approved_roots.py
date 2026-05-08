"""Helpers for working with one or more approved project roots."""

from pathlib import Path
from typing import Any, Iterable


def get_approved_roots(settings: Any) -> tuple[Path, ...]:
    """Return approved roots from settings, preserving legacy settings shape."""
    roots = getattr(settings, "approved_roots", None)
    if roots:
        return tuple(Path(root).resolve() for root in roots)

    approved_directory = Path(settings.approved_directory).resolve()
    extra = getattr(settings, "approved_directories", None) or []

    deduped: list[Path] = []
    seen: set[str] = set()
    for root in [approved_directory, *extra]:
        resolved = Path(root).resolve()
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(resolved)
    return tuple(deduped)


def normalize_approved_roots(roots: Path | Iterable[Path]) -> tuple[Path, ...]:
    """Normalize a single root or iterable of roots."""
    if isinstance(roots, Path):
        raw_roots = [roots]
    else:
        raw_roots = list(roots)

    deduped: list[Path] = []
    seen: set[str] = set()
    for root in raw_roots:
        resolved = Path(root).resolve()
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(resolved)
    return tuple(deduped)


def is_path_under_roots(path: Path, roots: Iterable[Path]) -> bool:
    """Return True when path is inside any approved root."""
    resolved = path.resolve()
    for root in roots:
        try:
            resolved.relative_to(Path(root).resolve())
            return True
        except ValueError:
            continue
    return False


def relative_path_for_roots(path: Path, roots: Iterable[Path]) -> str:
    """Return a stable display path relative to the first matching root."""
    resolved = path.resolve()
    normalized_roots = normalize_approved_roots(tuple(roots))
    for root in normalized_roots:
        try:
            rel = resolved.relative_to(root)
            rel_text = str(rel)
            return "." if rel_text in ("", ".") else rel_text
        except ValueError:
            continue
    return resolved.name
