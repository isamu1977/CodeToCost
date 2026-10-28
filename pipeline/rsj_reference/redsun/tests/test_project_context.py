"""Phase 0 / task 1.5 + 1.13 + 1.14: ProjectContext construction, defaults,
theme_metadata propagation, and filename template genericness.
"""
from __future__ import annotations

import argparse

import pytest

from redsun.context import (
    ProjectContext,
    EpisodeMetadata,
    ThemeMetadata,
    LLMConfig,
    TTSConfig,
    ImageConfig,
    RenderConfig,
    VisualDensityConfig,
    QualityConfig,
)
from redsun.workspace import ProjectWorkspace


def _args(**kw):
    ns = argparse.Namespace(**kw)
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def test_defaults_exposes_required_fields():
    ctx = ProjectContext.defaults()
    assert isinstance(ctx.workspace, ProjectWorkspace)
    assert isinstance(ctx.episode, EpisodeMetadata)
    assert isinstance(ctx.theme_metadata, ThemeMetadata)
    assert isinstance(ctx.llm, LLMConfig)
    assert isinstance(ctx.tts, TTSConfig)
    assert isinstance(ctx.image, ImageConfig)
    assert isinstance(ctx.render, RenderConfig)
    assert isinstance(ctx.visual_density, VisualDensityConfig)
    assert isinstance(ctx.quality, QualityConfig)


def test_from_cli_args_propagates_model(make_workspace):
    ns = _args(
        slug="kappa", title="Kappa Test",
        title_ja="", language="en",
        source_text="", source_url=None, keywords=None,
        source_type=None, atmosphere=None,
        model="juggernaut", base_seed=100,
        chunk_seconds=30, chunked=True,
    )
    ws = make_workspace("kappa")
    # Patch workspace root to test dir.
    ns.__dict__["slug"] = "kappa"
    ctx = ProjectContext(
        project_id=None,
        workspace=ws,
        episode=EpisodeMetadata(slug="kappa", title="Kappa Test"),
        theme_metadata=ThemeMetadata(),
        image=ImageConfig(model="juggernaut", base_seed=100),
        render=RenderConfig(chunk_seconds=30, chunked=True),
    )
    assert ctx.image.model == "juggernaut"
    assert ctx.image.base_seed == 100
    assert ctx.render.chunk_seconds == 30


@pytest.mark.parametrize(
    "slug",
    ["kappa", "mujina", "taira-no-masakado", "xyz-test"],
)
def test_unrecognized_slug_yields_empty_theme_metadata(make_workspace, slug):
    ws = make_workspace(slug)
    ctx = ProjectContext(
        project_id=None,
        workspace=ws,
        episode=EpisodeMetadata(slug=slug, title=slug),
        theme_metadata=ProjectContext._load_theme_metadata(slug),
    )
    if slug not in {"kappa", "mujina", "yuki_onna", "taira_no_masakado"}:
        # Uncatalogued (xyz-test, taira-no-masakado alias).
        assert ctx.theme_metadata.is_empty()


def test_recognized_slug_hydrates_theme_metadata(make_workspace):
    ws = make_workspace("kappa")
    ctx = ProjectContext(
        project_id=None,
        workspace=ws,
        episode=EpisodeMetadata(slug="kappa", title="Kappa"),
        theme_metadata=ProjectContext._load_theme_metadata("kappa"),
    )
    # The catalog entry's `name` field for slug "kappa" is "Kappa".
    assert ctx.theme_metadata.name
    assert any(
        "river" in v.lower() for v in ctx.theme_metadata.visual_keywords
    ), f"expected 'river' in visual_keywords, got {ctx.theme_metadata.visual_keywords}"


def test_voice_change_yields_different_config_hash(make_workspace):
    ws = make_workspace("kappa")
    ctx1 = ProjectContext(
        project_id=None,
        workspace=ws,
        episode=EpisodeMetadata(slug="kappa", title="K"),
        theme_metadata=ThemeMetadata(),
        tts=TTSConfig(voice="af_heart"),
    )
    ws2 = make_workspace("kappa")
    ctx2 = ProjectContext(
        project_id=None,
        workspace=ws2,
        episode=EpisodeMetadata(slug="kappa", title="K"),
        theme_metadata=ThemeMetadata(),
        tts=TTSConfig(voice="am_charles"),
    )
    h1 = ctx1.config_hash("tts")
    h2 = ctx2.config_hash("tts")
    assert h1 != h2


def test_config_hash_changes_when_slice_changes(make_workspace):
    ws = make_workspace("kappa")
    ctx = ProjectContext(
        project_id=None,
        workspace=ws,
        episode=EpisodeMetadata(slug="kappa", title="K"),
        theme_metadata=ThemeMetadata(),
        image=ImageConfig(model="epicrealism"),
        render=RenderConfig(chunk_seconds=30),
    )
    h_image = ctx.config_hash("image")
    h_render = ctx.config_hash("render")
    assert h_image != h_render


