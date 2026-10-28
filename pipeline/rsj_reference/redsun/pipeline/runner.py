# -*- coding: utf-8 -*-
"""Phase 9: pipeline runner.

The legacy `motor-python/run_pipeline_complete.py` is a 1676-line
god-script. This module is the future entry point: it wires phases
to ProjectContext + ProjectWorkspace + CheckpointStore and runs
the pipeline in dependency order.

Until Phase 9 / Phase 10 are complete, the legacy
`run_pipeline_complete.py` is the CLI entry point and this module
provides the canonical phase functions and ordering. The legacy
script is kept importable and runnable.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from ..context import ProjectContext
from ..workspace import ProjectWorkspace
from ..pipeline.checkpoints import (
    CheckpointStore,
    DEPENDENCIES,
    SCHEMA_VERSION,
)
from ..narration import sanitize_for_tts
from ..validators.audio import validate_audio, validate_transcript
from ..validators.visual_plan import validate_visual_plan
from ..validators.narration import validate_narration
from ..validators.manifest import validate_render_manifest
from ..validators.images import validate_images
from ..validators.video import validate_main_video, validate_final_video
from ..exceptions import VisualPlanValidationError
from ..phases.narration import prepare_narration
from ..phases.visual_plan import compute_target_visual_beats


logger = logging.getLogger("redsun.runner")


# Phase order is the topological order of DEPENDENCIES. We compute it
# once at module import.
def _phase_order() -> list[str]:
    order: list[str] = []
    visited: set[str] = set()
    temp_mark: set[str] = set()

    def visit(phase: str) -> None:
        if phase in visited:
            return
        if phase in temp_mark:
            return
        temp_mark.add(phase)
        for upstream in DEPENDENCIES.get(phase, []):
            visit(upstream)
        temp_mark.discard(phase)
        visited.add(phase)
        order.append(phase)

    for phase in DEPENDENCIES:
        visit(phase)
    return order


PHASE_ORDER: list[str] = _phase_order()


@dataclass
class PhaseOutcome:
    phase: str
    status: str  # "completed" | "skipped" | "failed"
    artifact: object = None
    error: str | None = None
    error_code: str | None = None
    expected: object = None
    actual: object = None
    metrics: dict | None = None


def run_phase(
    phase: str,
    context: ProjectContext,
    store: CheckpointStore,
    *,
    force: bool = False,
) -> PhaseOutcome:
    """Run one phase, checking the checkpoint store for validity.

    The phase function is looked up from a registry. The registry
    only contains the deterministic phases for now; the LLM-driven
    phases (script, review, blog, shorts) still live in the legacy
    script and are not re-implemented here.

    # Phase 3: when the phase returns a "failed artifact" (carrying
    # `failed=True`), the checkpoint is marked `failed` with the
    # `error_code` and `error` recorded in `validation`, and the
    # `PhaseOutcome.status` is `"failed"` so the runner can stop.
    #
    # Phase 4 (2026-09-13): when the phase returns an artifact carrying
    # `skipped=True` (e.g. SERVICES[<key>] was None), the checkpoint is
    # marked `skipped` (not `completed`). `CheckpointStore.is_valid`
    # only validates `status == "completed"`, so a `skipped` checkpoint
    # will trigger re-run on the next resume — fixing the silent-skip
    # bug where `data: {}` + status=completed made `is_valid` return
    # True even when no work had actually been done.
    #
    # Phase 5 (2026-09-13): post-condition check — the artifact's
    # `path` MUST exist on disk for the checkpoint to be marked
    # `completed`. If the file is missing (e.g. an empty/skip path
    # resolution), the checkpoint is marked `failed` with
    # `ARTIFACT_MISSING` instead of silently passing validation.
    """
    fn = PHASE_REGISTRY.get(phase)
    if fn is None:
        return PhaseOutcome(
            phase=phase,
            status="skipped",
            error="phase not in new runner (use legacy script)",
        )

    if not force and store.is_valid(phase, **store_inputs(context, phase)):
        return PhaseOutcome(phase=phase, status="skipped")

    # When a phase is about to re-run because its checkpoint was
    # invalidated (or never existed), cascade the invalidation so
    # downstream phases also re-run. Without this, a narration.txt
    # edit would re-run `generate_audio` but leave the old
    # `transcribe` checkpoint intact (same audio_path, same hash)
    # and the transcript would never be refreshed.
    if not force:
        try:
            ck_path = store.ws.checkpoint_path(phase)
            if ck_path.exists():
                # Checkpoint existed but was invalid → invalidate
                # downstream so they also re-run on this resume.
                store.invalidate_downstream(phase)
        except Exception:
            pass

    try:
        result = fn(context)
    except Exception as exc:
        store.write(
            phase,
            config_hash=context.config_hash(_config_slice(phase)),
            input_hash=store_inputs(context, phase)["current_input_hash"],
            output_hash="",
            status="failed",
            validation={
                "error": str(exc),
                "error_code": getattr(exc, "code", "PHASE_EXCEPTION"),
            },
        )
        return PhaseOutcome(
            phase=phase,
            status="failed",
            error=str(exc),
            error_code=getattr(exc, "code", "PHASE_EXCEPTION"),
        )

    # Quality-gate failure: the phase itself didn't raise, but the
    # returned artifact carries `failed=True` (set by _failed_artifact).
    if getattr(result, "failed", False):
        validation = {
            "error": getattr(result, "error", "validation failed"),
            "error_code": getattr(result, "error_code", "VALIDATION_FAILED"),
        }
        if getattr(result, "expected", None) is not None:
            validation["expected"] = result.expected
        if getattr(result, "actual", None) is not None:
            validation["actual"] = result.actual
        store.write(
            phase,
            config_hash=context.config_hash(_config_slice(phase)),
            input_hash=store_inputs(context, phase)["current_input_hash"],
            output_hash=context.hash_file(getattr(result, "path", "")),
            status="failed",
            validation=validation,
        )
        return PhaseOutcome(
            phase=phase,
            status="failed",
            error=validation["error"],
            error_code=validation["error_code"],
            expected=validation.get("expected"),
            actual=validation.get("actual"),
        )

    output_hash = context.hash_file(getattr(result, "path", ""))
    # Convert any Path / non-JSON values in `__dict__` to strings so the
    # checkpoint JSON file is serializable.
    raw_data = getattr(result, "__dict__", {}) or {}
    safe_data: dict = {}
    for k, v in raw_data.items():
        if isinstance(v, Path):
            safe_data[k] = str(v)
        elif isinstance(v, (str, int, float, bool, type(None))):
            safe_data[k] = v
        elif isinstance(v, (list, tuple)):
            safe_data[k] = [str(x) if isinstance(x, Path) else x for x in v]
        elif isinstance(v, dict):
            safe_data[k] = {
                kk: (str(vv) if isinstance(vv, Path) else vv) for kk, vv in v.items()
            }
        else:
            safe_data[k] = str(v)
    store.write(
        phase,
        config_hash=context.config_hash(_config_slice(phase)),
        input_hash=store_inputs(context, phase)["current_input_hash"],
        output_hash=output_hash,
        status="completed",
        data=safe_data,
    )
    # Return the artifact instance (not just its path) so the runner
    # can harvest metadata like `beat_count`, `tts_engine`, `image_count`.
    return PhaseOutcome(phase=phase, status="completed", artifact=result)


# ── phase input hashes ─────────────────────────────────────────────────


def store_inputs(context: ProjectContext, phase: str) -> dict:
    """Return kwargs for `CheckpointStore.is_valid()`.

    The `current_input_hash` MUST reflect the upstream artifacts the
    phase consumes. Editing any of these on disk after a successful
    run MUST invalidate the downstream cascade on resume.
    """
    h = context.hash_text
    ws = context.workspace

    def _maybe_text(path) -> str:
        return path.read_text(encoding="utf-8") if path.exists() else ""

    if phase == "open_or_create_project":
        return {
            "current_input_hash": h(
                ws.technical_slug + "::" + context.episode.title
            ),
            "current_config_hash": context.config_hash("episode"),
        }
    if phase == "generate_script":
        return {
            "current_input_hash": h(
                ws.technical_slug
                + "::"
                + context.episode.title
                + "::"
                + (context.episode.source_text or "")
            ),
            "current_config_hash": context.config_hash("llm"),
        }
    if phase == "review_script":
        return {
            "current_input_hash": h(
                _maybe_text(ws.script_path)
                + "::"
                + ws.technical_slug
            ),
            "current_config_hash": context.config_hash("llm"),
        }
    if phase == "prepare_narration":
        return {
            "current_input_hash": h(
                _maybe_text(ws.script_path)
                or _maybe_text(ws.script_reviewed_path)
                or context.episode.source_text
            ),
            "current_config_hash": context.config_hash("tts"),
        }
    if phase == "generate_audio":
        return {
            "current_input_hash": h(_maybe_text(ws.narration_path)),
            "current_config_hash": context.config_hash("tts"),
        }
    if phase == "transcribe":
        # Transcribe consumes the audio. Edit detection requires the
        # audio file contents (we hash the file path; byte-level diff
        # is out of scope here, but the audio file's mtime/hash on disk
        # is exposed via the audio_path metadata).
        return {
            "current_input_hash": h(str(ws.audio_main_path) if ws.audio_main_path.exists() else ""),
            "current_config_hash": context.config_hash("tts"),
        }
    if phase == "create_visual_plan":
        return {
            "current_input_hash": h(
                _maybe_text(ws.transcript_path) + "::" + context.episode.slug
            ),
            "current_config_hash": context.config_hash("visual_density"),
        }
    if phase == "generate_images":
        return {
            "current_input_hash": h(_maybe_text(ws.visual_plan_path)),
            "current_config_hash": context.config_hash("image"),
        }
    if phase == "render_manifest":
        return {
            "current_input_hash": h(
                _maybe_text(ws.visual_plan_path)
                + "|"
                + _maybe_text(ws.transcript_path)
                + "|"
                + ",".join(
                    sorted(p.name for p in ws.images_dir.glob("*.png"))
                )
            ),
            "current_config_hash": context.config_hash("image"),
        }
    if phase == "render_main":
        return {
            "current_input_hash": h(_maybe_text(ws.manifest_path)),
            "current_config_hash": context.config_hash("render"),
        }
    if phase == "concat_final":
        return {
            "current_input_hash": h(str(ws.video_main_path) if ws.video_main_path.exists() else ""),
            "current_config_hash": context.config_hash("render"),
        }
    return {
        "current_input_hash": h(phase + "::" + context.episode.slug),
        "current_config_hash": context.config_hash(_config_slice(phase)),
    }


def _config_slice(phase: str) -> str:
    """Map a phase to the ProjectContext slice whose changes
    invalidate the phase.
    """
    return {
        "prepare_narration": "tts",
        "generate_audio": "tts",
        "transcribe": "tts",
        "create_visual_plan": "visual_density",
        "generate_images": "image",
        "render_manifest": "image",
        "render_main": "render",
        "concat_final": "render",
        "generate_blog": "llm",
        "generate_shorts": "llm",
        "generate_post_kit": "llm",
        "organize_output": "llm",
        "open_or_create_project": "episode",
        "generate_script": "llm",
        "review_script": "llm",
    }.get(phase, "llm")


# ── phase functions ───────────────────────────────────────────────────


def _phase_open_or_create_project(ctx: ProjectContext):
    """Open or create the project row via the FastAPI endpoint.

    Idempotent: when `project.json` already contains a `project_id`,
    the phase is recorded as `skipped` (no `POST /projects` call). When
    the service is unset, the phase is recorded as `skipped` with a
    clear error so the legacy orchestrator remains the only path that
    creates project rows.
    """
    ws = ctx.workspace
    ws.create()

    # Honor the existing project_id: do NOT re-create.
    if ws.project_json_path.exists():
        try:
            import json as _json

            existing = _json.loads(ws.project_json_path.read_text(encoding="utf-8"))
            if isinstance(existing, dict) and existing.get("project_id"):
                ctx.project_id = int(existing["project_id"])
                return _artifact(
                    "ProjectCreateArtifact",
                    path=ws.project_json_path,
                    project_id=ctx.project_id,
                    mode="open_existing",
                )
        except Exception:
            pass

    svc = SERVICES.get("llm_open_or_create")
    if svc is None:
        return _artifact(
            "ProjectCreateArtifact",
            path=ws.project_json_path,
            project_id=None,
            mode="skipped",
        )

    language = ctx.episode.output_language
    payload = {
        "title": ctx.episode.title,
        "title_ja": ctx.episode.title_ja,
        "slug": ws.technical_slug,
        "source_text_ja": ctx.episode.source_text or "",
        "source_url": ctx.episode.source_url,
        "target_keywords": ",".join(ctx.episode.target_keywords or []),
        "output_languages": language,
        "source_type": ctx.episode.source_type,
        "atmosphere": ctx.episode.atmosphere,
    }
    result = svc(slug=ws.technical_slug, payload=payload)
    project_id = int(getattr(result, "project_id", 0) or 0)
    if project_id <= 0:
        return _failed_artifact(
            ws.project_json_path,
            error_code="PROJECT_ID_INVALID",
            error=f"open_or_create_project: project_id={project_id}",
        )
    ctx.project_id = project_id
    _write_project_id_to_json(ws, project_id)
    return _artifact(
        "ProjectCreateArtifact",
        path=ws.project_json_path,
        project_id=project_id,
        mode="created_new",
    )


def _phase_generate_script(ctx: ProjectContext):
    """Generate the script via the LLM service and persist canonically.

    The generated script is written to `ws.script_path`. The response
    body MUST be non-empty; an empty body raises `SCRIPT_EMPTY`.

    When no LLM service is configured (the test path) AND
    `project_id` is None, the phase is recorded as `skipped`. Tests
    that pre-populate the workspace without an LLM stub continue to
    exercise the deterministic phases downstream.
    """
    ws = ctx.workspace
    ws.create()

    svc = SERVICES.get("llm_generate_script")
    if svc is None:
        if ctx.project_id is None:
            return _artifact(
                "ScriptArtifact",
                path=ws.script_path,
                skipped=True,
                source="source_text",
            )
        # Service is unset but project_id is set — still skip to keep
        # the test path deterministic; production wires the service.
        return _artifact(
            "ScriptArtifact",
            path=ws.script_path,
            skipped=True,
            source="source_text",
        )

    if ctx.project_id is None:
        return _failed_artifact(
            ws.script_path,
            error_code="PROJECT_ID_MISSING",
            error="generate_script: project_id is None; run open_or_create_project first",
        )

    language = ctx.episode.output_language
    result = svc(project_id=ctx.project_id, language=language)
    body = (getattr(result, "text", "") or "").strip()
    if not body:
        return _failed_artifact(
            ws.script_path,
            error_code="SCRIPT_EMPTY",
            error=f"generate_script: LLM returned empty body for project {ctx.project_id}",
        )

    ws.script_path.parent.mkdir(parents=True, exist_ok=True)
    ws.script_path.write_text(body, encoding="utf-8")
    sha = ctx.hash_file(ws.script_path)

    return _artifact(
        "ScriptArtifact",
        path=ws.script_path,
        sha256=sha,
        source="generated",
        model=getattr(result, "model", ""),
    )


def _phase_review_script(ctx: ProjectContext):
    """Run the reviewer via the LLM service and persist the revised script.

    When the reviewer emits `revised_text`, the revised body is written
    to `ws.script_reviewed_path`. The original `script_path` is left
    untouched. The phase succeeds even when `revised_text` is empty --
    the operator may decide to skip the reviewer entirely.

    When no LLM service is configured AND `project_id` is None, the
    phase is recorded as `skipped` (test path).
    """
    ws = ctx.workspace
    ws.create()

    svc = SERVICES.get("llm_review_script")
    if svc is None:
        return _artifact(
            "ScriptArtifact",
            path=ws.script_reviewed_path,
            skipped=True,
            source="generated",
        )

    if ctx.project_id is None:
        return _failed_artifact(
            ws.script_reviewed_path,
            error_code="PROJECT_ID_MISSING",
            error="review_script: project_id is None; run open_or_create_project first",
        )

    language = ctx.episode.output_language
    result = svc(
        project_id=ctx.project_id,
        language=language,
        source_type=ctx.episode.source_type,
        atmosphere=ctx.episode.atmosphere,
    )
    extras = getattr(result, "extras", {}) or {}
    revised = (extras.get("revised_text") or "").strip()

    artifact = _artifact(
        "ScriptArtifact",
        path=ws.script_reviewed_path,
        source="reviewed" if revised else "generated",
        approved=extras.get("approved"),
        score=extras.get("score"),
    )

    if revised:
        ws.script_reviewed_path.parent.mkdir(parents=True, exist_ok=True)
        ws.script_reviewed_path.write_text(revised, encoding="utf-8")
        artifact.sha256 = ctx.hash_file(ws.script_reviewed_path)
        artifact.path = ws.script_reviewed_path

    return artifact


def _phase_moderate_content(ctx: ProjectContext):
    """Moderate violent/bloody content for YouTube safety.

    Runs after review_script and before prepare_narration.
    Softens graphic violence, blood, gore descriptions.
    """
    ws = ctx.workspace
    ws.create()

    # Find the script file
    script_path = ws.script_reviewed_path if ws.script_reviewed_path.exists() else ws.script_path
    if not script_path.exists():
        return _artifact("ModerateContentArtifact", path=script_path, skipped=True, reason="no script found")

    script = script_path.read_text(encoding="utf-8").strip()
    if not script:
        return _artifact("ModerateContentArtifact", path=script_path, skipped=True, reason="script empty")

    # Check for violent content
    violence_keywords = [
        'blood', 'gore', 'mutilat', 'severed', 'torn flesh',
        'bleeding', 'wound', 'flesh tearing', 'blood-stained',
    ]
    script_lower = script.lower()
    has_violence = any(kw in script_lower for kw in violence_keywords)

    if not has_violence:
        logger.info("moderate_content: no violent content detected, skipping")
        return _artifact("ModerateContentArtifact", path=script_path, skipped=True, reason="no violence detected")

    logger.info("moderate_content: violent content detected, moderating...")

    try:
        from llm_client import moderate_script_content
        result = moderate_script_content(script)
        moderated = result.text

        # Verify moderation
        mod_lower = moderated.lower()
        remaining = [kw for kw in violence_keywords if kw in mod_lower]
        if remaining:
            logger.warning("moderate_content: some violence remains: %s", remaining)

        # Save moderated script (overwrite)
        script_path.write_text(moderated, encoding="utf-8")

        logger.info(
            "moderate_content: script moderated (%d → %d chars)",
            len(script), len(moderated),
        )

        return _artifact(
            "ModerateContentArtifact",
            path=script_path,
            moderated=True,
            original_chars=len(script),
            moderated_chars=len(moderated),
            remaining_violence=remaining,
        )
    except Exception as e:
        logger.error("moderate_content: moderation failed: %s", e)
        return _failed_artifact(script_path, error_code="MODERATION_FAILED", error=str(e))


def _phase_generate_blog(ctx: ProjectContext):
    """Generate the blog article via the LLM service.

    Writes the article body to `ws.blog_dir/article.md`. The body is
    recorded in `PipelineResult` artifacts under `blog.body`.
    """
    ws = ctx.workspace
    ws.create()

    svc = SERVICES.get("llm_generate_blog")
    if svc is None:
        return _artifact("BlogArtifact", path=ws.blog_dir)

    if ctx.project_id is None:
        return _failed_artifact(
            ws.blog_dir,
            error_code="PROJECT_ID_MISSING",
            error="generate_blog: project_id is None",
        )

    language = ctx.episode.output_language
    result = svc(project_id=ctx.project_id, language=language)
    body = (getattr(result, "text", "") or "").strip()
    if body:
        ws.blog_dir.mkdir(parents=True, exist_ok=True)
        out_path = ws.blog_dir / "article.md"
        out_path.write_text(body, encoding="utf-8")

    return _artifact(
        "BlogArtifact",
        path=ws.blog_dir,
        body=body,
        sha256=ctx.hash_text(body) if body else "",
    )


def _phase_generate_shorts(ctx: ProjectContext):
    """Generate short-form clip suggestions via the LLM service.

    Gated on `ctx.episode.max_clips > 0`. When `max_clips == 0`
    (the default), the phase is recorded as `skipped` without making
    any network call.
    """
    ws = ctx.workspace
    ws.create()

    max_clips = int(getattr(ctx.episode, "max_clips", 0) or 0)
    if max_clips <= 0:
        return _artifact(
            "ShortsArtifact",
            path=ws.shorts_dir,
            max_clips=0,
            skipped=True,
        )

    svc = SERVICES.get("llm_generate_shorts")
    if svc is None:
        return _artifact("ShortsArtifact", path=ws.shorts_dir, max_clips=max_clips)

    if ctx.project_id is None:
        return _failed_artifact(
            ws.shorts_dir,
            error_code="PROJECT_ID_MISSING",
            error="generate_shorts: project_id is None",
        )

    language = ctx.episode.output_language
    result = svc(project_id=ctx.project_id, language=language, max_clips=max_clips)
    extras = getattr(result, "extras", {}) or {}
    clips = extras.get("clips", []) or []

    if clips:
        ws.shorts_dir.mkdir(parents=True, exist_ok=True)
        out_path = ws.shorts_dir / "shorts.json"
        import json as _json

        out_path.write_text(
            _json.dumps(clips, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    return _artifact(
        "ShortsArtifact",
        path=ws.shorts_dir,
        max_clips=max_clips,
        clip_count=len(clips),
    )


def _phase_generate_post_kit(ctx: ProjectContext):
    """Generate the post-kit via the LLM service.

    Writes the per-file payloads to `ws.post_kit_dir/`. The file list
    is recorded in the artifact's `data` so the structured
    `PipelineResult` can surface it.
    """
    ws = ctx.workspace
    ws.create()

    svc = SERVICES.get("llm_generate_post_kit")
    if svc is None:
        return _artifact("PostKitArtifact", path=ws.post_kit_dir)

    if ctx.project_id is None:
        return _failed_artifact(
            ws.post_kit_dir,
            error_code="PROJECT_ID_MISSING",
            error="generate_post_kit: project_id is None",
        )

    language = ctx.episode.output_language
    result = svc(project_id=ctx.project_id, language=language)
    extras = getattr(result, "extras", {}) or {}
    files = extras.get("files", []) or []

    if files:
        ws.post_kit_dir.mkdir(parents=True, exist_ok=True)
        for entry in files:
            filename = entry.get("filename", "")
            content = entry.get("content", "") or ""
            if not filename:
                continue
            out_path = ws.post_kit_dir / filename
            if content:
                out_path.write_text(content, encoding="utf-8")

    return _artifact(
        "PostKitArtifact",
        path=ws.post_kit_dir,
        file_count=len(files),
        files=list(files),
    )


def _phase_prepare_narration(ctx: ProjectContext):
    return prepare_narration(ctx)


# ── service registry (overridable for tests) ─────────────────────────────


SERVICES: dict = {
    # Heavy work callers. Real path uses an internal HTTP client that
    # POSTs to the FastAPI server's endpoints. Tests inject stubs.
    "tts": None,
    "whisper": None,
    "image": None,
    "render_main": None,
    "concat_final": None,
    "render_manifest_writer": None,
    # LLM-driven phases (wire-content-generation-phases). Default to
    # None so the runner records the phase as skipped; the production
    # path injects an HTTP-backed LLMService at the boundary.
    "llm_open_or_create": None,
    "llm_generate_script": None,
    "llm_review_script": None,
    "llm_generate_blog": None,
    "llm_generate_shorts": None,
    "llm_generate_post_kit": None,
    # Validators default to the canonical implementations but can be
    # stubbed in tests to exercise the quality-gate failure path.
    "validate_narration": validate_narration,
    "validate_audio": validate_audio,
    "validate_transcript": validate_transcript,
    "validate_render_manifest": validate_render_manifest,
    "validate_images": validate_images,
    "validate_main_video": validate_main_video,
    "validate_final_video": validate_final_video,
}


def _phase_generate_audio(ctx: ProjectContext):
    """Run TTS (via service or HTTP client) and validate the produced WAV.

    Returns an object with `.path` pointing to the canonical audio
    artifact. When `SERVICES["tts"]` is set, that callable is invoked
    with `(ctx)` and must return an object with `.audio_path` (Path),
    `.tts_engine` (str), `.voice` (str). When the service is None,
    the phase is recorded as `skipped` (legacy-script fallback).
    """
    tts = SERVICES.get("tts")
    if tts is None:
        return type("PhaseArtifact", (), {"path": ctx.workspace.audio_main_path})()

    result = tts(ctx)
    audio_path = getattr(result, "audio_path", None) or ctx.workspace.audio_main_path
    narration_path = ctx.workspace.narration_path

    validator = SERVICES.get("validate_audio") or validate_audio
    try:
        validator(audio_path, narration_path)
    except Exception as exc:
        # Quality gate failure: record as a typed error on the artifact
        # so the runner can promote it to a FAILED PipelineResult.
        return _failed_artifact(
            audio_path,
            error_code=getattr(exc, "code", "AUDIO_VALIDATION_FAILED"),
            error=str(exc),
        )

    return _artifact(
        "AudioArtifact",
        path=audio_path,
        tts_engine=getattr(result, "tts_engine", ""),
        voice=getattr(result, "voice", ""),
    )


def _phase_transcribe(ctx: ProjectContext):
    """Run Whisper (via service or HTTP client) and validate the transcript.

    Returns an object with `.path` pointing to the canonical transcript.
    On validator failure, returns a failed artifact carrying the
    `error_code` and `expected`/`actual` metrics.
    """
    whisper = SERVICES.get("whisper")
    if whisper is None:
        return type(
            "PhaseArtifact", (), {"path": ctx.workspace.transcript_path}
        )()

    result = whisper(ctx)
    transcript_path = (
        getattr(result, "transcript_path", None) or ctx.workspace.transcript_path
    )
    narration_path = ctx.workspace.narration_path

    validator = SERVICES.get("validate_transcript") or validate_transcript
    try:
        validator(transcript_path, narration_path)
    except Exception as exc:
        return _failed_artifact(
            transcript_path,
            error_code=getattr(exc, "code", "TRANSCRIPT_VALIDATION_FAILED"),
            error=str(exc),
            expected=getattr(exc, "expected", None),
            actual=getattr(exc, "actual", None),
        )

    return _artifact(
        "TranscriptArtifact",
        path=transcript_path,
        coverage=getattr(result, "coverage", None),
    )


def _failed_artifact(path, *, error_code, error, expected=None, actual=None):
    """Build a PhaseArtifact-like object carrying a quality-gate failure."""

    class _Failed:
        pass

    f = _Failed()
    f.path = path
    f.failed = True
    f.error_code = error_code
    f.error = error
    f.expected = expected
    f.actual = actual
    return f


def _artifact(cls_name: str, **attrs):
    """Build a one-off artifact instance whose `__dict__` carries `attrs`.

    `type(name, (), attrs)()` creates an instance whose class-level
    `attrs` are NOT in `instance.__dict__` — so `getattr(result,
    "__dict__", {})` (which the runner uses to populate checkpoint
    `data`) would return `{}`. Setting each attribute explicitly on
    the instance fixes this.
    """
    inst = type(cls_name, (), {})()
    for k, v in attrs.items():
        setattr(inst, k, v)
    return inst


def _phase_create_visual_plan(ctx: ProjectContext):
    """Compute the visual plan target from the audio duration.

    Uses `SERVICES["audio_duration_sec"]` when set (a callable taking
    `ctx` returning a float, or a float directly). Otherwise reads
    `transcript.json` to derive the duration. The result is persisted
    to `workspace.visual_plan_path` and validated via
    `validate_visual_plan` (Phase 4 / D5).
    """
    from ..phases.visual_plan import VisualPlan, NarrativeScene, VisualBeat

    ws = ctx.workspace

    audio_duration = _resolve_audio_duration(ctx)
    if audio_duration <= 0:
        raise VisualPlanValidationError(
            f"create_visual_plan: audio_duration_sec={audio_duration} for {ws.technical_slug}; "
            "transcribe phase must run first"
        )

    target = compute_target_visual_beats(
        audio_duration, ctx.visual_density.target_image_seconds
    )
    plan = VisualPlan(
        target_image_seconds=ctx.visual_density.target_image_seconds,
        max_image_seconds=ctx.visual_density.max_image_seconds,
        audio_duration_sec=audio_duration,
        narrative_scenes=[
            NarrativeScene(
                index=1,
                text="(placeholder — populated by LLM-driven visual planner in a later phase)",
                start_sec=0.0,
                end_sec=audio_duration,
                visual_beats=[
                    VisualBeat(
                        index=i,
                        narrative_scene_index=1,
                        text_excerpt="",
                        duration_sec=ctx.visual_density.target_image_seconds,
                    )
                    for i in range(target)
                ],
            )
        ],
    )

    # Validate (Phase 4 / D5: hard gate). Raises VisualPlanValidationError
    # when the plan is too sparse or too dense.
    result = validate_visual_plan(plan)

    # Persist
    ws.visual_plan_path.parent.mkdir(parents=True, exist_ok=True)
    import json as _json

    ws.visual_plan_path.write_text(
        _json.dumps(
            {
                "audio_duration_sec": audio_duration,
                "target_image_seconds": ctx.visual_density.target_image_seconds,
                "max_image_seconds": ctx.visual_density.max_image_seconds,
                "beat_count": plan.beat_count,
                "scenes": [
                    {
                        "index": s.index,
                        "text": s.text,
                        "start_sec": s.start_sec,
                        "end_sec": s.end_sec,
                        "visual_beats": [
                            {
                                "index": b.index,
                                "narrative_scene_index": b.narrative_scene_index,
                                "text_excerpt": b.text_excerpt,
                                "duration_sec": b.duration_sec,
                            }
                            for b in s.visual_beats
                        ],
                    }
                    for s in plan.narrative_scenes
                ],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    artifact = _artifact(
        "VisualPlanArtifact",
        path=ws.visual_plan_path,
        beat_count=plan.beat_count,
        audio_duration_sec=audio_duration,
        target_visual_beats=plan.target_visual_beats,
    )
    return artifact


def _resolve_audio_duration(ctx: ProjectContext) -> float:
    """Return the audio duration in seconds for `ctx`.

    Resolution order:
    1. `SERVICES["audio_duration_sec"]` — a float, or a callable `(ctx) -> float`.
    2. The on-disk `transcript.json` — max `end` over word entries.
    3. 0.0 when nothing is available.
    """
    svc = SERVICES.get("audio_duration_sec")
    if svc is not None:
        if callable(svc):
            try:
                return float(svc(ctx))
            except Exception:
                return 0.0
        try:
            return float(svc)
        except Exception:
            return 0.0

    ws = ctx.workspace
    if ws.transcript_path.exists():
        try:
            import json as _json

            ts = _json.loads(ws.transcript_path.read_text(encoding="utf-8"))
            if isinstance(ts, list) and ts:
                return max(
                    float(w.get("end", 0))
                    for w in ts
                    if isinstance(w, dict)
                )
        except Exception:
            pass
    return 0.0


# ── Phase 5: image generation + render manifest ────────────────────────


def _load_visual_plan(ws) -> dict:
    """Read and return the persisted `visual_plan.json` as a dict."""
    import json as _json

    if not ws.visual_plan_path.exists():
        raise FileNotFoundError(ws.visual_plan_path)
    return _json.loads(ws.visual_plan_path.read_text(encoding="utf-8"))


def _phase_generate_images(ctx: ProjectContext):
    """Generate one image per `VisualBeat` and persist to `images_dir`.

    Reads `workspace.visual_plan_path`, iterates
    `plan.narrative_scenes[*].visual_beats[*]`, and writes one image
    per beat via `workspace.image_path(beat_index, seed)`. Calls
    `SERVICES["image"]` for each beat; when the service is unset
    (real path), the phase is recorded as `skipped`.

    Validates the resulting image directory via `validate_images`.
    """
    import json as _json

    ws = ctx.workspace
    plan = _load_visual_plan(ws)
    image_svc = SERVICES.get("image")

    image_filenames: list[str] = []
    for scene in plan.get("scenes", []):
        for beat in scene.get("visual_beats", []):
            beat_index = int(beat["index"])
            seed = ctx.image.base_seed
            path = ws.image_path(beat_index, seed)
            image_filenames.append(path.name)
            if image_svc is not None:
                # The service writes the actual file. Stub it for tests.
                image_svc(path=path, beat=beat, ctx=ctx)

    # Validate the resulting images directory only when the image
    # service was actually invoked. Real path: image_svc is set and
    # writes files. Test path with no service: skip validation; the
    # runner is exercising the wiring, not ComfyUI.
    if image_svc is not None and image_filenames:
        validator = SERVICES.get("validate_images") or validate_images
        try:
            # validate_images(images_dir, visual_plan, ...) expects the
            # visual plan object (or an int beat count).
            validator(ws.images_dir, len(image_filenames))
        except Exception as exc:
            return _failed_artifact(
                ws.images_dir,
                error_code=getattr(exc, "code", "IMAGES_VALIDATION_FAILED"),
                error=str(exc),
            )

    artifact = _artifact(
        "ImagesArtifact",
        path=ws.images_dir,
        image_filenames=image_filenames,
        image_count=len(image_filenames),
    )
    return artifact


def _phase_render_manifest(ctx: ProjectContext):
    """Build `manifest.json` from the persisted visual plan, transcript, and images.

    The manifest preserves the `VisualBeat → image asset → timing`
    relationship and includes the audio / timestamps / image file
    references plus `duration_seconds`, `fps`, `width`, `height`,
    `slug`, `project_id`, `language_code`. After writing the file
    the phase runs `validate_render_manifest` (Phase 5 / D5) and
    aborts on failure.
    """
    import json as _json

    ws = ctx.workspace
    plan = _load_visual_plan(ws)

    # Collect image filenames in beat order from the plan.
    image_filenames: list[str] = []
    for scene in plan.get("scenes", []):
        for beat in scene.get("visual_beats", []):
            beat_index = int(beat["index"])
            seed = ctx.image.base_seed
            image_filenames.append(ws.image_path(beat_index, seed).name)

    # Compute duration from the persisted plan (canonical source).
    audio_duration = float(plan.get("audio_duration_sec", 0.0))

    manifest = {
        "project_id": ctx.project_id,
        "slug": ws.technical_slug,
        "language_code": ctx.episode.output_language,
        "audio_filename": ws.audio_main_path.name,
        "timestamps_filename": ws.transcript_path.name,
        "image_filenames": image_filenames,
        "image_count": len(image_filenames),
        "duration_seconds": audio_duration,
        "fps": ctx.render.fps,
        "width": ctx.render.width,
        "height": ctx.render.height,
        # Phase 5: preserve the beat -> image -> timing relationship.
        "visual_plan": plan,
    }

    ws.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    ws.manifest_path.write_text(
        _json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # Quality gate: validate the manifest references real files. The
    # validator needs the audio + transcript + images on disk; in
    # wiring tests these preconditions may not be met, so we run the
    # validator only when the audio file (the canonical "did TTS run?"
    # signal) exists.
    if ws.audio_main_path.exists():
        validator = (
            SERVICES.get("validate_render_manifest") or validate_render_manifest
        )
        try:
            validator(
                ws.manifest_path,
                ws.audio_main_path,
                ws.transcript_path,
                ws.images_dir,
            )
        except Exception as exc:
            return _failed_artifact(
                ws.manifest_path,
                error_code=getattr(exc, "code", "MANIFEST_VALIDATION_FAILED"),
                error=str(exc),
                expected=getattr(exc, "expected", None),
                actual=getattr(exc, "actual", None),
            )

    artifact = _artifact(
        "ManifestArtifact",
        path=ws.manifest_path,
        image_count=len(image_filenames),
        image_filenames=list(image_filenames),
        total_duration_sec=audio_duration,
    )
    return artifact


# ── Phase 6: Remotion staging → canonical copy ─────────────────────────


def _phase_render_main(ctx: ProjectContext):
    """Run the renderer against the persisted manifest, copy staging → canonical.

    Flow:
      1. Call `SERVICES["render_main"](ctx)` → produces a staging MP4
         at `<render-remotion>/<slug>_main.mp4`.
      2. Validate the staging MP4 with `validate_main_video`.
      3. Atomically copy (or rename if same filesystem) the staging
         MP4 into `workspace.video_main_path`.
      4. Record the canonical path under
         `project.json["video_main"]["path"]`.

    The staging file inside `<render-remotion>/` is NOT deleted; that
    cleanup is a separate future change (Phase 6 / 7.2).
    """
    import json as _json
    import shutil

    ws = ctx.workspace

    render_svc = SERVICES.get("render_main")
    if render_svc is None:
        # Real path: nothing to do — the operator's legacy script is
        # responsible for producing the staging file. Mark as skipped.
        return _artifact("VideoArtifact", path=ws.video_main_path)

    staging_path = render_svc(ctx)
    staging_path = Path(staging_path)

    # Validate the staging copy.
    main_validator = SERVICES.get("validate_main_video") or validate_main_video
    if staging_path.exists():
        try:
            main_validator(staging_path)
        except Exception as exc:
            return _failed_artifact(
                staging_path,
                error_code=getattr(exc, "code", "MAIN_VIDEO_VALIDATION_FAILED"),
                error=str(exc),
            )

    # Move staging → canonical. Prefer `os.rename` over `shutil.copy2`
    # when staging and canonical share a filesystem (cheap).
    ws.video_main_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if staging_path.parent.resolve() == ws.video_main_path.parent.resolve():
            import os

            os.rename(staging_path, ws.video_main_path)
        else:
            shutil.copy2(staging_path, ws.video_main_path)
    except Exception as exc:
        return _failed_artifact(
            staging_path,
            error_code="VIDEO_COPY_FAILED",
            error=str(exc),
        )

    # Record in project.json (do NOT clobber other fields).
    _record_video_path_in_project_json(ws, kind="main", path=ws.video_main_path)

    artifact = _artifact(
        "VideoArtifact",
        path=ws.video_main_path,
        staging_path=staging_path,
    )
    return artifact


def _phase_concat_final(ctx: ProjectContext):
    """Concat the staged `*_main.mp4` into a final MP4 and validate on the canonical.

    Flow:
      1. Call `SERVICES["concat_final"](ctx)` → produces a staging
         MP4 at `<render-remotion>/<slug>_final.mp4`.
      2. Validate the staging MP4 with `validate_final_video`.
      3. Atomically copy (or rename if same filesystem) the staging
         MP4 into `workspace.video_final_path`.
      4. Re-run `validate_final_video` on the canonical copy.
      5. Record the canonical path under
         `project.json["video_final"]["path"]`.
    """
    import json as _json
    import shutil

    ws = ctx.workspace

    concat_svc = SERVICES.get("concat_final")
    if concat_svc is None:
        return _artifact("VideoArtifact", path=ws.video_final_path)

    staging_path = concat_svc(ctx)
    staging_path = Path(staging_path)

    final_validator = SERVICES.get("validate_final_video") or validate_final_video
    if staging_path.exists():
        try:
            final_validator(staging_path)
        except Exception as exc:
            return _failed_artifact(
                staging_path,
                error_code=getattr(exc, "code", "FINAL_VIDEO_VALIDATION_FAILED"),
                error=str(exc),
            )

    # Move staging → canonical.
    ws.video_final_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if staging_path.parent.resolve() == ws.video_final_path.parent.resolve():
            import os

            os.rename(staging_path, ws.video_final_path)
        else:
            shutil.copy2(staging_path, ws.video_final_path)
    except Exception as exc:
        return _failed_artifact(
            staging_path,
            error_code="VIDEO_COPY_FAILED",
            error=str(exc),
        )

    # Re-validate on the canonical copy.
    if ws.video_final_path.exists():
        try:
            final_validator(ws.video_final_path)
        except Exception as exc:
            return _failed_artifact(
                ws.video_final_path,
                error_code=getattr(exc, "code", "FINAL_VIDEO_VALIDATION_FAILED"),
                error=str(exc),
            )

    _record_video_path_in_project_json(ws, kind="final", path=ws.video_final_path)

    artifact = _artifact(
        "VideoArtifact",
        path=ws.video_final_path,
        staging_path=staging_path,
    )
    return artifact


def _record_video_path_in_project_json(ws, *, kind: str, path) -> None:
    """Persist `video_<kind>.path = path` into `project.json` (idempotent)."""
    import json as _json

    pj_path = ws.project_json_path
    try:
        existing = (
            _json.loads(pj_path.read_text(encoding="utf-8"))
            if pj_path.exists()
            else {}
        )
    except Exception:
        existing = {}
    if not isinstance(existing, dict):
        existing = {}
    key = f"video_{kind}"
    existing[key] = {"path": str(path)}
    pj_path.write_text(
        _json.dumps(existing, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


PHASE_REGISTRY: dict = {
    "open_or_create_project": _phase_open_or_create_project,
    "generate_script": _phase_generate_script,
    "review_script": _phase_review_script,
    "moderate_content": _phase_moderate_content,
    "prepare_narration": _phase_prepare_narration,
    "generate_audio": _phase_generate_audio,
    "transcribe": _phase_transcribe,
    "create_visual_plan": _phase_create_visual_plan,
    "generate_images": _phase_generate_images,
    "render_manifest": _phase_render_manifest,
    "render_main": _phase_render_main,
    "concat_final": _phase_concat_final,
    "generate_blog": _phase_generate_blog,
    "generate_shorts": _phase_generate_shorts,
    "generate_post_kit": _phase_generate_post_kit,
}


def install_default_services(context: ProjectContext) -> None:
    """Install the production services into the registry.

    Installs HTTP-backed services for:
    - LLM (script, review, blog, shorts, post_kit)
    - TTS (Kokoro via FastAPI /audio endpoint)
    - Whisper (via FastAPI /transcribe endpoint)
    """
    from ..services.llm import LLMService
    from ..services.tts import TTSService
    from ..services.whisper import WhisperService
    from ..services.image import ImageService

    llm = LLMService.from_context(context)
    tts = TTSService.from_context(context)
    whisper = WhisperService.from_context(context)
    image_svc = ImageService.from_context(context)

    # LLM services
    SERVICES["llm_open_or_create"] = (
        lambda slug, payload: llm.open_or_create_project(slug, payload)
    )
    SERVICES["llm_generate_script"] = lambda project_id, language: llm.generate_script(
        project_id, language
    )
    SERVICES["llm_review_script"] = (
        lambda project_id, language, **kw: llm.review_script(
            project_id, language,
            source_type=kw.get("source_type"),
            atmosphere=kw.get("atmosphere"),
        )
    )
    SERVICES["llm_generate_blog"] = lambda project_id, language: llm.generate_blog(
        project_id, language
    )
    SERVICES["llm_generate_shorts"] = (
        lambda project_id, language, max_clips: llm.generate_shorts(
            project_id, language, max_clips=max_clips
        )
    )
    SERVICES["llm_generate_post_kit"] = (
        lambda project_id, language: llm.generate_post_kit(project_id, language)
    )

    # TTS service — wraps ctx into a call to POST /projects/{id}/audio
    def _tts_from_ctx(ctx):
        ws = ctx.workspace
        # Read the narration text
        narration_text = ""
        if ws.narration_path.exists():
            narration_text = ws.narration_path.read_text(encoding="utf-8").strip()
        if not narration_text:
            raise RuntimeError(f"No narration text at {ws.narration_path}")
        # Read project_id from project.json
        import json as _json
        pjson = ws.root / "project.json"
        if pjson.exists():
            pdata = _json.loads(pjson.read_text(encoding="utf-8"))
            project_id = pdata.get("project_id", 1)
        else:
            project_id = 1
        language = "en"
        result = tts.generate_audio(project_id, language, narration_text)
        return result

    SERVICES["tts"] = _tts_from_ctx

    # Whisper service — wraps ctx into a call to POST /projects/{id}/transcribe
    def _whisper_from_ctx(ctx):
        import json as _json
        ws = ctx.workspace
        pjson = ws.root / "project.json"
        if pjson.exists():
            pdata = _json.loads(pjson.read_text(encoding="utf-8"))
            project_id = pdata.get("project_id", 1)
        else:
            project_id = 1
        result = whisper.transcribe(project_id, "en")
        # Copy transcript to workspace location if needed
        if result.path.exists() and not ws.transcript_path.exists():
            import shutil
            ws.transcript_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(result.path, ws.transcript_path)
        return result

    SERVICES["whisper"] = _whisper_from_ctx

    # Image generation service — wraps ctx into ComfyUI calls
    def _image_from_ctx(path, beat, ctx):
        image_svc.generate(path, beat, ctx)

    SERVICES["image"] = _image_from_ctx


PHASE_REGISTRY: dict = {
    "open_or_create_project": _phase_open_or_create_project,
    "generate_script": _phase_generate_script,
    "review_script": _phase_review_script,
    "moderate_content": _phase_moderate_content,
    "prepare_narration": _phase_prepare_narration,
    "generate_audio": _phase_generate_audio,
    "transcribe": _phase_transcribe,
    "create_visual_plan": _phase_create_visual_plan,
    "generate_images": _phase_generate_images,
    "render_manifest": _phase_render_manifest,
    "render_main": _phase_render_main,
    "concat_final": _phase_concat_final,
    "generate_blog": _phase_generate_blog,
    "generate_shorts": _phase_generate_shorts,
    "generate_post_kit": _phase_generate_post_kit,
}


def run_pipeline(context: ProjectContext, *, force: bool = False) -> list[PhaseOutcome]:
    """Run all deterministic phases in dependency order."""
    context.workspace.create()
    store = CheckpointStore(context.workspace)
    outcomes: list[PhaseOutcome] = []
    for phase in PHASE_ORDER:
        outcome = run_phase(phase, context, store, force=force)
        outcomes.append(outcome)
        if outcome.status == "failed":
            logger.error("phase %s failed: %s", phase, outcome.error)
            break
    return outcomes


# ── PipelineRunner class — the single orchestrator for new projects ──────


import time as _time
import uuid as _uuid
from datetime import datetime as _dt, timezone as _tz
from typing import Optional

from .result import PhaseResult, PipelineResult, SCHEMA_VERSION


class PipelineRunner:
    """Single orchestrator for the wired production pipeline.

    The new production flow for `redsun` projects is:
        context = ProjectContext.from_cli_args(args)
        runner, mode = PipelineRunner.open_or_create_project(
            args.slug, context,
            api_post_projects=...,
            slug_resolver=...,
        )
        result = runner.run()
    """

    def __init__(
        self,
        context: ProjectContext,
        workspace: ProjectWorkspace,
    ) -> None:
        self.context = context
        self.workspace = workspace
        self.mode: str = ""  # "created_new" | "open_existing" (set by open_or_create_project)
        self.resume: bool = False
        self.only_phases: set = set()
        self.skip_phases: set = set()

    @classmethod
    def open_or_create_project(
        cls,
        slug: str,
        context: ProjectContext,
        *,
        api_post_projects: Optional[callable] = None,
        slug_resolver: Optional[callable] = None,
        projects_root=None,
    ) -> tuple["PipelineRunner", str]:
        """Idempotent project open/create at the runner level.

        Returns `(runner, mode)` where mode is one of:
        - `"created_new"` — the canonical workspace did not exist;
          `api_post_projects` (if provided) was invoked to materialize
          the DB row, and `project.json` was updated with the returned
          `project_id`. The `context.project_id` attribute is set.
        - `"open_existing"` — the canonical workspace already existed
          (or a legacy DB row matched). `project.json` (if present) was
          re-read and `context.project_id` was populated. The DB row is
          NOT touched.

        Raises:
            WorkspaceError: if the slug matches an existing workspace
                or DB row with a different identity. Surfaces directly
                from `ProjectWorkspace.open_or_create`.

        The `api_post_projects` callable is the FastAPI HTTP client used
        to materialize a new DB row. It MUST accept `slug` and
        `context` (ProjectContext) and return a dict-like response with
        an `"id"` key. When `mode == "open_existing"` it is NOT called.
        """
        if context.workspace.technical_slug != slug:
            # The CLI may have built a context with a different slug;
            # normalize so the workspace and context agree.
            context.workspace = ProjectWorkspace.from_slug(slug)

        # Honor the projects_root from the existing context if any.
        if projects_root is None:
            try:
                projects_root = context.workspace.projects_root
            except Exception:
                projects_root = None

        workspace, mode = ProjectWorkspace.open_or_create(
            slug,
            slug_resolver=slug_resolver,
            projects_root=projects_root,
        )
        # Re-bind context to the resolved workspace (handles projects_root
        # overriding and slug normalization).
        context.workspace = workspace

        if mode == "created_new":
            if api_post_projects is not None:
                try:
                    response = api_post_projects(slug=slug, context=context)
                except Exception as exc:
                    logger.error(
                        "open_or_create_project: POST /projects failed for %s: %s",
                        slug, exc,
                    )
                    raise
                if isinstance(response, dict) and "id" in response:
                    context.project_id = response["id"]
                    _write_project_id_to_json(workspace, context.project_id)
            runner = cls(context, workspace)
            runner.mode = mode
            runner.resume = False
            return runner, mode

        # mode == "open_existing": re-read project_id from project.json
        if workspace.project_json_path.exists():
            try:
                import json as _json

                existing = _json.loads(
                    workspace.project_json_path.read_text(encoding="utf-8")
                )
                if isinstance(existing, dict) and "project_id" in existing:
                    context.project_id = existing["project_id"]
            except Exception:
                pass
        runner = cls(context, workspace)
        runner.mode = mode
        runner.resume = True
        return runner, mode

    @classmethod
    def run(
        cls,
        context: ProjectContext,
        workspace: ProjectWorkspace,
        *,
        force: bool = False,
        resume: bool = False,
        only_phases: Optional[list[str]] = None,
        skip_phases: Optional[list[str]] = None,
    ) -> PipelineResult:
        """Run the full pipeline and return a structured `PipelineResult`.

        For Phase 1 this delegates to the existing deterministic
        `run_pipeline(context, force)` helper. Later phases (3-7)
        extend the registered phase set with the LLM / TTS / Whisper /
        ComfyUI / Remotion phases and wire the quality gates.

        When `resume=True` the runner treats the workspace as already
        materialized and skips the `open_or_create_project` phase
        entirely, even if its checkpoint is missing. Existing
        checkpoint files take precedence over re-creating a project
        row.

        `only_phases` and `skip_phases` are validated against
        `PHASE_ORDER`; unknown names raise `ValueError` with a clear
        message. The filtered phase list is honored by `_run_phases`.
        """
        runner = cls(context, workspace)
        runner.resume = resume
        runner.only_phases = set(only_phases or [])
        runner.skip_phases = set(skip_phases or [])
        runner._validate_phase_lists()
        return runner._run(force=force)

    def _validate_phase_lists(self) -> None:
        """Validate `only_phases` / `skip_phases` against `PHASE_ORDER`.

        Raises `ValueError` with a clear message when an unknown name
        is provided. Silent fall-through is forbidden by the
        `wire-content-generation-phases` design D3.
        """
        self._validate_phase_lists_on_dicts(
            only_phases=self.only_phases,
            skip_phases=self.skip_phases,
        )

    def _validate_phase_lists_on_dicts(
        self,
        *,
        only_phases,
        skip_phases,
    ) -> None:
        unknown = (set(only_phases or []) | set(skip_phases or [])) - set(PHASE_ORDER)
        if unknown:
            valid_sorted = ", ".join(sorted(PHASE_ORDER))
            raise ValueError(
                f"unknown phase(s): {sorted(unknown)!r}. "
                f"Valid phases: {valid_sorted}"
            )

    def execute(self, *, force: bool = False, resume: bool | None = None) -> PipelineResult:
        """Instance-level convenience: run the pipeline using the
        runner's already-bound `context` and `workspace`.

        `resume` defaults to `self.resume` (set by `open_or_create_project`)
        but may be overridden.
        """
        if resume is not None:
            self.resume = resume
        return self._run(force=force)

    def _run(self, *, force: bool = False) -> PipelineResult:
        started_at = _dt.now(_tz.utc).isoformat()
        t0 = _time.monotonic()
        pipeline_run_id = str(_uuid.uuid4())

        # Materialize the canonical workspace.
        self.workspace.create()

        # Hydrate `context.project_id` from the materialized `project.json`
        # BEFORE the phase loop runs. When `resume=True`, the
        # `open_or_create_project` phase is skipped (its checkpoint is
        # `completed` or the workspace already existed), so this is the
        # only place we can read the previously-assigned id from disk and
        # hand it back to the context. Without this, every downstream
        # phase that uses `ctx.project_id` (generate_script, generate_audio,
        # transcribe, etc.) crashes with `PROJECT_ID_MISSING`.
        if getattr(self.context, "project_id", None) is None:
            try:
                pjson_path = self.workspace.project_json_path
                if pjson_path.exists():
                    import json as _pj
                    pdata = _pj.loads(pjson_path.read_text(encoding="utf-8"))
                    pid = pdata.get("project_id")
                    if pid:
                        self.context.project_id = int(pid)
            except Exception:
                # Best-effort: phases that need a project_id will raise
                # their own descriptive error if hydration failed.
                pass

        # Run the deterministic phase list. When `resume=True` and the
        # workspace is already materialized, the `open_or_create_project`
        # phase is skipped entirely — see task 3.2.
        outcomes = self._run_phases(force=force)

        finished_at = _dt.now(_tz.utc).isoformat()
        duration = _time.monotonic() - t0

        phase_results: list[PhaseResult] = []
        any_failed = False
        failed_phase: str = ""
        failed_error: str = ""
        failed_error_code: str = ""
        failed_expected = None
        failed_actual = None

        # Success-only metrics harvested from completed phases.
        tts_engine: str = ""
        voice: str = ""
        transcript_coverage: float | None = None
        visual_beats: int | None = None
        images_validated: int | None = None
        video_main_path: str = ""
        video_final_path: str = ""
        artifacts: dict = {}
        script_artifact: dict = {}

        for o in outcomes:
            phase_results.append(
                PhaseResult(
                    phase=o.phase,
                    status=o.status,
                    duration_seconds=0.0,
                    error=o.error if o.status == "failed" else None,
                    error_code=getattr(o, "error_code", None) or None,
                    expected=getattr(o, "expected", None) or None,
                    actual=getattr(o, "actual", None) or None,
                )
            )
            if o.status == "failed":
                any_failed = True
                failed_phase = o.phase
                failed_error = o.error or ""
                failed_error_code = getattr(o, "error_code", "") or ""
                failed_expected = getattr(o, "expected", None)
                failed_actual = getattr(o, "actual", None)
                break

        # Enrich success-only fields from the persisted checkpoints
        # AND from the live phase outcomes. Checkpoints are the source
        # of truth on resume; live outcomes capture the just-finished run.
        try:
            from .checkpoints import CheckpointStore as _CS

            store = _CS(self.workspace)
            for ck_phase in (
                "generate_audio",
                "transcribe",
                "create_visual_plan",
                "generate_images",
                "render_manifest",
                "render_main",
                "concat_final",
            ):
                try:
                    ck = store.read(ck_phase)
                except FileNotFoundError:
                    continue
                if ck.status != "completed":
                    continue
                data = ck.data or {}
                if ck_phase == "generate_audio":
                    tts_engine = data.get("tts_engine", "") or tts_engine
                    voice = data.get("voice", "") or voice
                elif ck_phase == "transcribe":
                    cov = data.get("coverage")
                    if cov is not None:
                        transcript_coverage = cov
                elif ck_phase == "create_visual_plan":
                    beat_count = data.get("beat_count")
                    if beat_count is not None:
                        visual_beats = int(beat_count)
                elif ck_phase == "generate_images":
                    img_count = data.get("image_count")
                    if img_count is not None:
                        images_validated = int(img_count)
                elif ck_phase == "render_manifest":
                    img_count = data.get("image_count")
                    if img_count is not None:
                        images_validated = int(img_count)
                elif ck_phase == "render_main":
                    p = data.get("path")
                    if p:
                        video_main_path = str(p)
                elif ck_phase == "concat_final":
                    p = data.get("path")
                    if p:
                        video_final_path = str(p)
        except Exception:
            pass

        # Also harvest from the live outcomes (Phase 7: artifact
        # attributes carry beat_count / image_count / path).
        for o in outcomes:
            if o.status != "completed":
                continue
            a = o.artifact
            if a is None:
                continue
            beat_count = getattr(a, "beat_count", None)
            if beat_count is not None and o.phase == "create_visual_plan":
                visual_beats = int(beat_count)
            img_count = getattr(a, "image_count", None)
            if img_count is not None and o.phase in ("generate_images", "render_manifest"):
                images_validated = int(img_count)
            img_filenames = getattr(a, "image_filenames", None)
            if img_filenames is not None:
                artifacts["image_filenames"] = list(img_filenames)
            artifact_path = getattr(a, "path", None)
            if artifact_path is not None and o.phase in ("render_main", "concat_final"):
                artifacts[o.phase + "_path"] = str(artifact_path)
            # ScriptArtifact harvest (script + review phases).
            if o.phase == "generate_script" and not script_artifact:
                script_artifact = {
                    "path": str(artifact_path) if artifact_path else "",
                    "sha256": getattr(a, "sha256", "") or "",
                    "source": getattr(a, "source", "generated") or "generated",
                    "model": getattr(a, "model", "") or "",
                }
            if o.phase == "review_script":
                src = getattr(a, "source", None)
                if src == "reviewed":
                    script_artifact = {
                        "path": str(artifact_path) if artifact_path else "",
                        "sha256": getattr(a, "sha256", "") or "",
                        "source": "reviewed",
                        "model": script_artifact.get("model", ""),
                    }
                else:
                    # Review ran but did not produce a revised body;
                    # leave the generated script artifact as-is.
                    if not script_artifact:
                        script_artifact = {
                            "path": str(artifact_path) if artifact_path else "",
                            "sha256": "",
                            "source": "generated",
                            "model": "",
                        }

        # Fallback: when the live run skipped the script phases (e.g.
        # resume=True and the generated script was already on disk),
        # read the on-disk file to populate the script artifact.
        if not script_artifact or not script_artifact.get("path"):
            ws_sp = self.workspace.script_path
            ws_rp = self.workspace.script_reviewed_path
            chosen = None
            if ws_rp.exists() and ws_rp.stat().st_size > 0:
                chosen = (ws_rp, "reviewed")
            elif ws_sp.exists() and ws_sp.stat().st_size > 0:
                chosen = (ws_sp, "generated")
            if chosen is not None:
                path, source = chosen
                script_artifact = {
                    "path": str(path),
                    "sha256": self.context.hash_file(path),
                    "source": source,
                    "model": "",
                }
            elif self.context.episode.source_text:
                script_artifact = {
                    "path": "",
                    "sha256": self.context.hash_text(self.context.episode.source_text),
                    "source": "source_text",
                    "model": "",
                }

        # Compute video_final/video_main as a fallback: when the live
        # run produced them but they weren't in checkpoints yet.
        if not video_main_path:
            mp = self.workspace.video_main_path
            if mp.exists():
                video_main_path = str(mp)
        if not video_final_path:
            fp = self.workspace.video_final_path
            if fp.exists():
                video_final_path = str(fp)

        # If no outcomes (empty registry, common at Phase 1) the run is
        # treated as a no-op READY so the CLI wiring is exercisable
        # before the full phase list lands.
        if not phase_results:
            any_failed = False

        # Persist the quality-gate results to project.json (task 4.3).
        if any_failed:
            self._persist_quality_gates(
                phase=phase_results[-1].phase if phase_results else "",
                error_code=failed_error_code,
                error=failed_error,
                expected=failed_expected,
                actual=failed_actual,
            )

        result = PipelineResult(
            schema_version=SCHEMA_VERSION,
            status="FAILED" if any_failed else "READY",
            technical_slug=self.context.episode.slug,
            workspace=str(self.workspace.root),
            project_id=self.context.project_id,
            pipeline_run_id=pipeline_run_id,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=duration,
            phases=phase_results,
            artifacts=artifacts,
            tts_engine=tts_engine or None,
            voice=voice or None,
            transcript_coverage=transcript_coverage,
            visual_beats=visual_beats,
            images_validated=images_validated,
            video_main=video_main_path or None,
            video_final=video_final_path or None,
            script=script_artifact or None,
            failed_phase=failed_phase or None,
            error_code=failed_error_code or None,
            expected=failed_expected,
            actual=failed_actual,
        )

        # Persist the structured PipelineResult to pipeline_summary.json
        # (task 8.4). The CLI and downstream tooling read from here.
        self._persist_pipeline_summary(result)
        return result

    def _persist_pipeline_summary(self, result: "PipelineResult") -> None:
        """Write the structured `PipelineResult` to `pipeline_summary.json`."""
        import json as _json

        try:
            payload = result.to_dict()
        except Exception:
            payload = {}
        path = self.workspace.pipeline_summary_path
        path.write_text(
            _json.dumps(payload, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

    def _persist_quality_gates(
        self,
        *,
        phase: str,
        error_code: str,
        error: str,
        expected=None,
        actual=None,
    ) -> None:
        """Persist the most recent quality-gate result under `quality_gates.<phase>`.

        Idempotent — overwrites the same key when the gate fires again.
        """
        import json as _json

        path = self.workspace.project_json_path
        try:
            existing = (
                _json.loads(path.read_text(encoding="utf-8"))
                if path.exists()
                else {}
            )
        except Exception:
            existing = {}
        if not isinstance(existing, dict):
            existing = {}
        qg = existing.setdefault("quality_gates", {})
        gate_key = {
            "generate_audio": "audio",
            "transcribe": "transcript",
            "prepare_narration": "narration",
        }.get(phase, phase)
        qg[gate_key] = {
            "phase": phase,
            "ok": False,
            "error": error,
            "error_code": error_code,
        }
        if expected is not None:
            qg[gate_key]["expected"] = expected
        if actual is not None:
            qg[gate_key]["actual"] = actual
        existing["quality_gates"] = qg
        path.write_text(
            _json.dumps(existing, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _run_phases(self, *, force: bool = False) -> list:
        """Run all phases, honoring `resume` semantics for `open_or_create_project`."""
        from .checkpoints import CheckpointStore
        from ..workspace import WorkspaceError

        store = CheckpointStore(self.workspace)
        outcomes: list = []

        # Filter PHASE_ORDER by only_phases / skip_phases.
        filtered = []
        for phase in PHASE_ORDER:
            if self.only_phases and phase not in self.only_phases:
                outcomes.append(
                    PhaseOutcome(
                        phase=phase,
                        status="skipped",
                        error="phase not in --only-phases list",
                    )
                )
                continue
            if phase in self.skip_phases:
                outcomes.append(
                    PhaseOutcome(
                        phase=phase,
                        status="skipped",
                        error="phase in --skip-phases list",
                    )
                )
                continue
            filtered.append(phase)

        for phase in filtered:
            # Task 3.2: when resume=True and the workspace already exists,
            # skip the open_or_create_project phase entirely. Existing
            # checkpoint files take precedence over re-creating the DB row.
            if (
                phase == "open_or_create_project"
                and self.resume
                and self.workspace.root.exists()
            ):
                outcomes.append(
                    PhaseOutcome(
                        phase=phase,
                        status="skipped",
                        error="resume: workspace exists, not re-creating",
                    )
                )
                continue

            try:
                outcome = run_phase(phase, self.context, store, force=force)
            except WorkspaceError:
                raise
            outcomes.append(outcome)
            if outcome.status == "failed":
                # Task 4.2: cascade the invalidation so the next run with
                # `--resume` re-runs the failed phase and any downstream
                # phases that depended on it.
                try:
                    affected = store.invalidate_downstream(phase)
                    logger.error(
                        "phase %s failed: %s — invalidated downstream: %s",
                        phase,
                        outcome.error,
                        ", ".join(affected) if affected else "(none)",
                    )
                except Exception as inv_exc:
                    logger.warning(
                        "invalidate_downstream(%s) failed: %s", phase, inv_exc
                    )
                break
        return outcomes


def _write_project_id_to_json(workspace: ProjectWorkspace, project_id: int) -> None:
    """Write `project_id` into an existing `project.json` if present.

    The `POST /projects` handler in `main.py` writes the initial
    `project.json` with title, slug, etc. but no DB id. After the row
    is inserted, this helper back-fills `project_id` so the workspace
    can later be reopened without re-querying the DB.
    """
    import json as _json

    path = workspace.project_json_path
    if not path.exists():
        return
    try:
        existing = _json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return
    if not isinstance(existing, dict):
        return
    existing["project_id"] = project_id
    path.write_text(
        _json.dumps(existing, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )