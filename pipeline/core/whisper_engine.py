"""Faster-Whisper transcription engine wrapper for the Red Sun Japan pipeline.

Phase 4:

- Single source of truth for *how* the pipeline transcribes audio to word-level
  timestamps. The rest of the project (``main.py``, future Remotion wiring)
  only ever calls functions exported from this module.

- Loads the model lazily on first call. Subsequent calls reuse the handle
  (~0.5s to re-resolve from disk cache vs. ~11s cold load on M1 Max).

- Writes Remotion-friendly JSON: ``[{"word", "start", "end"}, ...]``
  in chronological order. Remotion's ``SamuraiVideo`` reads this exact shape.

What's NOT here yet (Phase 5+):

- Diarization (multi-speaker). We only ship single-speaker narration for now.
- Translation. Whisper can translate too, but our pipeline generates the
  English script via the LLM engine, not by translating Japanese audio.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("redsun.whisper")

# ─────────────────────────────────────────────────────────────────────────────
# Config (resolved from env vars set in .env — see .env.example)
# ─────────────────────────────────────────────────────────────────────────────
MODEL_SIZE = os.environ.get("WHISPER_MODEL_SIZE", "base")
DEVICE = os.environ.get("WHISPER_DEVICE", "cpu")
COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE_TYPE", "int8")
DEFAULT_LANGUAGE = os.environ.get("WHISPER_LANGUAGE", "en")

# VAD (voice activity detection) settings. Whisper hallucinates on silence;
# VAD segments audio first to avoid that. min_silence_duration_ms=300 is
# a good default for narration (pauses between sentences).
VAD_FILTER = os.environ.get("WHISPER_VAD_FILTER", "1") == "1"
VAD_MIN_SILENCE_MS = int(os.environ.get("WHISPER_VAD_MIN_SILENCE_MS", "300"))


@dataclass
class TranscriptionResult:
    """What the pipeline persists per transcribed audio."""

    timestamps_path: str
    word_count: int
    segment_count: int
    duration_sec: float
    elapsed_sec: float
    model_size: str
    language: str


# ─────────────────────────────────────────────────────────────────────────────
# Lazy model handle
# ─────────────────────────────────────────────────────────────────────────────
_model = None  # populated on first call to _get_model()


def _get_model():
    """Lazily load faster-whisper on first call.

    Why lazy: the FastAPI process should not block on a 75MB model download
    just because someone hit /projects. Only callers that actually need
    transcription pay the cold-load cost.
    """
    global _model
    if _model is None:
        from faster_whisper import WhisperModel  # type: ignore

        logger.info(
            "Loading faster-whisper model=%s device=%s compute_type=%s",
            MODEL_SIZE, DEVICE, COMPUTE_TYPE,
        )
        t0 = time.perf_counter()
        _model = WhisperModel(
            MODEL_SIZE,
            device=DEVICE,
            compute_type=COMPUTE_TYPE,
        )
        logger.info(
            "faster-whisper %s loaded in %.1fs",
            MODEL_SIZE, time.perf_counter() - t0,
        )
    return _model


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────
def transcribe_audio(
    audio_path: str,
    timestamps_path: str,
    *,
    language: str | None = None,
    reference_text: str | None = None,
) -> TranscriptionResult:
    """Transcribe an audio file to word-level timestamps JSON.

    Args:
        audio_path: Path to input audio (WAV, MP3, anything ffmpeg reads).
        timestamps_path: Where to write the JSON.
        language: ISO code ("en", "ja", "pt-BR") or None to auto-detect.
                  Auto-detect is slower; pre-specifying is the common case.
        reference_text: Authoritative script text (e.g. from script_en.md).
                        When provided, runs post-Whisper forced alignment so that
                        timestamps are taken from Whisper, but exact spelling and
                        Japanese entity casing come 100% from the verified script.

    Returns:
        TranscriptionResult with paths and metrics.

    Output JSON shape (Remotion-compatible):
        [
          {"word": "The", "start": 0.12, "end": 0.45},
          {"word": "age",  "start": 0.45, "end": 0.78},
          ...
        ]
    """
    audio = Path(audio_path)
    if not audio.exists():
        raise FileNotFoundError(f"audio not found: {audio_path}")

    model = _get_model()
    lang = language or DEFAULT_LANGUAGE

    logger.info(
        "Transcribing: audio=%s language=%s vad=%s",
        audio, lang, VAD_FILTER,
    )
    t0 = time.perf_counter()

    segments, info = model.transcribe(
        str(audio),
        word_timestamps=True,
        language=lang if lang != "auto" else None,
        vad_filter=VAD_FILTER,
        vad_parameters=(
            dict(min_silence_duration_ms=VAD_MIN_SILENCE_MS) if VAD_FILTER else None
        ),
    )

    # Faster-whisper returns a generator — must iterate to actually transcribe.
    word_list: list[dict] = []
    segment_count = 0
    for seg in segments:
        segment_count += 1
        if not seg.words:
            continue
        for w in seg.words:
            # Skip words with no timing (rare; happens at segment boundaries
            # when VAD splits mid-token).
            if w.start is None or w.end is None:
                continue
            word_list.append({
                "word": w.word.strip(),
                "start": round(float(w.start), 3),
                "end": round(float(w.end), 3),
            })

    # Forced alignment: if reference script text is provided, calibrate word spellings
    # so that timestamps come from Whisper, but exact spelling/casing comes 100% from script_en.md.
    if reference_text and reference_text.strip():
        try:
            from redsun.services.subtitle_aligner import align_whisper_to_script
            aligned = align_whisper_to_script(word_list, reference_text)
            logger.info("Forced alignment applied: aligned %d whisper words to %d script tokens.", len(word_list), len(aligned))
            word_list = aligned
        except Exception as exc:
            logger.warning("Forced alignment failed: %s. Using raw Whisper words.", exc)

    elapsed = time.perf_counter() - t0

    # Write JSON
    out = Path(timestamps_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        json.dump(word_list, f, ensure_ascii=False, indent=2)

    logger.info(
        "Transcribed: %d words, %d segments, %.1fs audio in %.1fs (%.2fx realtime) -> %s",
        len(word_list), segment_count, info.duration, elapsed,
        elapsed / info.duration if info.duration else 0.0, out,
    )

    return TranscriptionResult(
        timestamps_path=str(out),
        word_count=len(word_list),
        segment_count=segment_count,
        duration_sec=float(info.duration),
        elapsed_sec=elapsed,
        model_size=MODEL_SIZE,
        language=lang,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Smoke test — run as `python whisper_engine.py` to validate the model loads
# and a real audio file transcribes end-to-end.
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Default: transcribe the Phase 2 Yuki-onna sample.
    audio = Path(__file__).parent / "storage" / "1" / "audio_en.wav"
    out = audio.parent / "timestamps.json"

    if not audio.exists():
        print(f"FAIL: {audio} not found.", file=sys.stderr)
        print("  Run POST /projects/1/audio first to generate the WAV.", file=sys.stderr)
        sys.exit(1)

    result = transcribe_audio(str(audio), str(out))
    print(f"\n✓ {result.word_count} words in {result.duration_sec:.1f}s audio")
    print(f"  took {result.elapsed_sec:.1f}s ({result.elapsed_sec / result.duration_sec:.2f}x realtime)")
    print(f"  -> {result.timestamps_path}")
