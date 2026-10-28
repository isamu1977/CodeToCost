"""HTTP-backed Whisper transcription service for the wired pipeline.

Calls the FastAPI endpoint ``POST /projects/{id}/transcribe`` to
generate word-level timestamps via faster-whisper.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import requests

logger = logging.getLogger("redsun.services.whisper")


@dataclass
class TranscriptResult:
    """Result from a Whisper transcription call."""

    path: Path
    word_count: int
    segment_count: int
    duration_sec: float


class WhisperService:
    """HTTP-backed Whisper driver.

    Construct via ``WhisperService.from_context(ctx)``.
    """

    def __init__(self, base_url: str, *, timeout: int = 300) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    @classmethod
    def from_context(cls, context, *, timeout: int = 300) -> "WhisperService":
        base_url = (
            getattr(getattr(context, "llm", None), "base_url", None)
            or "http://127.0.0.1:8771"
        )
        return cls(base_url, timeout=timeout)

    def transcribe(
        self,
        project_id: int,
        language: str,
    ) -> TranscriptResult:
        """POST /projects/{id}/transcribe."""
        url = f"{self.base_url}/projects/{project_id}/transcribe"
        payload = {"language_code": language}
        logger.info("Whisper: POST %s", url)
        resp = requests.post(url, json=payload, timeout=self.timeout)
        resp.raise_for_status()
        raw = resp.json()

        # The server stores timestamps at storage/{id}/timestamps_{lang}.json
        ts_path = Path(raw.get("timestamps_path", ""))
        return TranscriptResult(
            path=ts_path,
            word_count=int(raw.get("word_count", 0)),
            segment_count=int(raw.get("segment_count", 0)),
            duration_sec=float(raw.get("duration_sec", 0)),
        )
