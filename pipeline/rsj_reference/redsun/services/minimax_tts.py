"""MiniMax Speech API (T2A v2) integration for Red Sun Japan.

Provides cloud-based neural voice synthesis using MiniMax's Speech API (speech-01-turbo / speech-02-hd).
Outputs 16-bit PCM WAV files fully compatible with Remotion, Faster-Whisper, and the Red Sun state machine.
"""
from __future__ import annotations

import base64
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

logger = logging.getLogger("redsun.minimax_tts")

# ─────────────────────────────────────────────────────────────────────────────
# Environment Defaults
# ─────────────────────────────────────────────────────────────────────────────
DEFAULT_MINIMAX_BASE_URL = "https://api.minimax.io/v1"
DEFAULT_MINIMAX_MODEL = "speech-01-turbo"
DEFAULT_MINIMAX_VOICE_ID = "English_male_narrator"
DEFAULT_MINIMAX_SPEED = 0.95

MINIMAX_RECOMMENDED_VOICES: List[str] = [
    "English_male_narrator",
    "English_Deep-VoicedGentleman",
    "English_Trustworth_Man",
    "English_ManWithDeepVoice",
    "English_MatureBoss",
    "English_Steadymentor",
    "English_Graceful_Lady",
    "English_CalmWoman",
]


@dataclass
class MiniMaxTTSResult:
    """Dataclass holding metadata from MiniMax synthesis."""

    wav_path: str
    sample_rate: int
    duration_sec: float
    character_count: int
    chunk_count: int
    speaker: str
    instruct: str
    language: str
    model_id: str


def get_minimax_config() -> Dict[str, Any]:
    """Retrieve current MiniMax settings from environment."""
    return {
        "api_key": os.environ.get("MINIMAX_API_KEY", "").strip(),
        "group_id": os.environ.get("MINIMAX_GROUP_ID", "").strip(),
        "base_url": os.environ.get("MINIMAX_BASE_URL", DEFAULT_MINIMAX_BASE_URL).rstrip("/"),
        "model": os.environ.get("MINIMAX_MODEL", DEFAULT_MINIMAX_MODEL).strip(),
        "voice_id": os.environ.get("MINIMAX_VOICE_ID", DEFAULT_MINIMAX_VOICE_ID).strip(),
        "speed": float(os.environ.get("MINIMAX_SPEED", str(DEFAULT_MINIMAX_SPEED))),
    }


def generate_minimax_audio_chunk(
    text: str,
    voice_id: Optional[str] = None,
    speed: Optional[float] = None,
    model: Optional[str] = None,
    timeout: float = 60.0,
    max_retries: int = 3,
) -> Tuple[np.ndarray, int]:
    """Synthesize a single text chunk via MiniMax T2A v2 API.

    Returns:
        tuple (waveform_numpy_array, sample_rate)
    """
    cfg = get_minimax_config()
    api_key = cfg["api_key"]
    if not api_key:
        raise ValueError(
            "MINIMAX_API_KEY não configurada no ambiente (.env). "
            "Configure a chave da plataforma MiniMax para sintetizar áudio."
        )

    target_model = model or cfg["model"]
    target_voice = voice_id or cfg["voice_id"]
    target_speed = speed if speed is not None else cfg["speed"]

    endpoint = f"{cfg['base_url']}/t2a_v2"
    if cfg["group_id"]:
        endpoint += f"?GroupId={cfg['group_id']}"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": target_model,
        "text": text,
        "stream": False,
        "voice_setting": {
            "voice_id": target_voice,
            "speed": target_speed,
            "vol": 1.0,
            "pitch": 0,
        },
        "audio_setting": {
            "sample_rate": 32000,
            "bitrate": 128000,
            "format": "mp3",
            "channel": 1,
        },
    }

    last_exc: Optional[Exception] = None
    delay = 2.0

    for attempt in range(1, max_retries + 1):
        try:
            logger.info(
                "MiniMax T2A request (attempt %d/%d): model=%s voice=%s speed=%.2f text_len=%d",
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
                        err_body = str(err_json.get("base_resp", {}).get("status_msg") or err_body)
                    except Exception:
                        pass
                    raise RuntimeError(f"MiniMax API HTTP {resp.status_code}: {err_body}")

                data = resp.json()

            base_resp = data.get("base_resp", {})
            status_code = base_resp.get("status_code", 0)
            if status_code != 0:
                status_msg = base_resp.get("status_msg", "Erro desconhecido na API MiniMax")
                raise RuntimeError(f"MiniMax API Error ({status_code}): {status_msg}")

            audio_data = data.get("data", {})
            hex_audio = audio_data.get("audio")
            if not hex_audio:
                raise RuntimeError(f"Nenhum dado de áudio retornado pela MiniMax: {data}")

            # Decode hex or base64 audio payload
            try:
                audio_bytes = bytes.fromhex(hex_audio)
            except ValueError:
                audio_bytes = base64.b64decode(hex_audio)

            with io.BytesIO(audio_bytes) as audio_buf:
                waveform, sr = sf.read(audio_buf, dtype="float32")

            if waveform.ndim > 1:
                waveform = np.mean(waveform, axis=1)

            if len(waveform) == 0:
                raise RuntimeError(f"MiniMax retornou waveform vazio para texto ({len(text)} caracteres)")

            return waveform, sr

        except Exception as exc:
            last_exc = exc
            logger.warning(
                "Falha na tentativa %d/%d com MiniMax TTS (%s): %s",
                attempt,
                max_retries,
                target_model,
                exc,
            )
            if attempt < max_retries:
                time.sleep(delay)
                delay *= 2.0

    raise RuntimeError(f"MiniMax TTS falhou após {max_retries} tentativas: {last_exc}")
