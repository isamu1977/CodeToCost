"""FastAPI router: workspace + checkpoints + artifact-manifest.

This is the first router split out of the legacy `main.py` for
Phase 10. Existing endpoints in `main.py` are kept; new
endpoints are added here behind a thin router.

New endpoints:
- GET  /projects/{id}/workspace
- GET  /projects/{id}/checkpoints
- POST /projects/{id}/checkpoints/reset
- GET  /projects/{id}/artifact-manifest
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ...workspace import ProjectWorkspace
from ...pipeline.checkpoints import CheckpointStore
from ...themes import catalog
from ...context import ProjectContext, EpisodeMetadata, ThemeMetadata
from ...exceptions import (
    WorkspaceError,
    CheckpointCorruptError,
)


logger = logging.getLogger("redsun.api.workspace")

router = APIRouter(prefix="/projects/{project_id}", tags=["workspace"])


# ── schemas ─────────────────────────────────────────────────────────────


class WorkspaceResponse(BaseModel):
    project_id: Optional[int]
    technical_slug: str
    root: str
    canonical_paths: dict


class CheckpointResponse(BaseModel):
    phase: str
    status: str
    completed_at: str
    input_hash: str
    config_hash: str
    output_hash: str
    validation: dict = Field(default_factory=dict)
    data: dict = Field(default_factory=dict)


class CheckpointListResponse(BaseModel):
    project_id: int
    technical_slug: str
    checkpoints: List[CheckpointResponse]


class ResetRequest(BaseModel):
    phase: str
    cascade: bool = True


class ResetResponse(BaseModel):
    project_id: int
    invalidated: List[str]


class ArtifactManifestResponse(BaseModel):
    project_id: int
    technical_slug: str
    workspace_root: str
    script_present: bool
    narration_present: bool
    audio_present: bool
    transcript_present: bool
    visual_plan_present: bool
    manifest_present: bool
    main_video_present: bool
    final_video_present: bool
    images_present: int
    theme_metadata: dict
    episode: dict


# ── helpers ────────────────────────────────────────────────────────────


def _resolve_workspace(
    project_id: int, slug_resolver=None
) -> ProjectWorkspace:
    """Build a ProjectWorkspace for a DB project.

    Until Phase 10/Phase 9 migrate the DB layer, we accept a
    project_id and look up the slug through an injected resolver
    (or fall back to a heuristic for known test projects).
    """
    if slug_resolver is None:
        # Heuristic fallback for development. Production wires a
        # real resolver that reads from the VideoProject table.
        slug = f"project-{project_id}"
    else:
        slug = slug_resolver(project_id)
    return ProjectWorkspace.from_slug(slug)


# ── routes ─────────────────────────────────────────────────────────────


@router.get("/workspace", response_model=WorkspaceResponse)
def get_workspace(project_id: int) -> WorkspaceResponse:
    """Return the canonical workspace for the given project."""
    ws = _resolve_workspace(project_id)
    canonical = {
        "project_json": str(ws.project_json_path),
        "script": str(ws.script_path),
        "narration": str(ws.narration_path),
        "audio_main": str(ws.audio_main_path),
        "audio_chunks_dir": str(ws.audio_chunks_dir),
        "transcript": str(ws.transcript_path),
        "visual_plan": str(ws.visual_plan_path),
        "images_dir": str(ws.images_dir),
        "manifest": str(ws.manifest_path),
        "video_main": str(ws.video_main_path),
        "video_final": str(ws.video_final_path),
        "checkpoints_dir": str(ws.checkpoints_dir),
        "logs_dir": str(ws.logs_dir),
        "blog_dir": str(ws.blog_dir),
        "shorts_dir": str(ws.shorts_dir),
        "post_kit_dir": str(ws.post_kit_dir),
    }
    return WorkspaceResponse(
        project_id=project_id,
        technical_slug=ws.technical_slug,
        root=str(ws.root),
        canonical_paths=canonical,
    )


@router.get("/checkpoints", response_model=CheckpointListResponse)
def list_checkpoints(project_id: int) -> CheckpointListResponse:
    """Return every checkpoint under the project's `checkpoints/`."""
    ws = _resolve_workspace(project_id)
    store = CheckpointStore(ws)
    out: list = []
    for path in sorted(ws.checkpoints_dir.glob("*.json")):
        phase = path.stem
        try:
            ck = store.read(phase)
            out.append(
                CheckpointResponse(
                    phase=ck.phase,
                    status=ck.status,
                    completed_at=ck.completed_at,
                    input_hash=ck.input_hash,
                    config_hash=ck.config_hash,
                    output_hash=ck.output_hash,
                    validation=ck.validation,
                    data=ck.data,
                )
            )
        except CheckpointCorruptError as exc:
            logger.warning("corrupt checkpoint %s: %s", path, exc)
    return CheckpointListResponse(
        project_id=project_id,
        technical_slug=ws.technical_slug,
        checkpoints=out,
    )


@router.post("/checkpoints/reset", response_model=ResetResponse)
def reset_checkpoint(
    project_id: int, payload: ResetRequest
) -> ResetResponse:
    """Mark a checkpoint as invalidated. Optionally cascade."""
    ws = _resolve_workspace(project_id)
    store = CheckpointStore(ws)
    invalidated = []
    if payload.cascade:
        invalidated = store.invalidate_downstream(payload.phase)
    else:
        store.invalidate(payload.phase)
        invalidated = [payload.phase]
    return ResetResponse(
        project_id=project_id, invalidated=invalidated
    )


@router.get("/artifact-manifest", response_model=ArtifactManifestResponse)
def get_artifact_manifest(project_id: int) -> ArtifactManifestResponse:
    """Return a snapshot of which artifacts exist on disk for the
    project, plus theme/episode metadata.
    """
    ws = _resolve_workspace(project_id)
    images_present = (
        sum(1 for _ in ws.images_dir.glob("*.png"))
        if ws.images_dir.exists()
        else 0
    )
    theme = catalog.find(ws.technical_slug) or {}
    return ArtifactManifestResponse(
        project_id=project_id,
        technical_slug=ws.technical_slug,
        workspace_root=str(ws.root),
        script_present=ws.script_path.exists(),
        narration_present=ws.narration_path.exists(),
        audio_present=ws.audio_main_path.exists(),
        transcript_present=ws.transcript_path.exists(),
        visual_plan_present=ws.visual_plan_path.exists(),
        manifest_present=ws.manifest_path.exists(),
        main_video_present=ws.video_main_path.exists(),
        final_video_present=ws.video_final_path.exists(),
        images_present=images_present,
        theme_metadata=theme,
        episode={"slug": ws.technical_slug},
    )