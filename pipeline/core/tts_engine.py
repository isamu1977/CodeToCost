"""TTS engine wrapper for the Red Sun Japan pipeline.

Supports two engines:
  - **kokoro** (default): Kokoro-82M via the `kokoro` package. ~10x faster
    than Qwen3-TTS on Apple Silicon. RTF ~0.34x on M1 Max.
  - **qwen**: Qwen3-TTS CustomVoice via `qwen-tts`. Higher quality ceiling
    but ~3.4x slower than real-time on MPS.

Switch via the ``TTS_ENGINE`` env var (default: ``kokoro``).

Both engines produce 16-bit PCM WAV files and return a ``TTSResult``
with metadata the pipeline persists alongside the audio.
"""
from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

logger = logging.getLogger("redsun.tts")

# ─────────────────────────────────────────────────────────────────────────────
# Config (resolved from env vars set in .env — see .env.example)
# ─────────────────────────────────────────────────────────────────────────────
TTS_PROVIDER = os.environ.get("TTS_PROVIDER") or os.environ.get("TTS_ENGINE", "stepfun")
TTS_ENGINE = TTS_PROVIDER

# StepFun StepAudio config (used when TTS_PROVIDER="stepfun")
STEPFUN_API_KEY = os.environ.get("STEPFUN_API_KEY", "")
STEPFUN_BASE_URL = os.environ.get("STEPFUN_BASE_URL", "https://api.stepfun.ai/step_plan/v1")
STEPFUN_MODEL = os.environ.get("STEPFUN_MODEL", "stepaudio-2.5-tts")
STEPFUN_DEFAULT_VOICE = os.environ.get("STEPFUN_VOICE", "magnetic-voiced-male")
STEPFUN_SPEED = float(os.environ.get("STEPFUN_SPEED", "0.95"))

# MiniMax Speech API config (used when TTS_PROVIDER="minimax")
MINIMAX_API_KEY = os.environ.get("MINIMAX_API_KEY", "")
MINIMAX_GROUP_ID = os.environ.get("MINIMAX_GROUP_ID", "")
MINIMAX_BASE_URL = os.environ.get("MINIMAX_BASE_URL", "https://api.minimax.io/v1")
MINIMAX_MODEL = os.environ.get("MINIMAX_MODEL", "speech-01-turbo")
MINIMAX_DEFAULT_VOICE = os.environ.get("MINIMAX_VOICE_ID", "English_male_narrator")
MINIMAX_SPEED = float(os.environ.get("MINIMAX_SPEED", "0.95"))

# Qwen3-TTS config (used when TTS_PROVIDER="qwen")
QWEN_MODEL_ID = os.environ.get(
    "QWEN_TTS_MODEL", "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"
)
QWEN_DEVICE = os.environ.get("QWEN_TTS_DEVICE", "mps")
QWEN_DEFAULT_SPEAKER = os.environ.get("QWEN_TTS_SPEAKER", "Vivian")
QWEN_DEFAULT_INSTRUCT = os.environ.get(
    "QWEN_TTS_INSTRUCT",
    "Speak in a deep, calm, documentary narrator voice with slow pacing",
)
QWEN_DEFAULT_LANGUAGE = os.environ.get("QWEN_TTS_LANGUAGE", "English")

# Kokoro config (used when TTS_PROVIDER="kokoro")
KOKORO_REPO_ID = os.environ.get("KOKORO_REPO_ID", "hexgrad/Kokoro-82M")
KOKORO_DEFAULT_VOICE = os.environ.get("KOKORO_VOICE", "am_michael")
KOKORO_DEFAULT_LANG = os.environ.get("KOKORO_LANG", "a")  # 'a' = American EN

# Chunk thresholds (shared — all engines benefit from chunking long text)
CHUNK_MAX_CHARS = 900
CHUNK_TARGET_CHARS = 700


@dataclass
class TTSResult:
    """What the pipeline persists per generated utterance."""

    wav_path: str
    sample_rate: int
    duration_sec: float
    character_count: int
    chunk_count: int
    speaker: str
    instruct: str
    language: str
    model_id: str


