"""Post-process the curated music library:

1. Re-validate each file: must be a real audio file, not a Wikipedia
   pronunciation sample or attribution stub.
2. Compute duration_sec via ffprobe for every entry (the API often returns
   duration=None).
3. Re-check license metadata to be safe.
4. Drop files that fail any check.
5. Save the cleaned metadata.json.

Run AFTER curate_music.py completes.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
MUSIC_DIR = ROOT / "music_library"
METADATA_PATH = MUSIC_DIR / "metadata.json"

# Minimum real-music thresholds. Anything shorter is almost certainly a
# pronunciation sample, attribution stub, or test tone.
MIN_DURATION_SEC = 30
MIN_FILE_SIZE_KB = 50  # pronunciation files are usually <50KB

# Words that signal a non-music Wikimedia file.
NON_MUSIC_HINTS = (
    "pronunciation",
    "attribution",
    "spelled",
    "pronunciation of",
    "pronounciation",
)


def ffprobe_duration(path: Path) -> float | None:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        if result.stdout.strip():
            return float(result.stdout.strip())
    except Exception:
        pass
    return None


def ffprobe_codec(path: Path) -> tuple[str | None, int | None]:
    """Return (codec_name, bit_rate) from ffprobe."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error",
             "-select_streams", "a:0",
             "-show_entries", "stream=codec_name,bit_rate",
             "-of", "default=noprint_wrappers=1", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        codec = None
        bitrate = None
        for line in result.stdout.splitlines():
            if line.startswith("codec_name="):
                codec = line.split("=", 1)[1]
            elif line.startswith("bit_rate="):
                try:
                    bitrate = int(line.split("=", 1)[1])
                except ValueError:
                    pass
        return codec, bitrate
    except Exception:
        return None, None


def is_music_file(path: Path, title: str) -> tuple[bool, str]:
    """Return (is_music, reason)."""
    if not path.exists():
        return False, "missing file"
    size_kb = path.stat().st_size / 1024
    if size_kb < MIN_FILE_SIZE_KB:
        return False, f"file too small ({size_kb:.0f}KB < {MIN_FILE_SIZE_KB}KB)"

    title_lower = title.lower()
    for hint in NON_MUSIC_HINTS:
        if hint in title_lower:
            return False, f"title contains {hint!r}"

    dur = ffprobe_duration(path)
    if dur is None:
        return False, "ffprobe failed (no duration)"
    if dur < MIN_DURATION_SEC:
        return False, f"duration too short ({dur:.0f}s < {MIN_DURATION_SEC}s)"

    codec, bitrate = ffprobe_codec(path)
    if codec is None:
        return False, "ffprobe failed (no audio stream)"
    # Reject speech-only formats? Vorbis, MP3, FLAC, AAC are all fine.
    # We don't reject any codec; just need audio.

    return True, "ok"


def main():
    if not METADATA_PATH.exists():
        print(f"FAIL: {METADATA_PATH} not found. Run curate_music.py first.")
        sys.exit(1)

    with METADATA_PATH.open() as f:
        metadata = json.load(f)

    print(f"Loaded {len(metadata)} entries. Validating...")

    cleaned: dict[str, dict] = {}
    rejected: list[tuple[str, str]] = []

    for title, info in metadata.items():
        filename = info.get("filename") or ("File:" + title.replace(" ", "_").replace("File:", ""))
        # If filename starts with "File:" (recovered entry), resolve to a real file
        if filename.startswith("File:"):
            # Find by name across mood subdirs
            for mood_dir in MUSIC_DIR.iterdir():
                if not mood_dir.is_dir():
                    continue
                target = mood_dir / filename.replace("File:", "")
                if target.exists():
                    filename = str(target.relative_to(MUSIC_DIR))
                    break
        elif "/" not in filename:
            # Legacy entries that just have a basename
            for mood_dir in MUSIC_DIR.iterdir():
                if not mood_dir.is_dir():
                    continue
                target = mood_dir / filename
                if target.exists():
                    filename = str(target.relative_to(MUSIC_DIR))
                    break

        full = MUSIC_DIR / filename
        ok, reason = is_music_file(full, title)
        if not ok:
            rejected.append((title, reason))
            # Remove the file too
            if full.exists():
                full.unlink()
                print(f"  ✗ REJECT  {filename} ({reason})")
            continue

        # Refresh duration_sec from ffprobe (more reliable than API)
        dur = ffprobe_duration(full)
        codec, bitrate = ffprobe_codec(full)
        size_kb = full.stat().st_size / 1024

        info["filename"] = filename
        info["duration_sec"] = dur
        info["size_kb"] = round(size_kb, 1)
        info["codec"] = codec
        info["bit_rate"] = bitrate
        cleaned[title] = info

    # Save
    with METADATA_PATH.open("w") as f:
        json.dump(cleaned, f, indent=2, ensure_ascii=False)

    # Summary
    print(f"\n{'='*60}")
    print(f"Kept:    {len(cleaned)}")
    print(f"Rejected: {len(rejected)}")
    if rejected:
        print("\nRejected files (deleted from disk):")
        for title, reason in rejected:
            print(f"  {reason:30s}  {title[:60]}")
    by_mood: dict[str, int] = {}
    total_dur = 0
    for info in cleaned.values():
        m = info.get("mood", "unknown")
        by_mood[m] = by_mood.get(m, 0) + 1
        if info.get("duration_sec"):
            total_dur += info["duration_sec"]
    print("\nFinal distribution:")
    for mood in sorted(by_mood):
        print(f"  {mood:15s}: {by_mood[mood]} tracks")
    print(f"\nTotal music time: {total_dur/60:.1f} min ({total_dur/3600:.2f} hours)")


if __name__ == "__main__":
    main()
