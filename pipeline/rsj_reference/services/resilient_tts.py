"""Proxy export for Resilient Tiered Fallback TTS engine."""
from redsun.services.resilient_tts import (
    synthesize_chunk_with_fallback,
    synthesize_chunk_waveform_with_fallback,
    synthesize_single_engine_chunk,
    waveform_to_wav_bytes,
    wav_bytes_to_waveform,
)

__all__ = [
    "synthesize_chunk_with_fallback",
    "synthesize_chunk_waveform_with_fallback",
    "synthesize_single_engine_chunk",
    "waveform_to_wav_bytes",
    "wav_bytes_to_waveform",
]