# ─────────────────────────────────────────────────────────────────────────────
# Lazy model handles
# ─────────────────────────────────────────────────────────────────────────────
_qwen_model = None  # populated on first call to _get_qwen_model()
_kokoro_pipeline = None  # populated on first call to _get_kokoro_pipeline()


# ─────────────────────────────────────────────────────────────────────────────
# Qwen3-TTS engine
# ─────────────────────────────────────────────────────────────────────────────
def _resolve_attn_implementation(device: str) -> str:
    """Return an attention implementation that works on the device."""
    if device == "cuda" or device.startswith("cuda:"):
        return "flash_attention_2"
    try:
        from torch.nn.functional import scaled_dot_product_attention
        _ = scaled_dot_product_attention  # noqa: F841
        return "sdpa"
    except ImportError:
        return "eager"


def _get_qwen_model():
    """Lazy-import the Qwen3-TTS model on first use."""
    global _qwen_model
    if _qwen_model is not None:
        return _qwen_model

    import torch
    from qwen_tts import Qwen3TTSModel

    attn_impl = _resolve_attn_implementation(QWEN_DEVICE)
    logger.info(
        "Loading Qwen3-TTS model=%s device=%s attn=%s",
        QWEN_MODEL_ID, QWEN_DEVICE, attn_impl,
    )
    t0 = time.perf_counter()
    _qwen_model = Qwen3TTSModel.from_pretrained(
        QWEN_MODEL_ID,
        device_map=QWEN_DEVICE,
        dtype=torch.bfloat16,
        attn_implementation=attn_impl,
    )
    logger.info("Qwen3-TTS loaded in %.1fs", time.perf_counter() - t0)
    return _qwen_model


def _generate_qwen(
    text: str, speaker: str, instruct: str, language: str
) -> tuple[np.ndarray, int]:
    """Generate audio with Qwen3-TTS. Returns (waveform, sample_rate)."""
    model = _get_qwen_model()
    wavs, sr = model.generate_custom_voice(
        text=text,
        language=language,
        speaker=speaker,
        instruct=instruct,
    )
    if not wavs or len(wavs[0]) == 0:
        raise RuntimeError(f"Qwen3-TTS produced empty waveform for text ({len(text)} chars)")
    return wavs[0], sr


# ─────────────────────────────────────────────────────────────────────────────
# Kokoro engine
# ─────────────────────────────────────────────────────────────────────────────
def _get_kokoro_pipeline():
    """Lazy-import the Kokoro pipeline on first use."""
    global _kokoro_pipeline
    if _kokoro_pipeline is not None:
        return _kokoro_pipeline

    from kokoro import KPipeline

    logger.info(
        "Loading Kokoro pipeline repo=%s lang=%s",
        KOKORO_REPO_ID, KOKORO_DEFAULT_LANG,
    )
    t0 = time.perf_counter()
    _kokoro_pipeline = KPipeline(lang_code=KOKORO_DEFAULT_LANG, repo_id=KOKORO_REPO_ID)
    logger.info("Kokoro pipeline ready in %.1fs", time.perf_counter() - t0)
    return _kokoro_pipeline


def _generate_kokoro(
    text: str, voice: str, speed: float = 1.0
) -> tuple[np.ndarray, int]:
    """Generate audio with Kokoro-82M. Returns (waveform, sample_rate)."""
    pipeline = _get_kokoro_pipeline()
    # Kokoro uses 'voice' not 'speaker', and 'speed' not 'instruct'
    # The pipeline returns a generator of (graphemes, phonemes, audio)
    audio_parts = []
    for gs, ps, audio in pipeline(text, voice=voice, speed=speed):
        audio_parts.append(audio)
    if not audio_parts:
        raise RuntimeError(f"Kokoro produced empty waveform for text ({len(text)} chars)")
    full_audio = np.concatenate(audio_parts)
    return full_audio, 24000  # Kokoro outputs at 24kHz


