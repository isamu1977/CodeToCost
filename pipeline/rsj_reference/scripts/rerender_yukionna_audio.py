#!/usr/bin/env python3
"""Re-render Yuki-onna audio with Ryan (Storyteller instruct).

Reads the existing script from storage/1/script_long.txt and generates
new audio using the storyteller TTS instruct.

Run from motor-python/ with the project venv.
"""

import sys, os, time

# Load .env before importing tts_engine (so env vars are set)
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tts_engine import generate_tts

STORAGE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "storage", "1")
SCRIPT_PATH = os.path.join(STORAGE_DIR, "script_long.txt")
OUTPUT_PATH = os.path.join(STORAGE_DIR, "audio_en_new.wav")

SPEAKER = "Ryan"
STORYTELLER_INSTRUCT = "Speak as a seasoned storyteller sharing a fascinating tale — clear articulation, natural pace, subtle wonder in your tone"


def main():
    print("=== Re-render Yuki-onna audio — Ryan (Storyteller) ===")

    with open(SCRIPT_PATH, "r") as f:
        script = f.read().strip()
    print(f"Script: {len(script)} chars")
    print(f"Output: {OUTPUT_PATH}")
    print(f"Speaker: {SPEAKER}")
    print(f"Instruct: {STORYTELLER_INSTRUCT}")
    print()

    t0 = time.perf_counter()
    try:
        result = generate_tts(
            script,
            OUTPUT_PATH,
            speaker=SPEAKER,
            instruct=STORYTELLER_INSTRUCT,
            language="english",
        )
        elapsed = time.perf_counter() - t0
        print(f"✅ Done in {elapsed:.0f}s ({elapsed/60:.1f} min)")
        print(f"   Audio duration: {result.duration_sec:.1f}s ({result.duration_sec/60:.1f} min)")
        print(f"   Chunks: {result.chunk_count}")
        print(f"   Speaker: {result.speaker}")
        print(f"   Instruct: {result.instruct}")
        print(f"   Output: {result.wav_path}")
    except Exception as e:
        elapsed = time.perf_counter() - t0
        print(f"❌ FAILED after {elapsed:.0f}s: {e}")
        raise


if __name__ == "__main__":
    main()
