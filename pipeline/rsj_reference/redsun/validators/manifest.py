"""Render manifest validator (Phase 8)."""
from __future__ import annotations

import json

from ..exceptions import ManifestValidationError
from . import ValidationResult


_REQUIRED_FIELDS = (
    "project_id",
    "slug",
    "language_code",
    "audio_filename",
    "timestamps_filename",
    "image_filenames",
    "image_count",
    "duration_seconds",
    "fps",
    "width",
    "height",
)


def validate_render_manifest(
    manifest_path,
    audio_path,
    timestamps_path,
    images_dir,
    *,
    duration_drift_tolerance_sec: float = 2.0,
) -> ValidationResult:
    """Deterministic manifest validator.

    Rejects when any required field is missing, files referenced
    by the manifest do not exist, image_count drifts from
    len(image_filenames), or duration drifts by more than
    `duration_drift_tolerance_sec` from the actual audio length.
    """
    from pathlib import Path
    import wave

    manifest_path = Path(manifest_path)
    audio_path = Path(audio_path)
    timestamps_path = Path(timestamps_path)
    images_dir = Path(images_dir)

    result = ValidationResult(metrics={"manifest_path": str(manifest_path)})
    if not manifest_path.exists():
        raise ManifestValidationError(f"manifest missing: {manifest_path}")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ManifestValidationError(f"manifest not valid JSON: {exc}") from exc

    missing = [f for f in _REQUIRED_FIELDS if f not in manifest]
    if missing:
        result.fail(f"manifest missing fields: {missing}")
        raise ManifestValidationError("; ".join(result.errors))

    audio_filename = manifest["audio_filename"]
    timestamps_filename = manifest["timestamps_filename"]
    image_filenames = manifest["image_filenames"]

    audio_full = images_dir.parent / audio_filename if False else audio_path
    # The audio path passed in is canonical; verify it matches the
    # basename declared in the manifest.
    if Path(audio_filename).name != audio_path.name:
        result.fail(
            f"manifest audio_filename basename {audio_filename!r} != "
            f"audio path {audio_path.name!r}"
        )

    if not audio_path.exists():
        result.fail(f"audio file missing: {audio_path}")

    if not timestamps_path.exists():
        result.fail(f"timestamps file missing: {timestamps_path}")

    for fn in image_filenames:
        if not (images_dir / fn).exists():
            result.fail(f"image referenced by manifest missing: {images_dir / fn}")

    if manifest["image_count"] != len(image_filenames):
        result.fail(
            f"image_count {manifest['image_count']} != "
            f"len(image_filenames) {len(image_filenames)}"
        )

    # Duration drift
    declared = float(manifest["duration_seconds"])
    audio_duration = 0.0
    if audio_path.exists():
        try:
            with wave.open(str(audio_path), "rb") as w:
                rate = w.getframerate()
                frames = w.getnframes()
                audio_duration = frames / rate if rate else 0.0
        except Exception:
            pass
    result.metrics["declared_duration_sec"] = declared
    result.metrics["actual_audio_duration_sec"] = audio_duration
    if abs(declared - audio_duration) > duration_drift_tolerance_sec:
        result.fail(
            f"manifest duration {declared}s drifts > "
            f"{duration_drift_tolerance_sec}s from audio {audio_duration:.1f}s"
        )

    # timestamps must be non-empty
    try:
        ts_data = json.loads(timestamps_path.read_text(encoding="utf-8"))
    except Exception:
        ts_data = []
    if not ts_data:
        result.fail("timestamps file is empty")

    if not result.ok:
        raise ManifestValidationError("; ".join(result.errors))
    return result