# ─────────────────────────────────────────────────────────────────────────────
# Speaker / voice helpers
# ─────────────────────────────────────────────────────────────────────────────
def list_speakers(engine: str | None = None) -> list[str]:
    """Return the list of available speaker/voice names for the active engine."""
    active = (engine or os.environ.get("TTS_PROVIDER") or os.environ.get("TTS_ENGINE", "stepfun")).lower().strip()
    if active == "stepfun":
        from redsun.services.stepfun_tts import STEPFUN_RECOMMENDED_VOICES
        return list(STEPFUN_RECOMMENDED_VOICES)
    elif active == "minimax":
        from redsun.services.minimax_tts import MINIMAX_RECOMMENDED_VOICES
        return list(MINIMAX_RECOMMENDED_VOICES)
    elif active == "kokoro":
        return [
            "af_heart",      # American Female — warm, expressive
            "af_bella",      # American Female — confident
            "af_nicole",     # American Female — soft
            "af_sarah",      # American Female — clear
            "af_sky",        # American Female — bright
            "am_adam",       # American Male — warm
            "am_michael",    # American Male — deep
            "am_governor",   # American Male — authoritative
            "am_eric",       # American Male — lively
            "bf_emma",       # British Female
            "bm_george",     # British Male
            "bm_lewis",      # British Male — young
        ]
    else:
        return [
            "Vivian", "Ryan", "Serena", "Aiden", "Dylan",
            "Eric", "Ananya", "Mia", "Luca",
        ]


# ─────────────────────────────────────────────────────────────────────────────
# Chunking
# ─────────────────────────────────────────────────────────────────────────────
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")


def _chunk_text(text: str, max_chars: int = CHUNK_MAX_CHARS, target_chars: int = CHUNK_TARGET_CHARS) -> list[str]:
    """Split text into chunks under max_chars on sentence boundaries."""
    text = text.strip()
    if len(text) <= max_chars:
        return [text]

    paragraphs = _PARAGRAPH_SPLIT.split(text)
    chunks: list[str] = []
    current = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if len(current) + len(para) + 1 <= max_chars:
            current = (current + " " + para).strip()
            continue

        if current:
            chunks.append(current.strip())
            current = ""

        if len(para) > max_chars:
            sentences = _SENTENCE_SPLIT.split(para)
            piece = ""
            for sent in sentences:
                sent = sent.strip()
                if not sent:
                    continue
                if len(piece) + len(sent) + 1 <= max_chars:
                    piece = (piece + " " + sent).strip()
                else:
                    if piece:
                        chunks.append(piece.strip())
                    piece = sent[:max_chars]
            if piece:
                chunks.append(piece.strip())
                current = ""
        else:
            current = para

    if current:
        chunks.append(current.strip())

    return [c[:max_chars] for c in chunks if c]


# ─────────────────────────────────────────────────────────────────────────────
# Text sanitization — strip markdown/symbols before TTS
# ─────────────────────────────────────────────────────────────────────────────

