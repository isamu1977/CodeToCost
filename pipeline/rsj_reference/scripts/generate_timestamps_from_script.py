#!/usr/bin/env python3
from __future__ import annotations
import json, math, os, random, re, sys
from pathlib import Path
_WORD_RE = re.compile(r"[A-Za-z']+")
def _clean_words(text: str) -> list[str]:
    return [w for w in _WORD_RE.findall(text) if w]
def _estimate_timestamps(words: list[str], duration_sec: float, seed: int = 42) -> list[dict]:
    if not words:
        return []
    if duration_sec <= 0:
        duration_sec = 1.0
    rng = random.Random(seed)
    total = len(words)
    base_interval = duration_sec / max(total - 1, 1)
    result = []
    t = 0.0
    for i, w in enumerate(words):
        start = round(t, 3)
        if i == total - 1:
            end = round(duration_sec, 3)
        else:
            jitter = rng.uniform(-0.02, 0.02)
            next_t = t + base_interval + jitter
            end = round(min(next_t, duration_sec), 3)
        result.append({"word": w, "start": start, "end": end})
        t = end
    return result
def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: generate_timestamps_from_script.py <project_id> [audio_path]", file=sys.stderr)
        sys.exit(1)
    project_id = int(sys.argv[1])
    audio_path = (
        Path(sys.argv[2])
        if len(sys.argv) > 2
        else Path(__file__).resolve().parent.parent / "storage" / str(project_id) / "audio_en.wav"
    )
    storage_dir = Path(__file__).resolve().parent.parent / "storage" / str(project_id)
    script_path = storage_dir / "script_long.txt"
    output_path = storage_dir / "timestamps_en.json"
    if not script_path.exists() or not audio_path.exists():
        print(f"Missing inputs: script={script_path.exists()}, audio={audio_path.exists()}", file=sys.stderr)
        sys.exit(1)
    script_text = script_path.read_text(encoding="utf-8")
    words = _clean_words(script_text)
    import subprocess
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(audio_path),
    ]
    duration_sec = float(subprocess.check_output(cmd).decode().strip() or 0)
    timestamps = _estimate_timestamps(words, duration_sec)
    output_path.write_text(json.dumps(timestamps, ensure_ascii=False, indent=2))
    print(f"✅ {len(timestamps)} words -> {output_path}")
    print(f"   audio duration: {duration_sec:.1f}s")
if __name__ == "__main__":
    main()
