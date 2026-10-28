"""StepFun StepAudio 2.5 (stepaudio-2.5-tts) integration for Red Sun Japan.

Provides cloud-based neural voice synthesis using StepFun's StepAudio API.
Outputs 16-bit PCM WAV files fully compatible with Remotion, Faster-Whisper, and the Red Sun state machine.
"""
from __future__ import annotations

import io
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
import numpy as np
import soundfile as sf

logger = logging.getLogger("redsun.stepfun_tts")

# ─────────────────────────────────────────────────────────────────────────────
# Environment Defaults
# ─────────────────────────────────────────────────────────────────────────────
DEFAULT_STEPFUN_BASE_URL = "https://api.stepfun.ai/step_plan/v1"
DEFAULT_STEPFUN_MODEL = "stepaudio-2.5-tts"
DEFAULT_STEPFUN_VOICE = "magnetic-voiced-male"
DEFAULT_STEPFUN_SPEED = 0.95

STEPFUN_RECOMMENDED_VOICES: List[str] = [
    "magnetic-voiced-male",    # Voz oficial homologada (Cadência documental aveludada / solene)
    "soft-spoken-gentleman",   # Voz masculina calma e intimista
    "zixinnansheng",           # Voz masculina confiante
    "vibrant-youth",           # Jovem vibrante / narrativo
]


class StepFunContentBlockedError(RuntimeError):
    """Raised when StepFun blocks input due to content moderation (HTTP 451)."""
    def __init__(self, message: str, status_code: int = 451):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class StepFunTTSResult:
    """Dataclass holding metadata from StepFun synthesis."""

    wav_path: str
    sample_rate: int
    duration_sec: float
    character_count: int
    chunk_count: int
    speaker: str
    instruct: str
    language: str
    model_id: str


def get_stepfun_config() -> Dict[str, Any]:
    """Retrieve current StepFun settings from environment."""
    return {
        "api_key": os.environ.get("STEPFUN_API_KEY", "").strip().strip('"').strip("'"),
        "base_url": (
            os.environ.get("STEPFUN_BASE_URL", DEFAULT_STEPFUN_BASE_URL)
            .strip()
            .strip('"')
            .strip("'")
            .rstrip("/")
        ),
        "model": (
            os.environ.get("STEPFUN_MODEL", DEFAULT_STEPFUN_MODEL)
            .strip()
            .strip('"')
            .strip("'")
        ),
        "voice": (
            os.environ.get("STEPFUN_VOICE", DEFAULT_STEPFUN_VOICE)
            .strip()
            .strip('"')
            .strip("'")
        ),
        "speed": float(os.environ.get("STEPFUN_SPEED", str(DEFAULT_STEPFUN_SPEED))),
    }


def generate_stepfun_audio_chunk(
    text: str,
    voice_id: Optional[str] = None,
    speed: Optional[float] = None,
    model: Optional[str] = None,
    timeout: float = 60.0,
    max_retries: int = 3,
) -> Tuple[np.ndarray, int]:
    """Synthesize a single text chunk via StepFun StepAudio (POST /audio/speech).

    Returns:
        tuple (waveform_numpy_array, sample_rate)
    """
    cfg = get_stepfun_config()
    api_key = cfg["api_key"]
    if not api_key:
        raise ValueError(
            "STEPFUN_API_KEY não configurada no ambiente (.env). "
            "Configure a chave da plataforma StepFun para sintetizar áudio."
        )

    target_model = model or cfg["model"]
    target_voice = voice_id or cfg["voice"]
    target_speed = speed if speed is not None else cfg["speed"]

    endpoint = f"{cfg['base_url']}/audio/speech"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": target_model,
        "input": text,
        "voice": target_voice,
        "speed": target_speed,
        "response_format": "wav",
    }

    last_exc: Optional[Exception] = None
    delay = 2.0

    for attempt in range(1, max_retries + 1):
        try:
            logger.info(
                "StepFun StepAudio request (attempt %d/%d): model=%s voice=%s speed=%.2f text_len=%d",
                attempt,
                max_retries,
                target_model,
                target_voice,
                target_speed,
                len(text),
            )
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(endpoint, headers=headers, json=payload)
                if resp.status_code != 200:
                    err_body = resp.text
                    try:
                        err_json = resp.json()
                        err_body = str(err_json.get("error", {}).get("message") or err_body)
                    except Exception:
                        pass
                    if resp.status_code == 451 or "blocked" in err_body.lower() or "content_policy" in err_body.lower():
                        logger.warning(
                            "StepFun content moderation block detectado (HTTP %d): %s",
                            resp.status_code,
                            err_body,
                        )
                        raise StepFunContentBlockedError(
                            f"StepFun API HTTP {resp.status_code}: {err_body}",
                            status_code=resp.status_code,
                        )
                    raise RuntimeError(f"StepFun API HTTP {resp.status_code}: {err_body}")

                audio_bytes = resp.content

            if not audio_bytes or len(audio_bytes) < 1000:
                raise RuntimeError(f"Nenhum dado de áudio retornado pela StepFun ({len(audio_bytes)} bytes)")

            with io.BytesIO(audio_bytes) as audio_buf:
                waveform, sr = sf.read(audio_buf, dtype="float32")

            if waveform.ndim > 1:
                waveform = np.mean(waveform, axis=1)

            return waveform, sr

        except StepFunContentBlockedError:
            raise
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "StepFun request attempt %d failed (%s). Retrying in %.1fs...",
                attempt,
                exc,
                delay,
            )
            if attempt < max_retries:
                time.sleep(delay)
                delay *= 1.5

    raise RuntimeError(
        f"Falha na síntese StepFun após {max_retries} tentativas: {last_exc}"
    ) from last_exc