def _sanitize_for_tts(text: str) -> str:
    """Remove markdown formatting symbols that TTS engines read literally.

    Strips: *asterisks*, _underscores_, **bold**, __underline__,
    #headers, >blockquotes, [links](url), **emphasis words**,
    and other symbols that cause TTS to say "asterisk" aloud.
    """
    import re as _re

    # Remove markdown bold/italic markers around words
    # **word** or *word* → word
    text = _re.sub(r'\*{1,2}([^*]+)\*{1,2}', r'\1', text)
    # __word__ or _word_ → word
    text = _re.sub(r'_{1,2}([^_]+)_{1,2}', r'\1', text)
    # [text](url) → text
    text = _re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    # Remove standalone # headers
    text = _re.sub(r'^#{1,6}\s+', '', text, flags=_re.MULTILINE)
    # Remove > blockquotes
    text = _re.sub(r'^>\s*', '', text, flags=_re.MULTILINE)
    # Remove remaining stray asterisks
    text = text.replace('*', '')
    # Remove backticks
    text = text.replace('`', '')
    # Collapse multiple blank lines
    text = _re.sub(r'\n{3,}', '\n\n', text)

    return text.strip()


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────
def generate_tts(
    text: str,
    output_path: str,
    *,
    speaker: str | None = None,
    instruct: str | None = None,
    language: str | None = None,
    engine: str | None = None,
    provider: str | None = None,
    speed: float | None = None,
    slug: str | None = None,
) -> TTSResult:
    """Generate speech audio and save as 16-bit PCM WAV.

    Args:
        text: Narration text. Will be chunked automatically if long.
        output_path: Absolute path for the resulting WAV file.
        speaker: Speaker/voice name. Defaults to engine-specific default.
        instruct: Style instruction (Qwen only, ignored for Kokoro/MiniMax).
        language: Content language code (Qwen: "English", Kokoro: "a"/"b", MiniMax: "en").
        engine: Override the engine/provider for this call ("kokoro", "minimax", or "qwen").
        provider: Alias for engine ("kokoro" or "minimax").
        speed: Optional speech rate multiplier (e.g. 0.95).
        slug: Optional episode slug to apply project-specific pronunciation overrides.

    Returns:
        TTSResult with metadata the pipeline persists alongside the WAV.
    """
    if not text or not text.strip():
        raise ValueError("text must be non-empty")

    # Strip markdown symbols that TTS engines read literally
    text = _sanitize_for_tts(text)

    # Apply phonetic normalization: canonical term map + CodeToCost channel
    # dictionary (acronyms, currency, scale numbers). Keeps scripts authentic on
    # disk while the TTS engine receives phonetically guided text.
    # Local module — decoupled from the redsun package. Imported at call time so
    # a failure here is loud instead of silently skipping normalization.
    from pronunciation import apply_pronunciation_map

    text = apply_pronunciation_map(text, slug=slug)

    active_engine = (provider or engine or os.environ.get("TTS_PROVIDER") or os.environ.get("TTS_ENGINE", "stepfun")).lower().strip()

    if active_engine == "stepfun":
        chunks = _chunk_text(text, max_chars=800, target_chars=600)
        return _generate_with_stepfun(text, output_path, chunks, speaker, instruct, speed=speed)
    elif active_engine == "minimax":
        chunks = _chunk_text(text, max_chars=1800, target_chars=1200)
        return _generate_with_minimax(text, output_path, chunks, speaker, instruct, speed=speed)
    elif active_engine == "kokoro":
        chunks = _chunk_text(text)
        return _generate_with_kokoro(text, output_path, chunks, speaker, instruct)
    elif active_engine == "qwen":
        chunks = _chunk_text(text)
        return _generate_with_qwen(text, output_path, chunks, speaker, instruct, language)
    else:
        raise ValueError(f"Unknown TTS engine/provider: {active_engine!r}. Use 'stepfun', 'minimax', or 'kokoro'.")


def _generate_with_stepfun(
    text: str,
    output_path: str,
    chunks: list[str],
    speaker: str | None,
    instruct: str | None = None,
    speed: float | None = None,
) -> TTSResult:
    """Generate audio using StepFun StepAudio with Tiered Fallback (Fish Audio -> Edge-TTS)."""
    from redsun.services.resilient_tts import synthesize_chunk_waveform_with_fallback

    voice = speaker or os.environ.get("STEPFUN_VOICE") or STEPFUN_DEFAULT_VOICE
    target_speed = speed if speed is not None else float(os.environ.get("STEPFUN_SPEED", str(STEPFUN_SPEED)))
    target_model = os.environ.get("STEPFUN_MODEL", STEPFUN_MODEL)

    logger.info(
        "TTS run (StepFun with Tiered Fallback): %d chars → %d chunk(s); voice=%s; speed=%.2f",
        len(text),
        len(chunks),
        voice,
        target_speed,
    )

    all_waves: list[np.ndarray] = []
    sample_rate = 24000
    providers_used: list[str] = []

    for idx, chunk in enumerate(chunks, start=1):
        if len(chunks) > 1:
            logger.info("Chunk %d/%d (%d chars)", idx, len(chunks), len(chunk))
        t0 = time.perf_counter()
        waveform, sr, provider_used = synthesize_chunk_waveform_with_fallback(
            chunk,
            speed=target_speed,
            slug=None,
            preferred_engine="stepfun",
        )
        gen_s = time.perf_counter() - t0
        providers_used.append(provider_used)
        logger.info(
            "Chunk %d generated via %s in %.1fs (%.1fs audio, %d Hz)",
            idx,
            provider_used,
            gen_s,
            len(waveform) / sr,
            sr,
        )
        sample_rate = sr
        all_waves.append(waveform)
        # Sutil pausa natural de 200ms entre parágrafos
        if idx < len(chunks):
            pause_samples = int(sr * 0.20)
            all_waves.append(np.zeros(pause_samples, dtype=np.float32))
            if provider_used == "stepfun":
                time.sleep(1.0)

    full_wave = np.concatenate(all_waves) if len(all_waves) > 1 else all_waves[0]

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out_path), full_wave, sample_rate, subtype="PCM_16")

    duration_sec = len(full_wave) / sample_rate
    logger.info(
        "WAV written (StepFun): %s  sr=%d  duration=%.2fs  size=%.1fKB",
        out_path,
        sample_rate,
        duration_sec,
        out_path.stat().st_size / 1024,
    )

    return TTSResult(
        wav_path=str(out_path),
        sample_rate=sample_rate,
        duration_sec=duration_sec,
        character_count=len(text),
        chunk_count=len(chunks),
        speaker=voice,
        instruct=f"model={target_model}, speed={target_speed}",
        language="en",
        model_id=target_model,
    )



