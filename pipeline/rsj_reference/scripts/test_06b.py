#!/usr/bin/env python3
"""Test Qwen3-TTS-0.6B vs 1.7B — same text, same speaker, same instruct."""

import os, time, torch
import soundfile as sf
from qwen_tts import Qwen3TTSModel

SCRIPT = (
    "In the mountains of northern Japan, where winter lasts half the year, "
    "there is a story older than memory itself. A story of a woman made of "
    "snow and silence, who walks the blizzards when the moon is new."
)

SPEAKER = "Ryan"
INSTRUCT = "Speak as a seasoned storyteller sharing a fascinating tale — clear articulation, natural pace, subtle wonder in your tone"
LANGUAGE = "English"
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples")
os.makedirs(OUTPUT_DIR, exist_ok=True)

MODEL_ID = "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"
OUTPUT_PATH = os.path.join(OUTPUT_DIR, "voice_test_0.6B_storyteller.wav")

print(f"=== Qwen3-TTS-0.6B Test ===")
print(f"Model: {MODEL_ID}")
print(f"Script ({len(SCRIPT)} chars): {SCRIPT[:60]}...")
print(f"Speaker: {SPEAKER}")
print(f"Instruct: {INSTRUCT[:60]}...")
print(f"Output: {OUTPUT_PATH}")
print()

# Load model
print("Loading model (first run downloads ~1.2GB)...")
t0 = time.perf_counter()
model = Qwen3TTSModel.from_pretrained(
    MODEL_ID,
    device_map="mps",
    dtype=torch.bfloat16,
    attn_implementation="sdpa",  # MPS compatible
)
load_time = time.perf_counter() - t0
print(f"Model loaded in {load_time:.1f}s")
print()

# Generate
print("Generating speech...")
t0 = time.perf_counter()
wavs, sr = model.generate_custom_voice(
    text=SCRIPT,
    language=LANGUAGE,
    speaker=SPEAKER,
    instruct=INSTRUCT,
)
gen_time = time.perf_counter() - t0
audio = wavs[0]
duration = len(audio) / sr

sf.write(OUTPUT_PATH, audio, sr)

print(f"✅ Done!")
print(f"   Generation time: {gen_time:.1f}s ({gen_time/60:.1f} min)")
print(f"   Audio duration: {duration:.1f}s ({duration/60:.1f} min)")
print(f"   RTF: {gen_time/duration:.2f}x (lower = faster)")
print(f"   Output: {OUTPUT_PATH}")
print()
print(f"=== Comparison ===")
print(f"   1.7B (prev run): 40.5 min for 706s audio = 0.057 RTF")
print(f"   0.6B (this run): {gen_time/60:.1f} min for {duration:.1f}s audio = {gen_time/duration:.3f} RTF")
print(f"   Speedup: {(40.5*60)/gen_time:.1f}x")
