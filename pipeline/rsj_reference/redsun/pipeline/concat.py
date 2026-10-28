"""Phase 11: workspace-aware concat final.

Reads main video from `render-remotion/<slug>_main.mp4`
(staging) and writes the validated final MP4 into
`projects/<slug>/video/<slug>_final.mp4` (canonical).

This is a thin Python wrapper around the existing
`render-remotion/render_full.sh` flow. It exists so the new
runner can call it without forking a shell, and so the final
output location is recorded in `project.json`.

The legacy `concat_final.sh` continues to work; this function
adds the canonical-output copy.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Optional

from ..exceptions import VideoValidationError
from ..workspace import ProjectWorkspace
from ..validators.video import validate_final_video


logger = logging.getLogger("redsun.concat_final")


def copy_final_to_workspace(
    staging_final_path: Path,
    workspace: ProjectWorkspace,
    *,
    validate: bool = True,
) -> Path:
    """Copy the rendered final MP4 into the project's canonical
    video directory.

    Validates first (if `validate=True`). Returns the canonical
    destination path.
    """
    staging = Path(staging_final_path)
    dest = workspace.video_final_path
    dest.parent.mkdir(parents=True, exist_ok=True)

    if validate:
        validate_final_video(staging)

    shutil.copy2(staging, dest)
    size_mb = dest.stat().st_size / 1024 / 1024
    logger.info(
        "final video copied: %s (%.1f MB)", dest, size_mb
    )
    return dest


def copy_main_to_workspace(
    staging_main_path: Path,
    workspace: ProjectWorkspace,
    *,
    validate: bool = True,
) -> Path:
    """Same as `copy_final_to_workspace` but for the main render."""
    staging = Path(staging_main_path)
    dest = workspace.video_main_path
    dest.parent.mkdir(parents=True, exist_ok=True)

    if validate:
        from ..validators.video import validate_main_video
        validate_main_video(staging)

    shutil.copy2(staging, dest)
    size_mb = dest.stat().st_size / 1024 / 1024
    logger.info(
        "main video copied: %s (%.1f MB)", dest, size_mb
    )
    return dest