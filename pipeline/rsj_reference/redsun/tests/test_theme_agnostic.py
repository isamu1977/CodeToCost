"""Phase 0 / task 1.13: theme-agnostic runtime parametrized over 4 themes."""
from __future__ import annotations

import pytest

from redsun.context import ProjectContext, EpisodeMetadata, ThemeMetadata
from redsun.workspace import ProjectWorkspace


THEMES = [
    ("kappa", "Kappa Test"),
    ("mujina", "Mujina Test"),
    ("taira-no-masakado", "Taira no Masakado"),
    ("xyz-test", "Xyz Test"),
]


@pytest.mark.parametrize("slug,title", THEMES)
def test_workspace_and_context_build_for_any_slug(make_workspace, slug, title):
    ws = make_workspace(slug)
    assert ws.technical_slug == slug
    assert ws.root.name == slug

    ctx = ProjectContext(
        project_id=1,
        workspace=ws,
        episode=EpisodeMetadata(slug=slug, title=title),
        theme_metadata=ProjectContext._load_theme_metadata(slug),
    )
    assert ctx.episode.slug == slug
    assert ctx.episode.title == title

    # Filename templates MUST derive from the slug (parametric).
    # We verify by checking that the workspace source code has no
    # hardcoded theme literal in its filename templates.
    from pathlib import Path
    import re

    forbidden_for_parametric = ("kappa", "yuki_onna", "mujina", "umibouzu")
    src = (
        Path(__file__).resolve().parent.parent / "workspace.py"
    ).read_text(encoding="utf-8")
    for f in forbidden_for_parametric:
        for pat in (rf'f?["\']{f}_', rf'_{f}\.mp4'):
            assert not re.search(pat, src), (
                f"workspace.py hardcodes {f!r} in a filename template"
            )


@pytest.mark.parametrize("slug,title", THEMES)
def test_catalog_lookup_does_not_raise_for_any_slug(slug, title):
    """Even when the catalog lookup fails for unknown themes,
    ProjectContext construction MUST NOT raise."""
    ctx = ProjectContext(
        project_id=None,
        workspace=ProjectWorkspace.from_slug(slug),
        episode=EpisodeMetadata(slug=slug, title=title),
        theme_metadata=ProjectContext._load_theme_metadata(slug),
    )
    # For uncatalogued slugs the theme metadata is empty.
    if slug not in {"kappa", "mujina", "yuki_onna", "taira_no_masakado"}:
        assert ctx.theme_metadata.is_empty()


def test_unrecognized_slug_does_not_raise():
    """The runner MUST NOT crash if the catalog is missing or empty."""
    ProjectContext._load_theme_metadata("__no_such_slug__")  # must not raise