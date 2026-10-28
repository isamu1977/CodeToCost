"""Phase 0 / task 1.6 + 1.7: hashed checkpoints + voice/narration invalidation."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from redsun.context import (
    ProjectContext,
    EpisodeMetadata,
    ThemeMetadata,
    TTSConfig,
)
from redsun.pipeline.checkpoints import (
    CheckpointStore,
    DEPENDENCIES,
    downstream_phases,
)


def _write_script(ws, text: str = "alpha") -> None:
    ws.script_path.write_text(text, encoding="utf-8")


def _write_narration(ws, text: str = "alpha narration") -> None:
    ws.narration_path.write_text(text, encoding="utf-8")


def test_voice_change_invalidates_generate_audio(make_context):
    ctx = make_context(
        tts=TTSConfig(voice="af_heart"),
    )
    _write_script(ctx.workspace)
    _write_narration(ctx.workspace)

    store = CheckpointStore(ctx.workspace)
    store.write(
        "generate_audio",
        config_hash=ctx.config_hash("tts"),
        input_hash=ctx.hash_text(ctx.workspace.narration_path.read_text()),
        output_hash="deadbe",
    )
    assert store.is_valid(
        "generate_audio",
        current_input_hash=ctx.hash_text(
            ctx.workspace.narration_path.read_text()
        ),
        current_config_hash=ctx.config_hash("tts"),
    )

    # Same workspace, different voice: must invalidate.
    ctx2 = ProjectContext(
        project_id=None,
        workspace=ctx.workspace,
        episode=EpisodeMetadata(slug="kappa", title="K"),
        theme_metadata=ThemeMetadata(),
        tts=TTSConfig(voice="am_charles"),
    )
    assert not store.is_valid(
        "generate_audio",
        current_input_hash=ctx2.hash_text(
            ctx2.workspace.narration_path.read_text()
        ),
        current_config_hash=ctx2.config_hash("tts"),
    )


def test_narration_edit_invalidates_audio(make_context):
    ctx = make_context()
    _write_script(ctx.workspace)
    _write_narration(ctx.workspace, "v1 text")
    store = CheckpointStore(ctx.workspace)
    store.write(
        "generate_audio",
        config_hash=ctx.config_hash("tts"),
        input_hash=ctx.hash_text("v1 text"),
        output_hash="deadbe",
    )
    # Edit narration.
    _write_narration(ctx.workspace, "v2 text completely different")
    assert not store.is_valid(
        "generate_audio",
        current_input_hash=ctx.hash_text(
            ctx.workspace.narration_path.read_text()
        ),
        current_config_hash=ctx.config_hash("tts"),
    )


def test_downstream_phases_cascade():
    # Changing generate_audio invalidates everything downstream of it.
    assert "transcribe" in downstream_phases("generate_audio")
    assert "render_main" in downstream_phases("generate_audio")
    assert "concat_final" in downstream_phases("generate_audio")
    # `generate_blog` depends on `prepare_narration` (NOT on
    # `generate_audio`), so it is NOT invalidated by changes to
    # `generate_audio`. Changing the script WILL invalidate it
    # (via prepare_narration -> generate_script).
    assert "generate_blog" not in downstream_phases("generate_audio")
    assert "generate_blog" in downstream_phases("generate_script")
    # `create_project` is the root of the DAG, but invalidating it
    # must cascade to every other phase.
    affected_by_create = downstream_phases("create_project")
    for phase in DEPENDENCIES:
        if phase == "create_project":
            continue
        assert phase in affected_by_create, (
            f"phase {phase!r} should cascade from create_project"
        )


def test_invalidate_marks_only_correctdownstream(make_context):
    ctx = make_context()
    _write_script(ctx.workspace)
    _write_narration(ctx.workspace)
    store = CheckpointStore(ctx.workspace)
    store.write(
        "create_project",
        config_hash="x",
        input_hash="x",
        output_hash="x",
    )
    store.write(
        "generate_script",
        config_hash="x",
        input_hash="x",
        output_hash="x",
    )
    store.write(
        "review_script",
        config_hash="x",
        input_hash="x",
        output_hash="x",
    )
    affected = store.invalidate_downstream("generate_script")
    assert "review_script" in affected
    assert "prepare_narration" in affected
    assert "generate_audio" in affected
    assert "create_project" not in affected


def test_legacy_done_migrated_once(make_context, tmp_path):
    """A `.done` file from `motor-python/storage/<slug>/checkpoints/`
    is migrated into the new layout. Subsequent runs do not re-do it.
    """
    import json as _json

    legacy_dir = tmp_path / "storage" / "kappa" / "checkpoints"
    legacy_dir.mkdir(parents=True)
    legacy_dir.joinpath("generate_audio.done").write_text(
        _json.dumps({"duration_sec": 100.0}),
        encoding="utf-8",
    )

    ctx = make_context()
    store = CheckpointStore(ctx.workspace)
    migrated = store.migrate_legacy(99)
    assert "generate_audio" in migrated
    ckpt = ctx.workspace.checkpoint_path("generate_audio")
    assert ckpt.exists()
    raw = _json.loads(ckpt.read_text(encoding="utf-8"))
    assert raw["schema_version"] == 1
    assert raw["validation"]["legacy_unverified"] is True
    # Calling again must NOT overwrite (already exists).
    migrated2 = store.migrate_legacy(99)
    assert "generate_audio" not in migrated2


def test_corrupt_checkpoint_raises(make_context):
    ctx = make_context()
    _write_script(ctx.workspace)
    store = CheckpointStore(ctx.workspace)
    path = ctx.workspace.checkpoint_path("generate_audio")
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(Exception):
        store.read("generate_audio")


def test_dependency_graph_completeness():
    """Every phase in the standard pipeline has a dependency entry."""
    expected = {
        "create_project",
        "generate_script",
        "review_script",
        "prepare_narration",
        "generate_audio",
        "transcribe",
        "create_visual_plan",
        "generate_images",
        "render_manifest",
        "render_main",
        "concat_final",
        "generate_blog",
        "generate_shorts",
        "generate_post_kit",
        "organize_output",
    }
    assert set(DEPENDENCIES.keys()) == expected


def test_resume_skips_valid_phases(make_context):
    """When the checkpoint matches, is_valid returns True (== skip phase)."""
    ctx = make_context()
    _write_script(ctx.workspace)
    _write_narration(ctx.workspace)
    store = CheckpointStore(ctx.workspace)
    input_hash = ctx.hash_text(ctx.workspace.narration_path.read_text())
    config_hash = ctx.config_hash("tts")
    store.write(
        "generate_audio",
        config_hash=config_hash,
        input_hash=input_hash,
        output_hash="deadbe",
    )
    assert store.is_valid(
        "generate_audio",
        current_input_hash=input_hash,
        current_config_hash=config_hash,
    )


def test_resume_reruns_invalidated_phases(make_context):
    ctx = make_context()
    _write_script(ctx.workspace)
    _write_narration(ctx.workspace)
    store = CheckpointStore(ctx.workspace)
    store.write(
        "generate_audio",
        config_hash="x",
        input_hash="x",
        output_hash="x",
        status="invalidated",
    )
    assert not store.is_valid(
        "generate_audio",
        current_input_hash="x",
        current_config_hash="x",
    )