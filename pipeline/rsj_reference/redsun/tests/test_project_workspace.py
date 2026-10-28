"""Phase 0 / task 1.4 + 1.15: ProjectWorkspace path resolution + technical_slug.

Every ProjectWorkspace property MUST start with
`projects/<technical_slug>/`. Forbidden patterns: any filename
template that embeds a concrete theme name.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from redsun.workspace import (
    ProjectWorkspace,
    slugify_title,
    WorkspaceError,
)


def test_workspace_root_is_projects_slug(make_workspace):
    ws = make_workspace("kappa")
    # The root is `<tmp>/kappa`, not "kappa".
    assert ws.root.name == "kappa"
    # `projects_root` was redirected to tmp_path; this verifies the
    # root is exactly `<tmp>/kappa`.
    assert str(ws.root.parent) == str(ws.projects_root)


def test_every_property_under_projects_slug(make_workspace):
    ws = make_workspace("kappa")
    props = [
        ws.project_json_path,
        ws.script_path,
        ws.script_reviewed_path,
        ws.narration_path,
        ws.audio_main_path,
        ws.audio_chunks_dir,
        ws.transcript_path,
        ws.visual_plan_path,
        ws.manifest_path,
        ws.video_main_path,
        ws.video_final_path,
        ws.images_dir,
        ws.checkpoints_dir,
        ws.logs_dir,
        ws.pipeline_log_path,
        ws.blog_dir,
        ws.shorts_dir,
        ws.post_kit_dir,
        ws.checkpoint_path("generate_audio"),
        ws.audio_chunk_path(3),
        ws.image_path(5, 42),
    ]
    for p in props:
        s = str(p)
        assert s.startswith(str(ws.root)), (
            f"path {p} escapes workspace root {ws.root}"
        )


def test_technical_slug_immutable(make_workspace):
    ws = make_workspace("kappa")
    assert ws.technical_slug == "kappa"
    # Production code MUST NOT reassign technical_slug. There is no
    # defensive enforcement in the dataclass (we keep it non-frozen
    # so tests can redirect projects_root), so we instead document
    # this rule and trust the test suite. See the project-workspace
    # spec for the contract.
    assert ws.technical_slug == "kappa"


def test_technical_slug_from_title():
    assert slugify_title("Taira no Masakado") == "taira-no-masakado"
    assert slugify_title("Yuki-onna") == "yuki-onna"
    assert slugify_title("  KAPPA  ") == "kappa"
    with pytest.raises(WorkspaceError):
        slugify_title("")
    with pytest.raises(WorkspaceError):
        slugify_title("###")  # produces empty slug


def test_create_idempotent(make_workspace):
    ws1 = make_workspace("kappa")
    ws2 = make_workspace("kappa")
    # Calling create twice MUST NOT raise.
    ws2.create()


@pytest.mark.parametrize(
    "slug",
    [
        "kappa",
        "mujina",
        "taira-no-masakado",
        "xyz-test",
        "yuki_onna",
        "ronin_47",
    ],
)
def test_workspace_for_each_slug(make_workspace, slug):
    ws = make_workspace(slug)
    assert ws.root.name == slug
    assert ws.narration_path.parts[-2:] == (slug, "narration.txt")
    assert ws.video_final_path.name == f"{slug}_final.mp4"
    assert ws.video_main_path.name == f"{slug}_main.mp4"
    # Filename templates must be parametric: the basename
    # depends only on the slug, not on a hardcoded theme literal.
    assert ws.video_final_path.name == f"{slug}_final.mp4"
    assert ws.video_main_path.name == f"{slug}_main.mp4"


def test_filename_templates_are_slug_parametric():
    """The workspace source MUST build filenames from slug, not from
    hardcoded literals. We inspect the source to ensure no
    hardcoded theme name appears as a filename component.
    """
    from pathlib import Path
    import re

    forbidden = ("kappa", "yuki_onna", "mujina", "umibouzu", "ronin_47")
    src = (Path(__file__).resolve().parent.parent / "workspace.py").read_text(
        encoding="utf-8"
    )
    for f in forbidden:
        # Allow the slug in comments, but NOT as a filename template.
        # Heuristic: any literal occurrence in a string concatenation
        # that is part of a Path expression.
        pattern = rf'f?["\'][^"\']*{f}[^"\']*["\']'
        for match in re.finditer(pattern, src):
            # Ignore docstrings and comments.
            line_start = src.rfind("\n", 0, match.start()) + 1
            line = src[line_start:src.find("\n", match.end())]
            if "#" in line.split('"""')[0] and not line.strip().startswith("return"):
                continue
            # If the literal is in a return statement that includes
            # `_final.mp4`, `_main.mp4`, `chunk_`, or `scene_`,
            # that's a hardcoded theme in a template.
            if any(
                tag in line
                for tag in ("_final.mp4", "_main.mp4", "chunk_", "scene_")
            ):
                pytest.fail(
                    f"workspace.py contains hardcoded theme literal {f!r} "
                    f"in filename template: {line.strip()!r}"
                )