def _generate_with_minimax(
    text: str,
    output_path: str,
    chunks: list[str],
    speaker: str | None,
    instruct: str | None = None,
    speed: float | None = None,
) -> TTSResult:
    """Generate audio using MiniMax Speech API (T2A v2)."""
    from redsun.services.minimax_tts import generate_minimax_audio_chunk

    voice = speaker or os.environ.get("MINIMAX_VOICE_ID") or MINIMAX_DEFAULT_VOICE
    target_speed = speed if speed is not None else float(os.environ.get("MINIMAX_SPEED", str(MINIMAX_SPEED)))
    target_model = os.environ.get("MINIMAX_MODEL", MINIMAX_MODEL)

    logger.info(
        "TTS run (MiniMax): %d chars → %d chunk(s); voice=%s; speed=%.2f; model=%s",
        len(text),
        len(chunks),
        voice,
        target_speed,
        target_model,
    )

    all_waves: list[np.ndarray] = []
    sample_rate = 32000

    for idx, chunk in enumerate(chunks, start=1):
        if len(chunks) > 1:
            logger.info("MiniMax chunk %d/%d (%d chars)", idx, len(chunks), len(chunk))
        t0 = time.perf_counter()
        waveform, sr = generate_minimax_audio_chunk(
            chunk,
            voice_id=voice,
            speed=target_speed,
            model=target_model,
        )
        gen_s = time.perf_counter() - t0
        logger.info(
            "MiniMax chunk %d generated in %.1fs (%.1fs audio, %d Hz)",
            idx,
            gen_s,
            len(waveform) / sr,
            sr,
        )
        sample_rate = sr
        all_waves.append(waveform)
        # Sutil pausa natural de 200ms entre parágrafos
        if idx < len(chunks):
            pause_samples = int(sr * 0.20)
            all_waves.append(np.zeros(pause_samples, dtype=np.float32))

    full_wave = np.concatenate(all_waves) if len(all_waves) > 1 else all_waves[0]

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out_path), full_wave, sample_rate, subtype="PCM_16")

    duration_sec = len(full_wave) / sample_rate
    logger.info(
        "WAV written (MiniMax): %s  sr=%d  duration=%.2fs  size=%.1fKB",
        out_path,
        sample_rate,
        duration_sec,
        out_path.stat().st_size / 1024,
    )

    return TTSResult(
        wav_path=str(out_path),
        sample_rate=sample_rate,
        duration_sec=duration_sec,
        character_count=len(text),
        chunk_count=len(chunks),
        speaker=voice,
        instruct=f"model={target_model}, speed={target_speed}",
        language="en",
        model_id=target_model,
    )