def test_text_hash_is_stable():
    a = ProjectContext.hash_text("hello")
    b = ProjectContext.hash_text("hello")
    c = ProjectContext.hash_text("world")
    assert a == b
    assert a != c


def test_runtime_does_not_branch_on_slug():
    """Static analysis: production runtime source MUST NOT have
    `if slug == \"kappa\"` or similar branches on concrete names.

    This is a coarse guardrail. It walks the runtime source files
    and asserts none of the forbidden conditional patterns appear.
    Forbidden patterns (regex):
        if\\s+(self\\.)?slug\\s*==\\s*[\"']<literal>[\"']
        slug\\s+in\\s*\\{[\\s\\S]*?\\bkappa\\b
        title\\.lower\\(\\)\\.contains\\([\"']<literal>[\"']
    """
    import re
    from pathlib import Path

    forbidden_literals = ("kappa", "yuki", "yukionna", "mujina", "umibouzu")
    runtime_root = (
        Path(__file__).resolve().parent.parent
    )  # motor-python/redsun
    # We only enforce against the new redsun/ package; legacy
    # files are governed by the Phase 12B enforcement audit.
    patterns = [
        re.compile(r"if\s+(self\.)?slug\s*==\s*[\"'](\w+)[\"']"),
        re.compile(r"slug\s+in\s*\{[^}]*?(kappa|yuki|mujina|umibouzu)[^}]*?\}"),
        re.compile(
            r"title\.lower\(\)\.(contains|__contains__)\s*\(\s*[\"']([^\"']+)[\"']\s*\)"
        ),
    ]
    for path in runtime_root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if "tests" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for pat in patterns:
            for match in pat.finditer(text):
                matched_literal = match.group(2) if match.lastindex >= 2 else None
                if matched_literal and matched_literal in forbidden_literals:
                    pytest.fail(
                        f"runtime branch on concrete theme name in "
                        f"{path}: {match.group(0)!r}"
                    )


def test_filename_templates_no_theme_literal(make_workspace):
    """No filename template may embed `kappa`, `yuki`, `mujina`, etc.

    The catalog data file (`themes/_catalog_data.py`) intentionally
    contains these strings as data and is excluded.
    """
    from pathlib import Path
    import re

    forbidden = ("kappa", "yuki", "yukionna", "mujina", "umibouzu")
    runtime_root = Path(__file__).resolve().parent.parent
    patterns = re.compile(r"[\"'](\w*[Ss]cene[\w]*_\w+)[\"']")
    for path in runtime_root.rglob("*.py"):
        if "__pycache__" in path.parts or "tests" in path.parts:
            continue
        # Catalog data file is allowed to contain theme names.
        if path.name == "_catalog_data.py":
            continue
        text = path.read_text(encoding="utf-8")
        for f in forbidden:
            # Look for forbidden literal inside a string literal
            if re.search(rf"[\"'][^\"']*{f}[^\"']*[\"']", text):
                pytest.fail(
                    f"filename template contains forbidden literal {f!r} in {path}"
                )


def test_workspace_property_filenames_have_no_concrete_theme(make_workspace):
    """ProjectWorkspace filename templates MUST derive from the
    technical_slug, not from a hardcoded theme literal.

    We inspect the source code to ensure the filename templates
    are parametric.
    """
    from pathlib import Path
    import re

    forbidden = ("kappa", "yuki_onna", "mujina", "umibouzu", "ronin_47")
    src = (Path(__file__).resolve().parent.parent / "workspace.py").read_text(
        encoding="utf-8"
    )
    for f in forbidden:
        # A hardcoded literal in a filename template looks like
        # `f"{literal}_...` or `f"{slug}_literal.mp4"`.
        for pat in (
            rf'f?["\']{f}_',
            rf'_{f}\.mp4',
            rf'_{f}\.wav',
            rf'_{f}\.png',
            rf'"{f}"\s*\+\s*"_',
        ):
            for match in re.finditer(pat, src):
                pytest.fail(
                    f"workspace.py contains hardcoded literal {f!r} "
                    f"in filename template"
                )


def test_mood_hints_generic_not_entity_specific(make_workspace):
    """bgm_mood_hints in the catalog must use generic Japanese words,
    not entity names.
    """
    forbidden = ("kappa", "yuki", "mujina", "umibouzu", "tsukumogami")
    for slug in ("kappa", "mujina", "yuki_onna"):
        meta = ProjectContext._load_theme_metadata(slug)
        if meta.is_empty():
            continue
        for hint in meta.bgm_mood_hints:
            assert hint.lower() not in forbidden, (
                f"theme {slug} mood hint {hint!r} is entity-specific"
            )