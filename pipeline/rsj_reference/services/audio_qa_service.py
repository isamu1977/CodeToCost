"""Proxy export for audio_qa_service."""
from redsun.services.audio_qa_service import (
    audit_audiobook,
    get_existing_audio_qa_report,
    clean_text_for_comparison,
    detect_acoustic_anomalies,
)

__all__ = [
    "audit_audiobook",
    "get_existing_audio_qa_report",
    "clean_text_for_comparison",
    "detect_acoustic_anomalies",
]
