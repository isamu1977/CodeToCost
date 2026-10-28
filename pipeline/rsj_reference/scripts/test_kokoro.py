#!/usr/bin/env python3
"""Test Kokoro-82M — the right way (native kokoro package, not mlx-audio)."""

import time, os
import soundfile as sf

SCRIPT = (
    "In the mountains of northern Japan, where winter lasts half the year, "
    "there is a story older than memory itself. A story of a woman made of "
    "snow and silence, who walks the blizzards when the moon is new."
)

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples")
os.makedirs(OUTPUT_DIR, exist_ok=True)

print("=== Kokoro-82M Test ===")
print(f"Script ({len(SCRIPT)} chars): {SCRIPT[:60]}...")
print(f"Output dir: {OUTPUT_DIR}")
print()

# Import kokoro
from kokoro import KPipeline

print("Initializing pipeline...")
t0 = time.perf_counter()
pipeline = KPipeline(lang_code='a')  # 'a' = American English
init_time = time.perf_counter() - t0
print(f"Pipeline ready in {init_time:.1f}s")
print()

# Generate
print("Generating speech...")
t0 = time.perf_counter()
generator = pipeline(SCRIPT, voice='af_heart', speed=1.0)

audio_parts = []
for i, (gs, ps, audio) in enumerate(generator):
    out_path = os.path.join(OUTPUT_DIR, f"voice_test_kokoro_{i}.wav")
    sf.write(out_path, audio, 24000)
    audio_parts.append((out_path, audio))
    print(f"  Chunk {i}: {len(audio)/24000:.1f}s audio")

gen_time = time.perf_counter() - t0

# Also save a combined file
import numpy as np
all_audio = np.concatenate([a for _, a in audio_parts])
combined_path = os.path.join(OUTPUT_DIR, "voice_test_kokoro.wav")
sf.write(combined_path, all_audio, 24000)
duration = len(all_audio) / 24000

print()
print(f"✅ Done!")
print(f"   Generation time: {gen_time:.1f}s ({gen_time/60:.1f} min)")
print(f"   Audio duration: {duration:.1f}s ({duration/60:.1f} min)")
print(f"   RTF: {gen_time/duration:.2f}x (lower = faster)")
print(f"   Output: {combined_path}")
print()
print(f"=== Comparison ===")
print(f"   Qwen3-TTS 1.7B: RTF 3.44x (40.5 min for 706s)")
print(f"   Qwen3-TTS 0.6B: RTF 3.14x (~37 min est.)")
print(f"   Kokoro-82M:     RTF {gen_time/duration:.2f}x ({gen_time/60:.1f} min for {duration:.1f}s)")
speedup_17b = (40.5 * 60) / gen_time if gen_time > 0 else 0
print(f"   Speedup vs 1.7B: {speedup_17b:.1f}x")
