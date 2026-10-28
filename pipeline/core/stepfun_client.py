"""StepFun StepAudio 2.5 client for the CodeToCost pipeline (self-contained).

Deliberately decoupled from the vendored ``redsun`` reference package: importing
``redsun.services`` executes ``redsun/services/__init__.py``, which pulls in
SQLAlchemy and the whole RSJ service stack. The core engine only needs the
StepAudio HTTP contract, so it is implemented here.

StepAudio contract used by this module:

* ``POST {STEPFUN_BASE_URL}/audio/speech``
  body ``{model, input, voice, speed, response_format: "wav"}``
  -> 16-bit PCM WAV bytes.

* ``GET https://api.stepfun.ai/v1/audio/system_voices?model=stepaudio-2.5-tts``
  -> catalog of system voices (the "standard" endpoint; the Coding Plan
  endpoint ``/step_plan/v1`` does not expose it, so the lookup degrades to an
  empty list and the packaged catalog in ``config/stepfun_voices.json`` is
  used instead).

Environment variables (see ``.env``):

* ``STEPFUN_API_KEY``  — bearer token (required).
* ``STEPFUN_BASE_URL`` — defaults to the Coding Plan base URL.
* ``STEPFUN_MODEL``    — defaults to ``stepaudio-2.5-tts``.
* ``STEPFUN_VOICE_ID`` — explicit voice id for the channel (preferred).
* ``STEPFUN_VOICE``    — legacy alias, used only when ``STEPFUN_VOICE_ID`` is unset.
* ``STEPFUN_SPEED``    — speech rate multiplier.
"""
from __future__ import annotations

import io
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional, Tuple

import httpx
import numpy as np
import soundfile as sf

logger = logging.getLogger("codetocost.stepfun")

DEFAULT_STEPFUN_BASE_URL = "https://api.stepfun.ai/step_plan/v1"
DEFAULT_STEPFUN_MODEL = "stepaudio-2.5-tts"
DEFAULT_STEPFUN_VOICE_ID = "soft-spoken-gentleman"
DEFAULT_STEPFUN_SPEED = 0.95

#: Standard (non Coding Plan) base URL — only used for the system-voice catalog.
STANDARD_API_BASE_URL = "https://api.stepfun.ai/v1"

CORE_DIR = Path(__file__).resolve().parent
VOICE_CATALOG_PATH = CORE_DIR / "config" / "stepfun_voices.json"


class StepFunContentBlockedError(RuntimeError):
    """Raised when StepFun blocks the input via content moderation (HTTP 451)."""

    def __init__(self, message: str, status_code: int = 451):
        super().__init__(message)
        self.status_code = status_code


def _clean(value: Optional[str]) -> str:
    """Strip surrounding whitespace and quotes from an env value."""
    if not value:
        return ""
    return value.strip().strip('"').strip("'")


def get_stepfun_config() -> dict[str, Any]:
    """Return the resolved StepFun settings from the environment."""
    return {
        "api_key": _clean(os.environ.get("STEPFUN_API_KEY")),
        "base_url": _clean(os.environ.get("STEPFUN_BASE_URL")) or DEFAULT_STEPFUN_BASE_URL,
        "model": _clean(os.environ.get("STEPFUN_MODEL")) or DEFAULT_STEPFUN_MODEL,
        # Explicit voice id wins; STEPFUN_VOICE is the legacy alias.
        "voice_id": (
            _clean(os.environ.get("STEPFUN_VOICE_ID"))
            or _clean(os.environ.get("STEPFUN_VOICE"))
            or DEFAULT_STEPFUN_VOICE_ID
        ),
        "speed": float(os.environ.get("STEPFUN_SPEED") or DEFAULT_STEPFUN_SPEED),
    }


def resolve_voice_id(explicit: Optional[str] = None) -> str:
    """Resolve the voice id to use.

    Precedence: explicit argument > ``STEPFUN_VOICE_ID`` > ``STEPFUN_VOICE`` >
    packaged default.
    """
    for candidate in (
        explicit,
        os.environ.get("STEPFUN_VOICE_ID"),
        os.environ.get("STEPFUN_VOICE"),
    ):
        cleaned = _clean(candidate)
        if cleaned:
            return cleaned
    return DEFAULT_STEPFUN_VOICE_ID


