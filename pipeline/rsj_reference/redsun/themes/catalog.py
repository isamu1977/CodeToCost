"""Theme catalog — convenience presets only.

This module is loaded optionally by ProjectContext._load_theme_metadata.
If `catalog.py` is missing, broken, or empty, the lookup returns
nothing and the pipeline runs with empty ThemeMetadata.

Theme-specific keywords MAY appear here as data. Runtime code MUST
NOT branch on concrete theme names; it MUST read from
ProjectContext.theme_metadata instead.
"""
from __future__ import annotations

from typing import Optional


_THEMES: list = []


def _init() -> None:
    """Lazy-load the theme registry. Swallows import errors so that
    a missing or broken catalog never crashes the pipeline.
    """
    global _THEMES
    if _THEMES:
        return
    try:
        from . import _catalog_data
        _THEMES = list(_catalog_data.THEMES)
    except Exception:
        _THEMES = []


def find(slug: str) -> Optional[dict]:
    """Return the catalog entry for slug, or None if not present.

    The catalog MUST be optional. Returning None for missing
    slugs is the expected path.
    """
    _init()
    if not slug:
        return None
    for entry in _THEMES:
        if entry.get("slug") == slug:
            return entry
    return None


def all_entries() -> list:
    """Return every theme entry. Used by the operator's discovery UI."""
    _init()
    return list(_THEMES)


def theme_count() -> int:
    """Number of cataloged themes (0 if catalog missing)."""
    _init()
    return len(_THEMES)


def is_available() -> bool:
    """True if the catalog module loaded successfully."""
    try:
        from . import _catalog_data  # noqa: F401

        return True
    except Exception:
        return False