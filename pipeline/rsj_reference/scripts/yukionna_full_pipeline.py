#!/usr/bin/env python3
"""Generate Yuki-onna audio with Kokoro-82M and run full pipeline."""
import sys, os, time

os.environ['PYTORCH_ENABLE_MPS_FALLBACK'] = '1'
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tts_engine import generate_tts

STORAGE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "storage", "1")
SCRIPT_PATH = os.path.join(STORAGE, "script_long.txt")
OUTPUT_PATH = os.path.join(STORAGE, "audio_en.wav")  # overwrite main audio

print("=== Yuki-onna Full Pipeline — Kokoro ===")
print()

# Step 1: TTS
print("[1/4] Generating audio with Kokoro-82M...")
with open(SCRIPT_PATH) as f:
    script = f.read().strip()
print(f"  Script: {len(script)} chars")

t0 = time.perf_counter()
result = generate_tts(
    script,
    OUTPUT_PATH,
    speaker="af_heart",
    engine="kokoro",
)
elapsed = time.perf_counter() - t0
print(f"  ✅ Audio: {result.duration_sec:.1f}s ({result.duration_sec/60:.1f} min)")
print(f"     Generated in {elapsed:.1f}s ({elapsed/60:.1f} min)")
print(f"     RTF: {elapsed/result.duration_sec:.2f}x")
print(f"     Output: {result.wav_path}")
print()

# Step 2: Whisper transcription
print("[2/4] Transcribing with Faster-Whisper...")
from whisper_engine import transcribe_audio
ts_output = os.path.join(STORAGE, "timestamps_en.json")
t0 = time.perf_counter()
ts_result = transcribe_audio(OUTPUT_PATH, ts_output, language="en")
elapsed = time.perf_counter() - t0
print(f"  ✅ {ts_result.word_count} words, {ts_result.duration_sec:.1f}s audio ({elapsed:.1f}s)")
print()

# Step 3: Manifest
print("[3/4] Generating render manifest...")
import requests
resp = requests.post(
    "http://127.0.0.1:8771/projects/1/render-manifest",
    json={"language_code": "en"},
    timeout=10,
)
manifest_data = resp.json()
print(f"  ✅ title_en: {manifest_data.get('title_en')}")
print(f"     title_ja: {manifest_data.get('title_ja')}")
print(f"     duration: {manifest_data.get('duration_seconds')}s")
print(f"     images: {manifest_data.get('image_count')}")
print()

# Step 4: Sync assets
print("[4/4] Syncing assets to render-remotion/public/...")
import subprocess
sync_result = subprocess.run(
    ["bash", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sync_project_assets.sh"), "1"],
    capture_output=True, text=True, timeout=60,
)
if sync_result.returncode == 0:
    print("  ✅ Assets synced")
else:
    print(f"  ⚠️ Sync warning: {sync_result.stderr[-200:]}")

# Copy manifest
import shutil
manifest_src = os.path.join(STORAGE, "manifest.json")
manifest_dst = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "render-remotion", "public", "manifest.json")
shutil.copy2(manifest_src, manifest_dst)
print("  ✅ Manifest copied to render-remotion/public/")
print()

print("=" * 50)
print("  ✅ All pipeline steps complete!")
print(f"  Audio: {result.wav_path}")
print(f"  Timestamps: {ts_output}")
print(f"  Manifest: {manifest_dst}")
print()
print("  Next: run render_full.sh from render-remotion/")
print("=" * 50)
