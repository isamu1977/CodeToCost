"""Phase 8: video validator (main + final) using ffprobe."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Optional

from ..exceptions import VideoValidationError
from . import ValidationResult


def _ffprobe_streams(video_path: Path) -> list[dict]:
    """Return ffprobe's list of streams for the given file."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_streams",
                "-show_format",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    return list(data.get("streams", []))


def _stream_summary(streams: list[dict]) -> dict:
    has_video = False
    has_audio = False
    video_codec = None
    audio_codec = None
    width = None
    height = None
    for s in streams:
        if s.get("codec_type") == "video":
            has_video = True
            video_codec = s.get("codec_name")
            width = s.get("width")
            height = s.get("height")
        elif s.get("codec_type") == "audio":
            has_audio = True
            audio_codec = s.get("codec_name")
    return {
        "has_video": has_video,
        "has_audio": has_audio,
        "video_codec": video_codec,
        "audio_codec": audio_codec,
        "width": width,
        "height": height,
    }


def _duration(streams: list[dict]) -> Optional[float]:
    for s in streams:
        if s.get("duration"):
            try:
                return float(s["duration"])
            except (TypeError, ValueError):
                pass
    return None


def validate_main_video(
    video_path,
    *,
    expected_width: int = 1920,
    expected_height: int = 1080,
    min_duration_sec: float = 30.0,
) -> ValidationResult:
    video_path = Path(video_path)
    result = ValidationResult(metrics={"video_path": str(video_path)})
    if not video_path.exists():
        result.fail(f"video file missing: {video_path}")
        raise VideoValidationError("; ".join(result.errors))

    streams = _ffprobe_streams(video_path)
    if not streams:
        result.fail("ffprobe failed (or ffprobe not installed)")
        raise VideoValidationError("; ".join(result.errors))

    summary = _stream_summary(streams)
    result.metrics.update(summary)

    if not summary["has_video"]:
        result.fail("video has no video stream")
    if not summary["has_audio"]:
        result.fail("video has no audio stream")
    if summary["video_codec"] and summary["video_codec"] != "h264":
        result.fail(f"video codec is {summary['video_codec']!r}, expected h264")
    if summary["audio_codec"] and summary["audio_codec"] != "aac":
        result.fail(f"audio codec is {summary['audio_codec']!r}, expected aac")
    if summary["width"] and summary["width"] != expected_width:
        result.fail(
            f"video width is {summary['width']}, expected {expected_width}"
        )
    if summary["height"] and summary["height"] != expected_height:
        result.fail(
            f"video height is {summary['height']}, expected {expected_height}"
        )

    duration = _duration(streams)
    if duration is not None:
        result.metrics["duration_sec"] = duration
        if duration < min_duration_sec:
            result.fail(
                f"video too short: {duration:.1f}s < {min_duration_sec}s"
            )

    if not result.ok:
        raise VideoValidationError("; ".join(result.errors))
    return result


def validate_final_video(
    video_path,
    *,
    min_duration_sec: float = 60.0,
) -> ValidationResult:
    video_path = Path(video_path)
    result = ValidationResult(metrics={"video_path": str(video_path)})
    if not video_path.exists():
        result.fail(f"final video missing: {video_path}")
        raise VideoValidationError("; ".join(result.errors))

    streams = _ffprobe_streams(video_path)
    if not streams:
        result.fail("ffprobe failed (or ffprobe not installed)")
        raise VideoValidationError("; ".join(result.errors))

    summary = _stream_summary(streams)
    if not summary["has_video"] or not summary["has_audio"]:
        result.fail("final video must have both video and audio streams")

    duration = _duration(streams)
    if duration is not None:
        result.metrics["duration_sec"] = duration
        if duration < min_duration_sec:
            result.fail(
                f"final video too short: {duration:.1f}s < {min_duration_sec}s"
            )

    if not result.ok:
        raise VideoValidationError("; ".join(result.errors))
    return result