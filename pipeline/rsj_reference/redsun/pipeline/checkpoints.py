"""Hashed checkpoints with versioned schema.

Each phase writes `projects/<slug>/checkpoints/<phase>.json` with
`schema_version`, `phase`, `status`, `completed_at`, `input_hash`,
`config_hash`, `output_hash`, `validation`.

Legacy `.done` files (from `motor-python/storage/<slug>/checkpoints/<phase>.done`)
are migrated on first read (copy + rename, never delete).
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..exceptions import CheckpointCorruptError
from ..workspace import ProjectWorkspace


logger = logging.getLogger("redsun.checkpoints")

SCHEMA_VERSION = 1

# Phase dependency graph. A change in `phase A` invalidates every
# phase reachable from `A` in this DAG.
DEPENDENCIES: dict[str, list[str]] = {
    "open_or_create_project": [],
    "generate_script": ["open_or_create_project"],
    "review_script": ["generate_script"],
    "moderate_content": ["review_script"],
    "prepare_narration": ["generate_script", "review_script", "moderate_content"],
    "generate_audio": ["prepare_narration"],
    "transcribe": ["generate_audio"],
    "create_visual_plan": ["transcribe"],
    "generate_images": ["create_visual_plan"],
    "render_manifest": ["generate_images"],
    "render_main": ["render_manifest"],
    "concat_final": ["render_main"],
    "generate_blog": ["prepare_narration"],
    "generate_shorts": ["prepare_narration"],
    "generate_post_kit": ["concat_final", "generate_blog"],
    "organize_output": ["generate_post_kit"],
}

# Per-phase config slice and artifact paths (for hashing).
PHASE_INPUTS: dict[str, list[str]] = {
    "open_or_create_project": [],
    "generate_script": ["script_path"],
    "review_script": ["script_path"],
    "moderate_content": ["script_path"],
    "prepare_narration": ["script_path", "narration_path"],
    "generate_audio": [
        "narration_path",
        "audio_chunks_dir",
        "audio_main_path",
    ],
    "transcribe": ["audio_main_path", "transcript_path"],
    "create_visual_plan": ["transcript_path", "visual_plan_path"],
    "generate_images": ["visual_plan_path", "images_dir"],
    "render_manifest": [
        "manifest_path",
        "audio_main_path",
        "transcript_path",
        "images_dir",
    ],
    "render_main": ["manifest_path", "video_main_path"],
    "concat_final": ["video_final_path"],
    "generate_blog": ["blog_dir"],
    "generate_shorts": ["shorts_dir"],
    "generate_post_kit": ["post_kit_dir"],
    "organize_output": [],
}


def downstream_phases(phase: str) -> list[str]:
    """Return phases that depend (directly or transitively) on `phase`.

    A change in `phase` invalidates every phase whose dependency
    list transitively includes `phase`. `DEPENDENCIES` is structured
    as `dependent -> [upstream]`, so we walk the reverse direction.
    """
    # Build reverse map once: upstream -> dependents.
    if not hasattr(downstream_phases, "_reverse"):
        reverse: dict[str, list[str]] = {}
        for dependent, ups in DEPENDENCIES.items():
            for u in ups:
                reverse.setdefault(u, []).append(dependent)
        downstream_phases._reverse = reverse  # type: ignore[attr-defined]
    reverse = downstream_phases._reverse  # type: ignore[attr-defined]

    seen: set[str] = set()
    stack: list[str] = list(reverse.get(phase, []))
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(reverse.get(cur, []))
    return [p for p in DEPENDENCIES if p in seen]


@dataclass
class Checkpoint:
    schema_version: int
    phase: str
    status: str  # "completed" | "failed" | "invalidated"
    completed_at: str
    input_hash: str
    config_hash: str
    output_hash: str
    validation: dict = field(default_factory=dict)
    data: dict = field(default_factory=dict)


class CheckpointStore:
    """Read/write hashed checkpoints under `projects/<slug>/checkpoints/`.

    Construction is cheap; cache the store at the top of the runner
    for one pipeline run.
    """

    def __init__(self, workspace: ProjectWorkspace) -> None:
        self.ws = workspace
        self.ws.checkpoints_dir.mkdir(parents=True, exist_ok=True)
        self._legacy_migrated: set[str] = set()

    # ── write ────────────────────────────────────────────────────────

    def write(
        self,
        phase: str,
        *,
        config_hash: str,
        input_hash: str,
        output_hash: str,
        status: str = "completed",
        validation: Optional[dict] = None,
        data: Optional[dict] = None,
    ) -> Checkpoint:
        ck = Checkpoint(
            schema_version=SCHEMA_VERSION,
            phase=phase,
            status=status,
            completed_at=datetime.now(timezone.utc).isoformat(),
            input_hash=input_hash,
            config_hash=config_hash,
            output_hash=output_hash,
            validation=validation or {},
            data=data or {},
        )
        path = self.ws.checkpoint_path(phase)
        path.write_text(
            json.dumps(asdict(ck), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info("checkpoint written: %s status=%s", path, status)
        return ck

    def invalidate(self, phase: str) -> None:
        """Mark a phase's checkpoint as `invalidated`. Used by the cascade."""
        path = self.ws.checkpoint_path(phase)
        if not path.exists():
            return
        try:
            ck = self.read(phase)
        except CheckpointCorruptError:
            return
        ck.status = "invalidated"
        path.write_text(
            json.dumps(asdict(ck), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def invalidate_downstream(self, phase: str) -> list[str]:
        """Invalidate every phase downstream of `phase`."""
        affected = downstream_phases(phase)
        for p in affected:
            self.invalidate(p)
        return affected

    # ── read ─────────────────────────────────────────────────────────

    def read(self, phase: str) -> Checkpoint:
        path = self.ws.checkpoint_path(phase)
        if not path.exists():
            raise FileNotFoundError(path)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CheckpointCorruptError(
                f"checkpoint {phase} corrupt: {exc}"
            ) from exc
        if raw.get("schema_version") != SCHEMA_VERSION:
            # Forward migration hook. For v1 this is a hard fail.
            raise CheckpointCorruptError(
                f"checkpoint {phase} schema_version="
                f"{raw.get('schema_version')!r} expected {SCHEMA_VERSION}"
            )
        return Checkpoint(
            schema_version=raw["schema_version"],
            phase=raw["phase"],
            status=raw["status"],
            completed_at=raw["completed_at"],
            input_hash=raw["input_hash"],
            config_hash=raw["config_hash"],
            output_hash=raw["output_hash"],
            validation=raw.get("validation", {}),
            data=raw.get("data", {}),
        )

    # ── resume / validity ───────────────────────────────────────────

    def is_valid(
        self,
        phase: str,
        *,
        current_input_hash: str,
        current_config_hash: str,
    ) -> bool:
        """True if the checkpoint matches current inputs and config.

        Returns False for missing, invalidated, or stale checkpoints.
        """
        try:
            ck = self.read(phase)
        except FileNotFoundError:
            return False
        except CheckpointCorruptError:
            return False
        if ck.status != "completed":
            return False
        return (
            ck.input_hash == current_input_hash
            and ck.config_hash == current_config_hash
        )

    # ── legacy migration ────────────────────────────────────────────

    def migrate_legacy(self, project_id: int) -> list[str]:
        """Copy legacy `.done` files into the new `checkpoints/`.

        Original `.done` files are never deleted. Returns the list
        of phase names that were migrated.
        """
        if "legacy_done" in self._legacy_migrated:
            return []
        self._legacy_migrated.add("legacy_done")

        legacy_dirs = []
        slug = self.ws.technical_slug
        for candidate in (
            Path(__file__).resolve().parent.parent.parent / "storage" / slug / "checkpoints",
            Path(__file__).resolve().parent.parent.parent
            / "storage"
            / str(project_id)
            / "checkpoints",
        ):
            if candidate.exists() and candidate.is_dir():
                legacy_dirs.append(candidate)

        migrated: list[str] = []
        for d in legacy_dirs:
            for legacy in d.glob("*.done"):
                    phase = legacy.stem
                    target = self.ws.checkpoint_path(phase)
                    if target.exists():
                        continue
                    try:
                        raw = json.loads(legacy.read_text(encoding="utf-8"))
                    except Exception:
                        continue
                    migrated.append(phase)
                    target.write_text(
                        json.dumps(
                            {
                                "schema_version": SCHEMA_VERSION,
                                "phase": phase,
                                "status": raw.get("status", "completed"),
                                "completed_at": raw.get(
                                    "completed_at",
                                    datetime.now(timezone.utc).isoformat(),
                                ),
                                "input_hash": "unknown:legacy",
                                "config_hash": "unknown:legacy",
                                "output_hash": "unknown:legacy",
                                "validation": {"legacy_unverified": True},
                                "data": raw.get("data", {}),
                            },
                            indent=2,
                            ensure_ascii=False,
                        ),
                        encoding="utf-8",
                    )
        return migrated