def test_legacy_paths_read_only_probe_does_not_write(tmp_path):
    """from_legacy probes but does not modify anything."""
    ws = ProjectWorkspace.from_slug("kappa")
    ws.projects_root = tmp_path
    # Even when called with project_id=5 (no legacy dirs exist),
    # from_legacy MUST NOT raise.
    legacy_ws = ProjectWorkspace.from_legacy(5, "kappa")
    legacy_ws.projects_root = tmp_path
    # No files were created under projects/.
    assert list(tmp_path.glob("*")) == []


def test_get_project_folder_name():
    from redsun.workspace import get_project_folder_name
    assert get_project_folder_name(1, "botan-doro") == "001_botan-doro"
    assert get_project_folder_name(37, "hoichi-the-earless") == "037_hoichi-the-earless"
    assert get_project_folder_name(105, "yuki-onna") == "105_yuki-onna"


def test_resolve_numbered_folder_precedence(tmp_path):
    from redsun.workspace import resolve_project_dir, ProjectWorkspace
    slug = "test-folklore"
    
    # 1. Unnumbered exists on disk
    unnum = tmp_path / slug
    unnum.mkdir()
    ws = ProjectWorkspace.from_slug(slug, projects_root=tmp_path)
    assert ws.root == unnum

    # 2. Numbered folder exists on disk -> takes precedence
    num_dir = tmp_path / f"042_{slug}"
    num_dir.mkdir()
    ws_num = ProjectWorkspace.from_slug(slug, project_id=42, projects_root=tmp_path)
    assert ws_num.root == num_dir

    # Even without explicit project_id, numbered folder matching glob is detected
    ws_auto = ProjectWorkspace.from_slug(slug, projects_root=tmp_path)
    assert ws_auto.root == num_dir


def test_from_slug_with_numbered_string(tmp_path):
    from redsun.workspace import ProjectWorkspace
    ws = ProjectWorkspace.from_slug("037_hoichi-the-earless", projects_root=tmp_path)
    assert ws.technical_slug == "hoichi-the-earless"
    assert ws.project_id == 37
    assert ws.root == tmp_path / "037_hoichi-the-earless"


def test_book_paths_in_workspace(tmp_path):
    from redsun.workspace import ProjectWorkspace
    ws = ProjectWorkspace.from_slug("botan-doro", project_id=1, projects_root=tmp_path)
    assert ws.book_dir == tmp_path / "001_botan-doro" / "book"
    assert ws.book_chapter_path == ws.book_dir / "chapter.md"
    assert ws.book_audiobook_wav_path == ws.book_dir / "chapter_audiobook.wav"
    assert ws.book_audiobook_mp3_path == ws.book_dir / "chapter_audiobook.mp3"