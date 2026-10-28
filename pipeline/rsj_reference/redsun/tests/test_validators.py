"""Phase 0 / task 1.8 + 1.9 + 1.10: density, truncation, manifest completeness."""
from __future__ import annotations

import json
import wave

import pytest

from redsun.exceptions import (
    AudioValidationError,
    TranscriptValidationError,
    VisualPlanValidationError,
)
from redsun.phases.visual_plan import (
    VisualPlan,
    VisualBeat,
    NarrativeScene,
    compute_target_visual_beats,
)
from redsun.validators.audio import (
    validate_audio,
    validate_transcript,
    _normalize_words,
)
from redsun.validators.visual_plan import validate_visual_plan
from redsun.validators.narration import validate_narration


# ── density ─────────────────────────────────────────────────────────────


def test_visual_plan_12_beats_for_780s_rejected():
    plan = VisualPlan(
        target_image_seconds=22,
        max_image_seconds=30,
        audio_duration_sec=783,
        narrative_scenes=[
            NarrativeScene(
                index=1,
                text="long text",
                start_sec=0,
                end_sec=783,
                visual_beats=[
                    VisualBeat(index=i, narrative_scene_index=1, text_excerpt="", duration_sec=783 / 12)
                    for i in range(12)
                ],
            )
        ],
    )
    with pytest.raises(VisualPlanValidationError):
        validate_visual_plan(plan)


def test_visual_plan_36_beats_for_780s_accepted():
    plan = VisualPlan(
        target_image_seconds=22,
        max_image_seconds=30,
        audio_duration_sec=783,
        narrative_scenes=[
            NarrativeScene(
                index=1,
                text="text",
                start_sec=0,
                end_sec=783,
                visual_beats=[
                    VisualBeat(
                        index=i, narrative_scene_index=1, text_excerpt="", duration_sec=22
                    )
                    for i in range(36)
                ],
            )
        ],
    )
    result = validate_visual_plan(plan)
    assert result.ok


def test_compute_target_visual_beats():
    assert compute_target_visual_beats(783, 22) == 36
    assert compute_target_visual_beats(300, 22) == 14
    assert compute_target_visual_beats(60, 22) == 3


# ── audio truncation ──────────────────────────────────────────────────