def _generate_with_kokoro(
    text: str, output_path: str, chunks: list[str],
    speaker: str | None, instruct: str | None,
) -> TTSResult:
    """Generate audio using Kokoro-82M."""
    voice = speaker or KOKORO_DEFAULT_VOICE

    logger.info(
        "TTS run (Kokoro): %d chars → %d chunk(s); voice=%s",
        len(text), len(chunks), voice,
    )

    all_waves: list[np.ndarray] = []
    sample_rate = 24000

    for idx, chunk in enumerate(chunks, start=1):
        if len(chunks) > 1:
            logger.info("Kokoro chunk %d/%d (%d chars)", idx, len(chunks), len(chunk))
        t0 = time.perf_counter()
        waveform, sr = _generate_kokoro(chunk, voice)
        gen_s = time.perf_counter() - t0
        logger.info("Kokoro chunk %d generated in %.1fs (%.1fs audio)", idx, gen_s, len(waveform) / sr)
        sample_rate = sr
        all_waves.append(waveform)

    full_wave = np.concatenate(all_waves) if len(all_waves) > 1 else all_waves[0]

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out_path), full_wave, sample_rate, subtype="PCM_16")

    duration_sec = len(full_wave) / sample_rate
    logger.info(
        "WAV written: %s  sr=%d  duration=%.2fs  size=%.1fKB",
        out_path, sample_rate, duration_sec, out_path.stat().st_size / 1024,
    )

    return TTSResult(
        wav_path=str(out_path),
        sample_rate=sample_rate,
        duration_sec=duration_sec,
        character_count=len(text),
        chunk_count=len(chunks),
        speaker=voice,
        instruct=instruct or "(Kokoro uses voice, not instruct)",
        language=KOKORO_DEFAULT_LANG,
        model_id=KOKORO_REPO_ID,
    )


def _generate_with_qwen(
    text: str, output_path: str, chunks: list[str],
    speaker: str | None, instruct: str | None, language: str | None,
) -> TTSResult:
    """Generate audio using Qwen3-TTS."""
    speaker = speaker or QWEN_DEFAULT_SPEAKER
    instruct = instruct or QWEN_DEFAULT_INSTRUCT
    language = language or QWEN_DEFAULT_LANGUAGE

    logger.info(
        "TTS run (Qwen): %d chars → %d chunk(s); speaker=%s; lang=%s",
        len(text), len(chunks), speaker, language,
    )

    all_waves: list[np.ndarray] = []
    sample_rate: int | None = None

    for idx, chunk in enumerate(chunks, start=1):
        if len(chunks) > 1:
            logger.info("Qwen chunk %d/%d (%d chars)", idx, len(chunks), len(chunk))
        t0 = time.perf_counter()
        waveform, sr = _generate_qwen(chunk, speaker, instruct, language)
        gen_s = time.perf_counter() - t0
        logger.info("Qwen chunk %d generated in %.1fs", idx, gen_s)
        sample_rate = sr
        all_waves.append(waveform)

    full_wave = np.concatenate(all_waves) if len(all_waves) > 1 else all_waves[0]

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    assert sample_rate is not None, "internal: sample_rate never assigned"
    sf.write(str(out_path), full_wave, sample_rate, subtype="PCM_16")

    duration_sec = len(full_wave) / sample_rate
    logger.info(
        "WAV written: %s  sr=%d  duration=%.2fs  size=%.1fKB",
        out_path, sample_rate, duration_sec, out_path.stat().st_size / 1024,
    )

    return TTSResult(
        wav_path=str(out_path),
        sample_rate=sample_rate,
        duration_sec=duration_sec,
        character_count=len(text),
        chunk_count=len(chunks),
        speaker=speaker,
        instruct=instruct,
        language=language,
        model_id=QWEN_MODEL_ID,
    )


def smoketest(output_path: str = "./tts_smoketest.wav") -> TTSResult:
    """Run a short smoketest with the active engine."""
    test_text = (
        "The age of the samurai was defined not just by the steel of the "
        "katana, but by the absolute devotion to the code of Bushido. "
        "Honor, loyalty, and discipline guided every waking hour."
    )
    return generate_tts(test_text, output_path)
