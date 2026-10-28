#!/usr/bin/env python3
"""
Reliable render pipeline for Red Sun Japan videos.

Fixes all known issues:
1. Duration drift (manifest vs audio)
2. Time base mismatches in concat
3. Subtitle timing drift
4. Audio duplication
"""
import json
import subprocess
import sys
from pathlib import Path
import os

REPO = Path("/Users/isamumatsuyama/Documents/online-projects/red-sun-japan")
REMOTION = REPO / "channel-redsunjapan" / "render-remotion"
PUBLIC = REMOTION / "public"
BRANDING = REPO / "branding"

def run(cmd, **kwargs):
    """Run shell command and return success bool."""
    print(f"$ {cmd[:100]}...")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, **kwargs)
    if result.returncode != 0:
        print(f"FAILED: {result.stderr[-500:]}")
        return False
    return True

def get_duration(path):
    """Get video duration in seconds."""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True
    )
    return float(r.stdout.strip())

def normalize_video(input_path, output_path, duration_limit=None):
    """Re-encode to standard format (eliminates time_base issues)."""
    # Use file:// URI for paths with spaces
    input_uri = f"file://{input_path}"
    cmd = f'''ffmpeg -y -i "{input_path}"'''
    if duration_limit:
        cmd += f' -t {duration_limit}'
    cmd += f''' -vf "scale=1920:1080,fps=30,format=yuv420p" \\
        -c:v libx264 -preset fast -crf 18 -video_track_timescale 90000 \\
        -c:a aac -b:a 192k -ar 48000 \\
        -movflags +faststart \\
        "{output_path}"'''
    return run(cmd)

def main():
    slug = sys.argv[1] if len(sys.argv) > 1 else "hoichi-the-earless"
    # Project slug uses underscores in projects/ folder
    project_dir = REPO / "channel-redsunjapan" / "projects" / "hoichi_the_earless"
    if not project_dir.exists():
        project_dir = REPO / "channel-redsunjapan" / "projects" / slug
    if not project_dir.exists():
        print(f"Project not found at: {project_dir}")
        sys.exit(1)

    # 1. Verify audio duration
    audio_path = PUBLIC / "audio_en.wav"
    audio_dur = get_duration(audio_path)
    print(f"Audio duration: {audio_dur:.3f}s")

    # 2. Fix manifest with correct duration
    manifest_path = PUBLIC / "manifest.json"
    with open(manifest_path) as f:
        manifest = json.load(f)

    fps = 30
    correct_frames = int(audio_dur * fps)
    print(f"Correct total frames: {correct_frames}")

    if manifest["duration_seconds"] != audio_dur:
        print(f"Fixing manifest: {manifest['duration_seconds']} → {audio_dur}")
        manifest["duration_seconds"] = audio_dur
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)

    # 3. Find the main_faded.mp4 (already rendered with Ken Burns etc.)
    main_faded = REMOTION / "projects" / slug / "outputs" / f"{slug}_main_faded.mp4"
    if not main_faded.exists():
        print(f"ERROR: {main_faded} not found")
        print("Run render_full.sh first to generate chunks + main + main_faded")
        sys.exit(1)

    main_dur = get_duration(main_faded)
    print(f"Main faded duration: {main_dur:.3f}s (expected: ~{audio_dur + 3:.3f}s with fade)")

    # 4. Normalize ALL segments to identical format
    work_dir = Path("/tmp/hoichi_final")
    work_dir.mkdir(exist_ok=True)

    intro_src = BRANDING / "red sun japan intro.mp4"
    ending_src = BRANDING / "red sun japan ending.mp4"

    print("\n=== Normalizing segments ===")
    intro_norm = work_dir / "intro_norm.mp4"
    main_norm = work_dir / "main_norm.mp4"
    ending_norm = work_dir / "ending_norm.mp4"

    normalize_video(intro_src, intro_norm, duration_limit=6.5)
    normalize_video(main_faded, main_norm)
    normalize_video(ending_src, ending_norm, duration_limit=5.0)

    # 5. Concat with identical format
    concat_list = work_dir / "concat.txt"
    concat_list.write_text(
        f"file '{intro_norm}'\n"
        f"file '{main_norm}'\n"
        f"file '{ending_norm}'\n"
    )

    final_output = project_dir / "video" / f"{slug}_complete.mp4"
    final_output.parent.mkdir(parents=True, exist_ok=True)

    print(f"\n=== Concat + re-encode final ===")
    final_tmp = work_dir / "final.mp4"
    # Re-encode the concat to ensure exact final duration
    cmd = f'''ffmpeg -y -f concat -safe 0 -i "{concat_list}" \\
        -c:v libx264 -preset fast -crf 18 -video_track_timescale 90000 \\
        -c:a aac -b:a 192k -ar 48000 \\
        -movflags +faststart \\
        "{final_tmp}"'''
    if not run(cmd):
        sys.exit(1)

    # 6. Verify and move to final location
    final_dur = get_duration(final_tmp)
    print(f"\n=== Verification ===")
    print(f"Expected: ~{6.5 + main_dur + 5.0:.3f}s")
    print(f"Actual: {final_dur:.3f}s")
    print(f"Drift: {final_dur - (6.5 + main_dur + 5.0):.3f}s")

    import shutil
    shutil.copy(final_tmp, final_output)
    print(f"\n✅ Final video: {final_output}")
    print(f"   Duration: {final_dur:.2f}s ({int(final_dur//60)}:{int(final_dur%60):02d})")

    # Show file size
    size_mb = final_output.stat().st_size / 1024 / 1024
    print(f"   Size: {size_mb:.1f} MB")

if __name__ == "__main__":
    main()
