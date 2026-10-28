"""Audio + transcript quality gates.

Phase 4 / `audio-quality-gate` spec implementation.
"""
from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Optional

from .. import defaults
from ..exceptions import (
    AudioValidationError,
    TranscriptValidationError,
)
from . import ValidationResult


_NON_WORD_RE = re.compile(r"[^a-z0-9\s]+")
_WS_RE = re.compile(r"\s+")


def _normalize_words(text: str) -> set[str]:
    """Lowercase, strip punctuation, collapse whitespace, return words."""
    s = text.lower()
    s = _NON_WORD_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    return set(s.split()) if s else set()


def validate_audio(
    audio_path,
    narration_path: Optional[Path] = None,
    *,
    min_duration_sec: int = defaults.MIN_AUDIO_DURATION_SEC,
    chars_per_second: int = defaults.CHARS_PER_SECOND,
    silent_tail_threshold_sec: float = defaults.SILENT_TAIL_THRESHOLD_SEC,
) -> ValidationResult:
    """Deterministic validator for a generated WAV.

    Rejects:
    - missing file
    - non-decodable WAV
    - too-short duration (against `min_duration_sec` and against the
      narration-derived expected minimum)
    - silent tail at the end of the file (>= `silent_tail_threshold_sec`)

    Always returns a `ValidationResult`; raises `AudioValidationError`
    if the audio file is missing entirely (cannot even read it).
    """
    audio_path = Path(audio_path)
    result = ValidationResult(metrics={"audio_path": str(audio_path)})

    if not audio_path.exists():
        raise AudioValidationError(f"audio file missing: {audio_path}")

    duration, sample_rate = _read_wav_duration(audio_path)
    result.metrics["duration_sec"] = duration
    result.metrics["sample_rate"] = sample_rate

    if duration < min_duration_sec:
        result.fail(
            f"audio too short: {duration:.1f}s < minimum {min_duration_sec}s"
        )

    expected_min = 0.0
    if narration_path is not None and Path(narration_path).exists():
        narration_text = Path(narration_path).read_text(encoding="utf-8")
        narration_len = len(narration_text.strip())
        expected_min = narration_len / max(chars_per_second, 1)
        result.metrics["expected_min_sec"] = expected_min
        result.metrics["narration_chars"] = narration_len

        if duration + 1.0 < expected_min:
            result.fail(
                f"audio truncated: {duration:.1f}s << "
                f"expected≈{expected_min:.1f}s "
                f"({narration_len} chars / {chars_per_second} cps)"
            )

    if duration > silent_tail_threshold_sec:
        tail_amp = _tail_rms(audio_path, sample_rate)
        result.metrics["tail_rms"] = tail_amp
        if tail_amp == 0.0:
            result.fail(
                f"audio has silent tail >= {silent_tail_threshold_sec}s"
            )

    if not result.ok:
        raise AudioValidationError("; ".join(result.errors))
    return result


def _read_wav_duration(audio_path: Path) -> tuple[float, int]:
    """Read (duration, sample_rate) from a WAV file.

    Uses the stdlib `wave` module to avoid numpy/soundfile deps in
    tests. Falls back to (0.0, 0) on failure.
    """
    import wave

    try:
        with wave.open(str(audio_path), "rb") as w:
            frames = w.getnframes()
            rate = w.getframerate()
            return (frames / float(rate) if rate else 0.0, rate)
    except Exception:
        return (0.0, 0)


def _tail_rms(audio_path: Path, sample_rate: int) -> float:
    """RMS amplitude over the last `silent_tail_threshold_sec` seconds.

    Returns 0.0 if the file is unreadable. Decoded as 16-bit signed
    little-endian PCM (the WAV subtype used by `tts_engine.py`).
    """
    import struct
    import wave

    if sample_rate <= 0:
        return 0.0
    try:
        with wave.open(str(audio_path), "rb") as w:
            n_channels = w.getnchannels()
            sampwidth = w.getsampwidth()
            tail_frames = int(defaults.SILENT_TAIL_THRESHOLD_SEC * sample_rate)
            if tail_frames <= 0:
                return 0.0
            # = last N frames (may not be aligned with the actual
            # silence window; we read the whole thing and slice)
            raw = w.readframes(w.getnframes())
    except Exception:
        return 0.0

    if not raw:
        return 0.0
    if sampwidth != 2:
        return 0.0

    samples_per_frame = n_channels
    total_samples = len(raw) // 2
    if total_samples == 0:
        return 0.0

    n_tail_samples = tail_frames * samples_per_frame
    n_tail_samples = min(n_tail_samples, total_samples)
    start_byte = (total_samples - n_tail_samples) * 2
    tail_bytes = raw[start_byte:]
    n_tail = len(tail_bytes) // 2
    if n_tail == 0:
        return 0.0

    fmt = "<" + "h" * n_tail
    try:
        ints = struct.unpack(fmt, tail_bytes[: n_tail * 2])
    except Exception:
        return 0.0

    sq_sum = sum(s * s for s in ints)
    rms = math.sqrt(sq_sum / n_tail) / 32768.0
    return rms


# ── Transcript ──────────────────────────────────────────────────────────


def validate_transcript(
    transcript_path,
    narration_path,
    *,
    min_coverage_ratio: float = defaults.MIN_COVERAGE_RATIO,
) -> ValidationResult:
    """Compare Whisper transcript against narration.txt.

    Rejects when normalized-word coverage is below
    `min_coverage_ratio`. Always returns a ValidationResult;
    raises `TranscriptValidationError` only when files are
    missing entirely.
    """
    transcript_path = Path(transcript_path)
    narration_path = Path(narration_path)

    if not transcript_path.exists():
        raise TranscriptValidationError(
            f"transcript file missing: {transcript_path}"
        )
    if not narration_path.exists():
        raise TranscriptValidationError(
            f"narration file missing: {narration_path}"
        )

    import json as _json

    try:
        data = _json.loads(transcript_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise TranscriptValidationError(
            f"transcript not valid JSON: {exc}"
        ) from exc

    if isinstance(data, dict):
        words_iter = data.get("words") or data.get("segments") or []
    else:
        words_iter = data

    transcript_words: set[str] = set()
    for entry in words_iter:
        if isinstance(entry, dict):
            w = entry.get("word") or entry.get("text") or ""
        else:
            w = str(entry)
        transcript_words |= _normalize_words(w)

    narration_text = narration_path.read_text(encoding="utf-8")
    narration_words = _normalize_words(narration_text)

    if not narration_words:
        return ValidationResult(
            ok=False,
            errors=["narration is empty; cannot compute coverage"],
            metrics={"coverage": 0.0},
        )

    matched = transcript_words & narration_words
    coverage = len(matched) / max(len(narration_words), 1)
    result = ValidationResult(
        metrics={
            "coverage": coverage,
            "transcript_words": len(transcript_words),
            "narration_words": len(narration_words),
            "matched_words": len(matched),
        }
    )
    if coverage < min_coverage_ratio:
        result.fail(
            f"transcript coverage {coverage:.0%} < "
            f"minimum {min_coverage_ratio:.0%} "
            f"({len(matched)}/{len(narration_words)} words)"
        )
    if not result.ok:
        raise TranscriptValidationError("; ".join(result.errors))
    return result