def test_audio_240s_for_780s_narration_rejected(tmp_path):
    narration = tmp_path / "narration.txt"
    # ~11000 chars → expected ≈ 11000/14 ≈ 786s.
    narration.write_text("a" * 11000, encoding="utf-8")
    audio = tmp_path / "audio.wav"
    n_frames = int(240 * 24000)
    with wave.open(str(audio), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\x00\x00" * n_frames)
    with pytest.raises(AudioValidationError):
        validate_audio(audio, narration)


def test_audio_proportional_duration_passes(tmp_path):
    narration = tmp_path / "narration.txt"
    narration.write_text("a" * 11000, encoding="utf-8")
    audio = tmp_path / "audio.wav"
    # Use 800s of audio (>= expected ≈ 786s + tolerance).
    n_frames = int(800 * 24000)
    with wave.open(str(audio), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        # non-silent: alternating ±1
        frames = bytearray()
        for i in range(n_frames):
            sample = 1000 if i % 2 == 0 else -1000
            frames += sample.to_bytes(2, "little", signed=True)
        w.writeframes(bytes(frames))
    result = validate_audio(audio, narration)
    assert result.ok, result.errors


def test_audio_silent_tail_rejected(tmp_path):
    narration = tmp_path / "narration.txt"
    narration.write_text("a" * 11000, encoding="utf-8")
    audio = tmp_path / "audio.wav"
    n_main = int((780 - 5) * 24000)
    n_tail = int(5 * 24000)
    with wave.open(str(audio), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        # main: alternating (non-silent)
        frames = bytearray()
        for i in range(n_main):
            sample = 1000 if i % 2 == 0 else -1000
            frames += sample.to_bytes(2, "little", signed=True)
        w.writeframes(bytes(frames))
        # tail: silence
        w.writeframes(b"\x00\x00" * n_tail)
    with pytest.raises(AudioValidationError):
        validate_audio(audio, narration)


def test_audio_missing_file_raises(tmp_path):
    with pytest.raises(AudioValidationError):
        validate_audio(tmp_path / "does-not-exist.wav", tmp_path / "narration.txt")


# ── transcript ─ ──────────────────────────────────────────────────────


def test_transcript_low_coverage_rejected(tmp_path):
    narration = tmp_path / "narration.txt"
    narration.write_text("hello world this is narration text", encoding="utf-8")
    transcript = tmp_path / "transcript.json"
    # Only 1 of 6 words covered. coverage = 1/6 ≈ 17% < 85%.
    transcript.write_text(
        json.dumps([{"word": "hello", "start": 0, "end": 0.5}]),
        encoding="utf-8",
    )
    with pytest.raises(TranscriptValidationError):
        validate_transcript(transcript, narration)


def test_transcript_high_coverage_passes(tmp_path):
    narration = tmp_path / "narration.txt"
    narration.write_text("hello world this is narration", encoding="utf-8")
    transcript = tmp_path / "transcript.json"
    transcript.write_text(
        json.dumps(
            [
                {"word": "hello", "start": 0, "end": 0.5},
                {"word": "world", "start": 0.5, "end": 1.0},
                {"word": "this", "start": 1.0, "end": 1.5},
                {"word": "is", "start": 1.5, "end": 1.7},
                {"word": "narration", "start": 1.7, "end": 2.5},
            ]
        ),
        encoding="utf-8",
    )
    result = validate_transcript(transcript, narration)
    assert result.ok


def test_transcript_normalizes_punctuation(tmp_path):
    """Coverage MUST be measured on normalized text — punctuation
    and casing must not cause false positives/negatives.
    """
    narration = tmp_path / "narration.txt"
    narration.write_text("Hello, World!", encoding="utf-8")
    transcript = tmp_path / "transcript.json"
    # Lowercase transcript without punctuation should pass.
    transcript.write_text(
        json.dumps(
            [
                {"word": "hello", "start": 0, "end": 0.5},
                {"word": "world", "start": 0.5, "end": 1.0},
            ]
        ),
        encoding="utf-8",
    )
    result = validate_transcript(transcript, narration)
    assert result.ok


# ── manifest completeness ────────────────────────────────────────────


def test_manifest_missing_field_rejected(tmp_path):
    from redsun.exceptions import ManifestValidationError
    from redsun.validators.manifest import validate_render_manifest

    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "project_id": 1,
                "slug": "kappa",
                "audio_filename": "audio.wav",
                # timestamps_filename missing!
                "image_filenames": [],
                "image_count": 0,
                "duration_seconds": 100,
                "fps": 30,
                "width": 1920,
                "height": 1080,
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "audio.wav").write_bytes(b"")
    (tmp_path / "timestamps.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ManifestValidationError):
        validate_render_manifest(
            manifest, tmp_path / "audio.wav", tmp_path / "timestamps.json", tmp_path
        )


def test_manifest_drift_rejected(tmp_path):
    from redsun.exceptions import ManifestValidationError
    from redsun.validators.manifest import validate_render_manifest

    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "project_id": 1,
                "slug": "kappa",
                "audio_filename": "audio.wav",
                "timestamps_filename": "timestamps.json",
                "image_filenames": [],
                "image_count": 0,
                "duration_seconds": 800,
                "fps": 30,
                "width": 1920,
                "height": 1080,
            }
        ),
        encoding="utf-8",
    )
    audio = tmp_path / "audio.wav"
    n_frames = int(600 * 24000)
    with wave.open(str(audio), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\x00\x00" * n_frames)
    ts = tmp_path / "timestamps.json"
    ts.write_text("[]", encoding="utf-8")
    with pytest.raises(ManifestValidationError):
        validate_render_manifest(manifest, audio, ts, tmp_path)


# ── narration validator ──────────────────────────────────────────────


def test_narration_validator_rejects_markdown(make_workspace):
    ws = make_workspace("kappa")
    ws.narration_path.write_text("hello **bold** world", encoding="utf-8")
    with pytest.raises(Exception):
        from redsun.validators.narration import validate_narration
        validate_narration(ws.narration_path)


def test_narration_validator_passes_clean_text(make_workspace):
    ws = make_workspace("kappa")
    ws.narration_path.write_text(
        # Long enough to pass min_size_bytes=100.
        "A clean narration with no markdown markers whatsoever, "
        "spanning multiple sentences to give the validator enough text.",
        encoding="utf-8",
    )
    from redsun.validators.narration import validate_narration
    result = validate_narration(ws.narration_path)
    assert result.ok