def load_voice_catalog() -> list[dict[str, Any]]:
    """Load the packaged StepFun voice catalog (``config/stepfun_voices.json``)."""
    if not VOICE_CATALOG_PATH.exists():
        return []
    try:
        raw = json.loads(VOICE_CATALOG_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to parse %s: %s", VOICE_CATALOG_PATH, exc)
        return []
    voices = raw.get("voices") if isinstance(raw, dict) else None
    return [v for v in (voices or []) if isinstance(v, dict) and v.get("id")]


def english_voice_ids() -> list[str]:
    """Return the ids of the English system voices, in catalog order."""
    return [v["id"] for v in load_voice_catalog() if v.get("english")]


def list_system_voices(model: Optional[str] = None) -> list[dict[str, Any]]:
    """Query the live StepFun system-voice catalog.

    Returns an empty list when the account cannot reach the standard endpoint
    (e.g. a Coding Plan key returns HTTP 402) — the packaged catalog remains
    the source of truth in that case.
    """
    cfg = get_stepfun_config()
    if not cfg["api_key"]:
        return []
    target_model = model or cfg["model"]
    url = f"{STANDARD_API_BASE_URL}/audio/system_voices"
    try:
        resp = httpx.get(
            url,
            headers={"Authorization": f"Bearer {cfg['api_key']}"},
            params={"model": target_model},
            timeout=30.0,
        )
    except Exception as exc:
        logger.info("StepFun system_voices unavailable (%s). Using packaged catalog.", exc)
        return []
    if resp.status_code != 200:
        logger.info(
            "StepFun system_voices HTTP %s — using packaged catalog instead.",
            resp.status_code,
        )
        return []
    try:
        payload = resp.json()
    except Exception:
        return []
    details = payload.get("voices-details") or {}
    out: list[dict[str, Any]] = []
    for voice_id in payload.get("voices") or []:
        info = details.get(voice_id, {})
        out.append(
            {
                "id": voice_id,
                "name": info.get("voice_name", ""),
                "description": info.get("voice_description", ""),
                "recommended_scene": info.get("recommended_scene", ""),
            }
        )
    return out


def synthesize_stepfun_chunk(
    text: str,
    voice_id: Optional[str] = None,
    speed: Optional[float] = None,
    model: Optional[str] = None,
    timeout: float = 60.0,
    max_retries: int = 3,
) -> Tuple[np.ndarray, int]:
    """Synthesize one text chunk via StepFun StepAudio.

    Returns:
        tuple (waveform_float32_mono, sample_rate)
    """
    cfg = get_stepfun_config()
    if not cfg["api_key"]:
        raise ValueError(
            "STEPFUN_API_KEY not configured (pipeline/core/.env). "
            "Add the StepFun platform key to synthesize audio."
        )

    target_model = _clean(model) or cfg["model"]
    target_voice = resolve_voice_id(voice_id)
    target_speed = float(speed if speed is not None else cfg["speed"])

    endpoint = f"{cfg['base_url'].rstrip('/')}/audio/speech"
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
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
                "StepFun request (attempt %d/%d): model=%s voice=%s speed=%.2f text_len=%d",
                attempt, max_retries, target_model, target_voice, target_speed, len(text),
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
                lowered = err_body.lower()
                if resp.status_code == 451 or "blocked" in lowered or "content_policy" in lowered:
                    raise StepFunContentBlockedError(
                        f"StepFun API HTTP {resp.status_code}: {err_body}",
                        status_code=resp.status_code,
                    )
                raise RuntimeError(f"StepFun API HTTP {resp.status_code}: {err_body}")

            audio_bytes = resp.content
            if not audio_bytes or len(audio_bytes) < 1000:
                raise RuntimeError(f"StepFun returned no audio ({len(audio_bytes)} bytes)")

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
                "StepFun attempt %d failed (%s). Retrying in %.1fs...",
                attempt, exc, delay,
            )
            if attempt < max_retries:
                time.sleep(delay)
                delay *= 1.5

    raise RuntimeError(
        f"StepFun synthesis failed after {max_retries} attempts: {last_exc}"
    ) from last_exc
