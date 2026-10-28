"""Resilient Tiered Fallback TTS Engine for Red Sun Japan.

Architectural Hierarchy:
  - TIER 1 (Primary): StepFun StepAudio (stepaudio-2.5-tts, magnetic-voiced-male, speed 0.95)
  - TIER 2 (First Fallback): Fish Audio API (s2.1-pro / v1/tts)
  - TIER 3 (Definitive Failsafe): Microsoft Edge-TTS (en-US-ChristopherNeural, 24kHz PCM mono)

Guarantees 100% resilient delivery against content moderation (HTTP 451) and service outages.
All outputs are standardized in 24,000 Hz, 16-bit PCM Mono.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import io
import logging
import os
from typing import Optional, Tuple

import httpx
import numpy as np
import soundfile as sf
from scipy import signal

from redsun.services.pronunciation import apply_pronunciation_map
from redsun.services.stepfun_tts import (
    StepFunContentBlockedError,
    generate_stepfun_audio_chunk,
    get_stepfun_config,
)

logger = logging.getLogger("redsun.resilient_tts")

DEFAULT_FISH_AUDIO_BASE_URL = "https://api.fish.audio/v1"
DEFAULT_FISH_AUDIO_MODEL = "s2.1-pro"
TARGET_SAMPLE_RATE = 24000


def waveform_to_wav_bytes(waveform: np.ndarray, sample_rate: int = TARGET_SAMPLE_RATE) -> bytes:
    """Convert float32 mono waveform to 16-bit PCM WAV bytes."""
    if waveform.ndim > 1:
        waveform = np.mean(waveform, axis=1)

    # Normalize sample rate if needed
    if sample_rate != TARGET_SAMPLE_RATE:
        num_samples = int(len(waveform) * TARGET_SAMPLE_RATE / sample_rate)
        waveform = signal.resample(waveform, num_samples).astype(np.float32)
        sample_rate = TARGET_SAMPLE_RATE

    bio = io.BytesIO()
    sf.write(bio, waveform, sample_rate, format="WAV", subtype="PCM_16")
    return bio.getvalue()


def wav_bytes_to_waveform(wav_bytes: bytes) -> Tuple[np.ndarray, int]:
    """Decode WAV bytes to float32 mono waveform and sample rate."""
    with io.BytesIO(wav_bytes) as bio:
        waveform, sr = sf.read(bio, dtype="float32")

    if waveform.ndim > 1:
        waveform = np.mean(waveform, axis=1)

    if sr != TARGET_SAMPLE_RATE:
        num_samples = int(len(waveform) * TARGET_SAMPLE_RATE / sr)
        waveform = signal.resample(waveform, num_samples).astype(np.float32)
        sr = TARGET_SAMPLE_RATE

    return waveform, sr


def _synthesize_edge_tts_chunk(text: str, voice: str = "en-US-ChristopherNeural") -> Tuple[np.ndarray, int]:
    """Tier 3: Synthesize text chunk via Microsoft Edge-TTS (en-US-ChristopherNeural)."""
    import edge_tts

    async def _synth():
        comm = edge_tts.Communicate(text=text, voice=voice)
        buf = bytearray()
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                buf.extend(chunk["data"])
        return bytes(buf)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        audio_bytes = pool.submit(lambda: asyncio.run(_synth())).result()

    if not audio_bytes or len(audio_bytes) < 500:
        raise RuntimeError(f"Edge-TTS returned empty or invalid audio data ({len(audio_bytes)} bytes)")

    with io.BytesIO(audio_bytes) as bio:
        waveform, sr = sf.read(bio, dtype="float32")

    if waveform.ndim > 1:
        waveform = np.mean(waveform, axis=1)

    if sr != TARGET_SAMPLE_RATE:
        num_samples = int(len(waveform) * TARGET_SAMPLE_RATE / sr)
        waveform = signal.resample(waveform, num_samples).astype(np.float32)
        sr = TARGET_SAMPLE_RATE

    return waveform, sr


def _synthesize_fish_audio_chunk(
    text: str,
    api_key: str,
    base_url: str = DEFAULT_FISH_AUDIO_BASE_URL,
    model: str = DEFAULT_FISH_AUDIO_MODEL,
    timeout: float = 45.0,
) -> Tuple[np.ndarray, int]:
    """Tier 2: Synthesize text chunk via Fish Audio API (v1/tts)."""
    endpoint = f"{base_url.rstrip('/')}/tts"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "text": text,
        "format": "wav",
        "latency": "normal",
    }

    ref_id = os.environ.get("FISH_AUDIO_REFERENCE_ID", "").strip().strip('"').strip("'")
    if ref_id:
        payload["reference_id"] = ref_id

    with httpx.Client(timeout=timeout) as client:
        resp = client.post(endpoint, headers=headers, json=payload)
        if resp.status_code != 200:
            err_text = resp.text[:200]
            raise RuntimeError(f"Fish Audio HTTP {resp.status_code}: {err_text}")

        audio_bytes = resp.content

    if not audio_bytes or len(audio_bytes) < 500:
        raise RuntimeError(f"Fish Audio returned insufficient audio data ({len(audio_bytes)} bytes)")

    with io.BytesIO(audio_bytes) as bio:
        waveform, sr = sf.read(bio, dtype="float32")

    if waveform.ndim > 1:
        waveform = np.mean(waveform, axis=1)

    if sr != TARGET_SAMPLE_RATE:
        num_samples = int(len(waveform) * TARGET_SAMPLE_RATE / sr)
        waveform = signal.resample(waveform, num_samples).astype(np.float32)
        sr = TARGET_SAMPLE_RATE

    return waveform, sr


def synthesize_single_engine_chunk(
    text: str,
    engine: str,
    voice: Optional[str] = None,
    speed: float = 0.95,
    slug: Optional[str] = None,
) -> Tuple[np.ndarray, int]:
    """Synthesize a single text chunk with ONE specific engine without fallback.

    Supported engines: 'stepfun', 'fish_audio' (or 'fishaudio'), 'edgetts' (or 'edge-tts').
    Raises appropriate exceptions if the engine fails (e.g., StepFunContentBlockedError, RuntimeError).
    """
    guided_text = apply_pronunciation_map(text, slug=slug) if slug else text
    engine_norm = engine.lower().strip()

    if engine_norm in ("stepfun", "stepaudio", "step"):
        waveform, sr = generate_stepfun_audio_chunk(
            text=guided_text,
            voice_id=voice or "magnetic-voiced-male",
            speed=speed,
            model="stepaudio-2.5-tts",
        )
        if sr != TARGET_SAMPLE_RATE:
            num_samples = int(len(waveform) * TARGET_SAMPLE_RATE / sr)
            waveform = signal.resample(waveform, num_samples).astype(np.float32)
            sr = TARGET_SAMPLE_RATE
        return waveform, sr

    elif engine_norm in ("fish_audio", "fishaudio", "fish-audio", "fish"):
        fish_api_key = os.environ.get("FISH_AUDIO_API_KEY", "").strip().strip('"').strip("'")
        if not fish_api_key:
            raise RuntimeError("FISH_AUDIO_API_KEY não configurada no ambiente (.env)")
        fish_base_url = os.environ.get("FISH_AUDIO_BASE_URL", DEFAULT_FISH_AUDIO_BASE_URL).strip().strip('"').strip("'")
        fish_model = os.environ.get("FISH_AUDIO_MODEL", DEFAULT_FISH_AUDIO_MODEL).strip().strip('"').strip("'")
        return _synthesize_fish_audio_chunk(
            text=guided_text,
            api_key=fish_api_key,
            base_url=fish_base_url,
            model=fish_model,
        )

    elif engine_norm in ("edgetts", "edge-tts", "edge_tts", "edge"):
        target_voice = voice if (voice and "Neural" in voice) else "en-US-ChristopherNeural"
        return _synthesize_edge_tts_chunk(text=guided_text, voice=target_voice)

    else:
        raise ValueError(f"Motor TTS desconhecido: '{engine}'. Motores suportados: stepfun, fish_audio, edgetts.")


def synthesize_chunk_waveform_with_fallback(
    text: str,
    speed: float = 0.95,
    slug: Optional[str] = None,
    preferred_engine: Optional[str] = None,
) -> Tuple[np.ndarray, int, str]:
    """Execute Tiered Fallback synthesis returning a numpy float32 waveform and sample rate (24000).

    Returns:
        tuple (waveform_array, sample_rate, provider_used)
    """
    guided_text = apply_pronunciation_map(text, slug=slug) if slug else text
    engine_pref = (preferred_engine or os.environ.get("TTS_PROVIDER", "stepfun")).lower().strip()

    # Direct Edge-TTS bypass if explicitly selected
    if engine_pref in ("edgetts", "edge-tts", "edge_tts"):
        waveform, sr = _synthesize_edge_tts_chunk(guided_text)
        return waveform, sr, "edgetts"

    # Direct Fish Audio start if explicitly selected
    if engine_pref in ("fish_audio", "fishaudio", "fish-audio", "fish"):
        fish_api_key = os.environ.get("FISH_AUDIO_API_KEY", "").strip().strip('"').strip("'")
        if fish_api_key:
            fish_base_url = os.environ.get("FISH_AUDIO_BASE_URL", DEFAULT_FISH_AUDIO_BASE_URL).strip().strip('"').strip("'")
            fish_model = os.environ.get("FISH_AUDIO_MODEL", DEFAULT_FISH_AUDIO_MODEL).strip().strip('"').strip("'")
            try:
                waveform, sr = _synthesize_fish_audio_chunk(
                    text=guided_text,
                    api_key=fish_api_key,
                    base_url=fish_base_url,
                    model=fish_model,
                )
                logger.info("[TTS Cascata] Direct synthesis successful via Fish Audio")
                return waveform, sr, "fish_audio"
            except Exception as fish_exc:
                logger.warning(
                    "[TTS Cascata] Fish Audio failed (%s). Falling back to Tier 3 (Edge-TTS)...",
                    fish_exc,
                )
        else:
            logger.warning("[TTS Cascata] Fish Audio API Key not configured. Falling back to Tier 3 (Edge-TTS)...")

        waveform, sr = _synthesize_edge_tts_chunk(guided_text)
        return waveform, sr, "edgetts"

    # ── TIER 1: StepFun StepAudio ───────────────────────────────────────────
    try:
        waveform, sr = generate_stepfun_audio_chunk(
            text=guided_text,
            voice_id="magnetic-voiced-male",
            speed=speed,
            model="stepaudio-2.5-tts",
        )
        if sr != TARGET_SAMPLE_RATE:
            num_samples = int(len(waveform) * TARGET_SAMPLE_RATE / sr)
            waveform = signal.resample(waveform, num_samples).astype(np.float32)
            sr = TARGET_SAMPLE_RATE
        return waveform, sr, "stepfun"

    except Exception as exc:
        exc_str = str(exc)
        status_code = getattr(exc, "status_code", 451 if "451" in exc_str else 500)
        logger.warning(
            "[TTS Cascata] StepFun bloqueou ou falhou (HTTP %s): %s. Acionando Tier 2 (Fish Audio)...",
            status_code,
            exc_str[:120],
        )

    # ── TIER 2: Fish Audio API ──────────────────────────────────────────────
    fish_api_key = os.environ.get("FISH_AUDIO_API_KEY", "").strip().strip('"').strip("'")
    if fish_api_key:
        fish_base_url = os.environ.get("FISH_AUDIO_BASE_URL", DEFAULT_FISH_AUDIO_BASE_URL).strip().strip('"').strip("'")
        fish_model = os.environ.get("FISH_AUDIO_MODEL", DEFAULT_FISH_AUDIO_MODEL).strip().strip('"').strip("'")
        try:
            waveform, sr = _synthesize_fish_audio_chunk(
                text=guided_text,
                api_key=fish_api_key,
                base_url=fish_base_url,
                model=fish_model,
            )
            logger.info("[TTS Cascata] Síntese concluída com sucesso via Tier 2 (Fish Audio)")
            return waveform, sr, "fish_audio"
        except Exception as fish_exc:
            logger.warning(
                "[TTS Cascata] Fish Audio indisponível (%s). Acionando Tier 3 (Edge-TTS)...",
                fish_exc,
            )
    else:
        logger.info("[TTS Cascata] Fish Audio API Key não configurada. Pulando para Tier 3 (Edge-TTS)...")

    # ── TIER 3: Microsoft Edge-TTS (Definitive Failsafe) ─────────────────────
    try:
        waveform, sr = _synthesize_edge_tts_chunk(guided_text)
        logger.info("[TTS Cascata] Síntese concluída com sucesso via Tier 3 (Edge-TTS)")
        return waveform, sr, "edgetts"
    except Exception as edge_exc:
        logger.error("[TTS Cascata] Erro crítico no Tier 3 (Edge-TTS): %s", edge_exc)
        raise RuntimeError(f"Todos os tiers de TTS falharam para o chunk: {edge_exc}") from edge_exc


def synthesize_chunk_with_fallback(
    text: str,
    speed: float = 0.95,
    slug: Optional[str] = None,
    preferred_engine: Optional[str] = None,
) -> Tuple[bytes, str]:
    """Execute Tiered Fallback synthesis returning standard 24kHz 16-bit PCM WAV bytes and provider name.

    Returns:
        tuple (wav_bytes, provider_used)
    """
    waveform, sr, provider = synthesize_chunk_waveform_with_fallback(
        text=text,
        speed=speed,
        slug=slug,
        preferred_engine=preferred_engine,
    )
    wav_bytes = waveform_to_wav_bytes(waveform, sample_rate=sr)
    return wav_bytes, provider
