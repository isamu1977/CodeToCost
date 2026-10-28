#!/usr/bin/env python3
"""Transcribe the new Yuki-onna audio with Faster-Whisper."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from whisper_engine import transcribe_audio

AUDIO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "storage", "1", "audio_en_new.wav")
OUTPUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "storage", "1", "timestamps_en_new.json")

print(f"Transcribing: {AUDIO}")
result = transcribe_audio(AUDIO, OUTPUT, language="en")
print(f"✅ {result.word_count} words, {result.duration_sec:.1f}s")
print(f"   Output: {result.timestamps_path}")
