"""Generates the canonical 5-second prestige dark charcoal outro video for Red Sun Japan.

Specifications:
- Resolution: 1920x1080 @ 30fps
- Background: Solid dark charcoal '#0d0d0e'
- Logo: Centered subtle Red Sun Japan emblem with elegant alpha
- Video: 5.0s slow fade-out to black
- Audio: Ambient night wind and deep resonant temple bell chime with 5.0s slow fade-out (-16 LUFS, 48kHz stereo)
- Video track timescale: 90000 (Remotion match)
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("generate_charcoal_outro")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PUBLIC_DIR = REPO_ROOT / "render-remotion" / "public"
ASSETS_DIR = REPO_ROOT / "assets"
LOGO_PATH = PUBLIC_DIR / "red-sun-logo-main.png"
if not LOGO_PATH.exists():
    LOGO_PATH = PUBLIC_DIR / "red-sun-logo-tate-white.png"


def generate_charcoal_outro(output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = output_path.parent / ".outro_tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)

    video_only = temp_dir / "outro_video.mp4"
    audio_only = temp_dir / "outro_audio.wav"

    logger.info("Generating charcoal background video with subtle emblem...")
    # 1. Generate 5-second video:
    # - Color canvas: 1920x1080 #0d0d0e
    # - Overlay logo centered, scaled to 360 width, opacity 0.85
    # - Video fade-out over 5 seconds (starts at 1.5s, fades out smoothly until 5.0s)
    vf = (
        f"[1:v]scale=360:-1,format=rgba,colorchannelmixer=aa=0.88[logo];"
        f"[0:v][logo]overlay=(W-w)/2:(H-h)/2:format=auto,"
        f"fade=t=out:st=1.5:d=3.5"
    )

    cmd_video = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "color=c=#0d0d0e:s=1920x1080:d=5.0:r=30",
        "-i", str(LOGO_PATH),
        "-filter_complex", vf,
        "-c:v", "libx264", "-preset", "slow", "-crf", "16",
        "-pix_fmt", "yuv420p",
        "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        "-video_track_timescale", "90000",
        "-t", "5.0",
        str(video_only),
    ]
    subprocess.run(cmd_video, check=True, capture_output=True)

    logger.info("Synthesizing ambient temple bell and night wind audio...")
    # 2. Synthesize ambient audio:
    # - Temple bell: rich resonant harmonic chime (frequencies: 216Hz fundamental, 432Hz octave, 648Hz, 864Hz)
    # - Ambient night wind: lowpass filtered noise with slow breath
    # - Slow fade-out over 5.0s
    af = (
        # Temple bell chime harmonics with gentle natural decay
        "aevalsrc=sin(216*2*PI*t)*exp(-0.7*t) + 0.5*sin(432*2*PI*t)*exp(-0.9*t) + 0.25*sin(652*2*PI*t)*exp(-1.1*t) + 0.15*sin(870*2*PI*t)*exp(-1.3*t):s=48000:d=5.0[bell];"
        # Ambient wind sweep
        "anoisesrc=d=5.0:c=pink:r=48000,lowpass=f=280,volume=0.22[wind];"
        # Mix bell + wind with stereo widen and fade-out
        "[bell][wind]amix=inputs=2:weights=1.0 0.7,"
        "afade=t=out:st=1.0:d=4.0,"
        "loudnorm=I=-16:TP=-1.5:LRA=11"
    )

    cmd_audio = [
        "ffmpeg", "-y",
        "-filter_complex", af,
        "-ar", "48000",
        "-ac", "2",
        "-t", "5.0",
        str(audio_only),
    ]
    subprocess.run(cmd_audio, check=True, capture_output=True)

    logger.info("Muxing final charcoal outro video...")
    cmd_mux = [
        "ffmpeg", "-y",
        "-i", str(video_only),
        "-i", str(audio_only),
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(output_path),
    ]
    subprocess.run(cmd_mux, check=True, capture_output=True)

    # Clean temporary files
    for tmp in [video_only, audio_only]:
        if tmp.exists():
            tmp.unlink()
    try:
        temp_dir.rmdir()
    except Exception:
        pass

    logger.info("✓ Charcoal outro successfully generated at: %s", output_path)
    return output_path


if __name__ == "__main__":
    out_asset = ASSETS_DIR / "outro.mp4"
    generate_charcoal_outro(out_asset)

    # Also sync to render-remotion/public/outro.mp4
    remotion_outro = PUBLIC_DIR / "outro.mp4"
    import shutil
    shutil.copy2(out_asset, remotion_outro)
    logger.info("✓ Synced to %s", remotion_outro)
