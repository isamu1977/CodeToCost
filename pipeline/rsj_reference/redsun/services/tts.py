"""HTTP-backed TTS service for the wired pipeline.

Calls the FastAPI endpoint ``POST /projects/{id}/audio`` to generate
TTS audio via Kokoro.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import requests

logger = logging.getLogger("redsun.services.tts")


@dataclass
class TTSResult:
    """Result from a TTS generation call."""

    audio_path: Path
    tts_engine: str
    voice: str
    duration_sec: float
    sample_rate: int


class TTSService:
    """HTTP-backed TTS driver.

    Construct via ``TTSService.from_context(ctx)``.
    """

    def __init__(self, base_url: str, *, timeout: int = 600) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    @classmethod
    def from_context(cls, context, *, timeout: int = 600) -> "TTSService":
        base_url = (
            getattr(getattr(context, "llm", None), "base_url", None)
            or "http://127.0.0.1:8771"
        )
        return cls(base_url, timeout=timeout)

    def generate_audio(
        self,
        project_id: int,
        language: str,
        script: str,
    ) -> TTSResult:
        """POST /projects/{id}/audio with the script text."""
        url = f"{self.base_url}/projects/{project_id}/audio"
        payload = {
            "language_code": language,
            "script": script,
        }
        logger.info("TTS: POST %s (script=%d chars)", url, len(script))
        resp = requests.post(url, json=payload, timeout=self.timeout)
        resp.raise_for_status()
        raw = resp.json()

        audio_path = Path(raw.get("audio_path", ""))
        return TTSResult(
            audio_path=audio_path,
            tts_engine=raw.get("model", "kokoro"),
            voice=raw.get("speaker", "am_michael"),
            duration_sec=float(raw.get("duration_sec", 0)),
            sample_rate=int(raw.get("sample_rate", 24000)),
        )
