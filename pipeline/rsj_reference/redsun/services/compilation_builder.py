"""Multi-Story Compilation Builder for Red Sun Japan.

Stitches 3 to 6 finished episodes into a continuous long-play documentary,
calculates unified timestamp chapters, creates composite 4K/1080p master video,
generates composite thumbnails, formats YouTube descriptions, and uploads
drafts to YouTube with playlist assignment.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from dotenv import load_dotenv
from fastapi import HTTPException
from PIL import Image, ImageDraw, ImageFilter, ImageFont

load_dotenv()

from redsun.workspace import RUNTIME_ROOT, resolve_project_dir
from redsun.services.youtube_auth import get_youtube_client

logger = logging.getLogger("redsun.compilation_builder")

COMPILATIONS_DIR = RUNTIME_ROOT / "_compilations"

# In-memory progress tracking for asynchronous builds
ACTIVE_COMPILATION_JOBS: Dict[str, Dict[str, Any]] = {}
_JOB_LOCK = threading.Lock()


def _ensure_compilations_dir() -> Path:
    COMPILATIONS_DIR.mkdir(parents=True, exist_ok=True)
    return COMPILATIONS_DIR


def format_duration(seconds: float) -> str:
    """Format duration in seconds into HH:MM:SS or MM:SS."""
    sec = int(round(seconds))
    hrs = sec // 3600
    rem = sec % 3600
    mins = rem // 60
    secs = rem % 60
    if hrs > 0:
        return f"{hrs:02d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"


def to_roman_numeral(n: int) -> str:
    """Convert integer to Roman numeral."""
    val = [1000, 900, 500, 400, 100, 90, 50, 40, 10, 9, 5, 4, 1]
    syb = ["M", "CM", "D", "CD", "C", "XC", "L", "XL", "X", "IX", "V", "IV", "I"]
    roman_num = ""
    i = 0
    while n > 0:
        for _ in range(n // val[i]):
            roman_num += syb[i]
            n -= val[i]
        i += 1
    return roman_num


def probe_video_duration(video_path: Path) -> float:
    """Extract exact video duration in seconds using ffprobe."""
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json",
        str(video_path),
    ]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        data = json.loads(res.stdout)
        dur_str = data.get("format", {}).get("duration")
        if dur_str:
            return float(dur_str)
    except Exception as exc:
        logger.warning("ffprobe duration check failed for %s: %s", video_path, exc)

    # Fallback to streams check
    cmd_stream = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=duration",
        "-of", "json",
        str(video_path),
    ]
    try:
        res = subprocess.run(cmd_stream, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        data = json.loads(res.stdout)
        streams = data.get("streams", [])
        if streams and streams[0].get("duration"):
            return float(streams[0]["duration"])
    except Exception as exc:
        logger.error("All ffprobe duration attempts failed for %s: %s", video_path, exc)

    raise RuntimeError(f"Unable to determine duration for video: {video_path}")


def _find_project_master_video(project_dir: Path, slug: str) -> Optional[Path]:
    """Find master video in a given project folder."""
    video_dir = project_dir / "video"
    render_dir = project_dir / "render"

    candidates = [
        video_dir / f"{slug}_master.mp4",
        video_dir / "output_master.mp4",
        render_dir / "output_master.mp4",
        project_dir / f"{slug}_master.mp4",
        project_dir / "output_master.mp4",
    ]
    for c in candidates:
        if c.is_file() and c.stat().st_size > 10 * 1024 * 1024:
            return c

    # Search for any master mp4 in video_dir
    if video_dir.exists():
        for f in video_dir.glob("*_master.mp4"):
            if f.is_file() and f.stat().st_size > 10 * 1024 * 1024:
                return f

    return None


def _find_project_thumbnail(project_dir: Path, slug: str) -> Optional[Path]:
    """Find thumbnail image for a given project folder."""
    dist_dir = project_dir / "distribution"
    candidates = [
        dist_dir / "thumbnail.png",
        dist_dir / f"{slug}_thumbnail.png",
        project_dir / "thumbnail.png",
        dist_dir / "thumbnail.jpg",
        dist_dir / "thumbnail.jpeg",
    ]
    for c in candidates:
        if c.is_file() and c.stat().st_size > 1000:
            return c
    return None


def _read_project_title(project_dir: Path, slug: str) -> str:
    """Read canonical title from metadata or fallback to slug title."""
    # Check project_manifest.json
    manifest_file = project_dir / "project_manifest.json"
    if manifest_file.exists():
        try:
            m = json.loads(manifest_file.read_text(encoding="utf-8"))
            if m.get("title_en"):
                return m["title_en"]
            if m.get("title"):
                return m["title"]
        except Exception:
            pass

    # Check social_distribution_kit.json
    kit_file = project_dir / "distribution" / "social_distribution_kit.json"
    if kit_file.exists():
        try:
            k = json.loads(kit_file.read_text(encoding="utf-8"))
            y_title = k.get("youtube", {}).get("title_options", [])
            if y_title and len(y_title) > 0:
                # Clean up title option
                raw_t = y_title[0].split("|")[0].split(":")[0].strip()
                if raw_t:
                    return raw_t
        except Exception:
            pass

    # Clean slug fallback
    clean = re.sub(r"^\d{3}_", "", slug)
    return clean.replace("-", " ").title()


def get_eligible_compilation_projects() -> List[Dict[str, Any]]:
    """Scan RUNTIME_ROOT and return all projects with rendered master videos,
    enriched with database metadata (ID, created_at, canonical title) for chronological tracking."""
    if not RUNTIME_ROOT.exists():
        return []

    # Map database projects by slug and ID for accurate creation order and metadata
    db_projects_by_slug: Dict[str, Any] = {}
    db_projects_by_id: Dict[int, Any] = {}
    try:
        from database import SessionLocal, VideoProject
        db = SessionLocal()
        for p in db.query(VideoProject).all():
            if p.slug:
                db_projects_by_slug[p.slug] = p
            if p.id is not None:
                db_projects_by_id[p.id] = p
        db.close()
    except Exception as exc:
        logger.warning(f"Could not load database projects in get_eligible_compilation_projects: {exc}")

    eligible: List[Dict[str, Any]] = []
    seen_slugs = set()
    seen_videos = set()

    # Sort directories: prioritize numbered folders (e.g. 035_xxx) over legacy unnumbered folders
    candidate_folders = []
    for f in RUNTIME_ROOT.iterdir():
        if not f.is_dir() or f.name.startswith(".") or f.name.startswith("_"):
            continue
        # Skip symlinked directories whose target is in RUNTIME_ROOT
        if f.is_symlink():
            continue
        candidate_folders.append(f)

    # Put numbered directories first so they win during deduplication
    candidate_folders.sort(key=lambda d: (0 if re.match(r"^\d{3}_", d.name) else 1, d.name))

    for folder in candidate_folders:
        raw_name = folder.name
        folder_match = re.match(r"^(\d{3})_(.+)$", raw_name)
        if folder_match:
            folder_id = int(folder_match.group(1))
            canonical_slug = folder_match.group(2)
        else:
            folder_id = None
            canonical_slug = raw_name

        if canonical_slug in seen_slugs:
            continue

        master_video = _find_project_master_video(folder, canonical_slug)
        if not master_video:
            continue

        # Prevent duplicate entries if symlinks or identical video files exist
        real_video_path = str(master_video.resolve())
        if real_video_path in seen_videos:
            continue

        try:
            duration_sec = probe_video_duration(master_video)
        except Exception:
            continue

        thumb_path = _find_project_thumbnail(folder, canonical_slug)

        # Match DB record
        db_proj = db_projects_by_slug.get(canonical_slug) or (
            db_projects_by_id.get(folder_id) if folder_id is not None else None
        )

        proj_id = db_proj.id if (db_proj and db_proj.id is not None) else folder_id
        if db_proj and db_proj.created_at:
            created_at = db_proj.created_at.isoformat()
        else:
            try:
                created_at = datetime.datetime.fromtimestamp(
                    folder.stat().st_ctime, tz=datetime.timezone.utc
                ).isoformat()
            except Exception:
                created_at = None

        if db_proj and db_proj.title:
            title = db_proj.title
        else:
            title = _read_project_title(folder, canonical_slug)

        seen_slugs.add(canonical_slug)
        seen_videos.add(real_video_path)

        eligible.append({
            "id": proj_id,
            "created_at": created_at,
            "slug": canonical_slug,
            "folder": raw_name,
            "title": title,
            "duration_seconds": round(duration_sec, 2),
            "duration_formatted": format_duration(duration_sec),
            "thumbnail_url": f"/projects/{canonical_slug}/thumbnail" if thumb_path else None,
            "has_video": True,
            "video_file": str(master_video),
            "status": "MASTER COMPLETED",
            "file_size_mb": round(master_video.stat().st_size / (1024 * 1024), 1),
        })

    # Default sort by creation order (project ID ascending, fallback to created_at)
    eligible.sort(key=lambda x: (x["id"] if x["id"] is not None else 999999, x["created_at"] or "", x["title"]))
    return eligible


def calculate_compilation_timeline(
    selected_slugs: List[str],
    trim_intermediate_outros_seconds: float = 12.0,
) -> Dict[str, Any]:
    """Calculate exact unified chapter timestamps and trimming details for ordered projects."""
    if not selected_slugs:
        raise ValueError("No projects selected for compilation.")

    episodes: List[Dict[str, Any]] = []
    total_accumulated_seconds = 0.0

    for idx, slug in enumerate(selected_slugs):
        proj_dir = resolve_project_dir(slug)
        if not proj_dir or not proj_dir.exists():
            raise FileNotFoundError(f"Project directory not found for '{slug}'")

        master_video = _find_project_master_video(proj_dir, slug)
        if not master_video:
            raise FileNotFoundError(f"Master video not found for '{slug}' in {proj_dir}")

        dur = probe_video_duration(master_video)
        is_last = (idx == len(selected_slugs) - 1)

        # Trim outro only on intermediate episodes
        trim_outro = trim_intermediate_outros_seconds if (not is_last and trim_intermediate_outros_seconds > 0) else 0.0
        effective_dur = max(1.0, dur - trim_outro)

        chapter_num = to_roman_numeral(idx + 1)
        title = _read_project_title(proj_dir, slug)
        chapter_title = f"Chapter {chapter_num}: {title}"

        thumb = _find_project_thumbnail(proj_dir, slug)

        chapter_start_sec = total_accumulated_seconds
        chapter_start_fmt = format_duration(chapter_start_sec)
        # Ensure 3-segment format HH:MM:SS for YouTube chapters
        if len(chapter_start_fmt.split(":")) == 2:
            chapter_start_fmt = f"00:{chapter_start_fmt}"

        episodes.append({
            "index": idx,
            "slug": slug,
            "title": title,
            "video_path": str(master_video),
            "thumbnail_path": str(thumb) if thumb else None,
            "original_duration_seconds": round(dur, 2),
            "effective_duration_seconds": round(effective_dur, 2),
            "trim_outro_seconds": round(trim_outro, 2),
            "chapter_start_seconds": round(chapter_start_sec, 2),
            "chapter_start_formatted": chapter_start_fmt,
            "chapter_title": chapter_title,
        })

        total_accumulated_seconds += effective_dur

    total_formatted = format_duration(total_accumulated_seconds)
    if len(total_formatted.split(":")) == 2:
        total_formatted = f"00:{total_formatted}"

    return {
        "episodes_count": len(episodes),
        "total_duration_seconds": round(total_accumulated_seconds, 2),
        "total_duration_formatted": total_formatted,
        "trim_intermediate_outros_seconds": trim_intermediate_outros_seconds,
        "episodes": episodes,
    }


def generate_compilation_metadata(
    compilation_title: str,
    timeline_data: Dict[str, Any],
    playlist_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate high-CTR title options, structured description with chapters, and tags."""
    episodes = timeline_data["episodes"]
    ep_count = len(episodes)
    dur_fmt = timeline_data["total_duration_formatted"]

    # 1. Title options optimized for nighttime retention and SEO
    title_options = [
        f"{compilation_title} (Full Compilation)",
        f"Japanese Ghost Stories & Kaidan: {ep_count} Uncut Feudal Chronicles (Sleep & Study)",
        f"Tales of Feudal Japan: {dur_fmt} Archival Folklore Documentary",
    ]

    # 2. Build Chapters text
    chapters_lines = []
    for ep in episodes:
        chapters_lines.append(f"{ep['chapter_start_formatted']} - {ep['chapter_title']}")
    chapters_block = "\n".join(chapters_lines)

    # 3. Full description
    default_playlist = playlist_name or "Edo Japan Folklore for Deep Sleep & Rainy Nights (Full Compilations)"
    description = f"""A continuous archival journey through ancient Japan. Immerse yourself in {ep_count} restored chronicles of spirits, samurais, and spectral kaidan from the Edo, Heian, and Meiji eras, curated for nighttime listening, atmospheric study, and deep contemplation.

📜 TIMESTAMPS & CHAPTERS:
{chapters_block}

🏯 OFFICIAL ARCHIVE & COMMUNITY:
• Canonical Blog & Archival Essays: https://redsunjapan.com
• High-Resolution Illustrated Prints: https://redsunjapan.com/store
• Subscribe to Red Sun Japan: https://www.youtube.com/@redsunjapan?sub_confirmation=1

🎵 TRADITIONAL SOUNDSCAPES & BGM:
• Traditional Shakuhachi, Wooden Biwa & Night Rainfall Ambience by Red Sun Japan Sound Studio
• Binaural Low-Frequency Atmospheric Master (-14 LUFS Broadcast Standard)

#JapaneseFolklore #Kaidan #EdoPeriod #DeepSleep #JapaneseHistory #SamuraiHistory #Yokai"""

    tags = [
        "Japanese folklore",
        "Kaidan",
        "Japanese ghost stories",
        "Sleep compilation",
        "Rainy night Kaidan",
        "Edo period",
        "Feudal Japan documentary",
        "Yokai",
        "Japanese mythology",
        "Deep sleep stories",
        "Relaxing folklore",
        "Red Sun Japan",
        "Hoichi the Earless",
        "Botan Doro",
        "Kwaidan",
        "Traditional Japan",
    ]

    return {
        "title_options": title_options,
        "selected_title": title_options[0],
        "chapters_text": chapters_block,
        "description": description.strip(),
        "tags": tags,
        "playlist_name": default_playlist,
    }


def build_composite_thumbnail(
    thumb_paths: List[Optional[Path]],
    output_path: Path,
    compilation_title: str,
) -> Path:
    """Create a consolidated 1280x720 thumbnail collage from episode thumbnails."""
    width, height = 1280, 720
    canvas = Image.new("RGB", (width, height), (12, 13, 16))

    valid_thumbs = [p for p in thumb_paths if p and p.exists()]

    if not valid_thumbs:
        # Generate dark atmospheric placeholder
        draw = ImageDraw.Draw(canvas)
        draw.rectangle([0, 0, width, height], fill=(16, 17, 20))
    elif len(valid_thumbs) == 1:
        img = Image.open(valid_thumbs[0]).convert("RGB").resize((width, height), Image.Resampling.LANCZOS)
        canvas.paste(img, (0, 0))
    elif len(valid_thumbs) == 2:
        col_w = width // 2
        for i, tp in enumerate(valid_thumbs[:2]):
            img = Image.open(tp).convert("RGB")
            # Crop to aspect ratio and resize
            img_ratio = img.width / img.height
            target_ratio = col_w / height
            if img_ratio > target_ratio:
                new_w = int(img.height * target_ratio)
                left = (img.width - new_w) // 2
                img = img.crop((left, 0, left + new_w, img.height))
            else:
                new_h = int(img.width / target_ratio)
                top = (img.height - new_h) // 2
                img = img.crop((0, top, img.width, top + new_h))
            img = img.resize((col_w, height), Image.Resampling.LANCZOS)
            canvas.paste(img, (i * col_w, 0))
    elif len(valid_thumbs) == 3:
        col_w = width // 3
        for i, tp in enumerate(valid_thumbs[:3]):
            img = Image.open(tp).convert("RGB")
            target_ratio = col_w / height
            img_ratio = img.width / img.height
            if img_ratio > target_ratio:
                new_w = int(img.height * target_ratio)
                left = (img.width - new_w) // 2
                img = img.crop((left, 0, left + new_w, img.height))
            else:
                new_h = int(img.width / target_ratio)
                top = (img.height - new_h) // 2
                img = img.crop((0, top, img.width, top + new_h))
            img = img.resize((col_w, height), Image.Resampling.LANCZOS)
            canvas.paste(img, (i * col_w, 0))
    else:
        # 4 or more: 2x2 grid or 4 vertical columns
        col_w = width // min(4, len(valid_thumbs))
        for i, tp in enumerate(valid_thumbs[:4]):
            img = Image.open(tp).convert("RGB")
            target_ratio = col_w / height
            img_ratio = img.width / img.height
            if img_ratio > target_ratio:
                new_w = int(img.height * target_ratio)
                left = (img.width - new_w) // 2
                img = img.crop((left, 0, left + new_w, img.height))
            else:
                new_h = int(img.width / target_ratio)
                top = (img.height - new_h) // 2
                img = img.crop((0, top, img.width, top + new_h))
            img = img.resize((col_w, height), Image.Resampling.LANCZOS)
            canvas.paste(img, (i * col_w, 0))

    # Add dark vignette and bottom banner overlay
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw_ov = ImageDraw.Draw(overlay)

    # Top dark vignette
    draw_ov.rectangle([0, 0, width, 120], fill=(0, 0, 0, 140))
    # Bottom gradient/banner for title
    draw_ov.rectangle([0, height - 160, width, height], fill=(10, 11, 14, 210))

    canvas.paste(overlay, (0, 0), overlay)

    # Text rendering
    draw = ImageDraw.Draw(canvas)

    # Fonts
    font_large = None
    font_small = None
    for font_path in [
        "/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf",
    ]:
        if os.path.exists(font_path):
            try:
                font_large = ImageFont.truetype(font_path, 46)
                font_small = ImageFont.truetype(font_path, 22)
                break
            except Exception:
                pass

    if not font_large:
        font_large = ImageFont.load_default()
        font_small = ImageFont.load_default()

    # Red badge: RED SUN JAPAN
    badge_bg = [40, 30, 240, 68]
    draw.rectangle(badge_bg, fill=(185, 28, 28))
    draw.text((50, 36), "RED SUN JAPAN", fill=(255, 255, 255), font=font_small)

    # Subtitle pill
    draw.text((260, 38), "FULL COMPILATION • RESTORED ARCHIVE", fill=(212, 212, 216), font=font_small)

    # Main title
    clean_title = compilation_title[:45]
    draw.text((40, height - 130), clean_title, fill=(255, 255, 255), font=font_large)
    draw.text((40, height - 60), "FEUDAL CHRONICLES & FOLKLORE FOR DEEP NIGHTS", fill=(244, 63, 94), font=font_small)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, "PNG")
    logger.info("Composite thumbnail successfully saved to %s", output_path)
    return output_path


def stitch_compilation_ffmpeg(
    timeline_data: Dict[str, Any],
    output_path: Path,
    normalize_audio: bool = True,
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> Path:
    """Concatenate episodes using FFmpeg with hardware acceleration, audio fades, and LUFS normalization."""
    episodes = timeline_data["episodes"]
    n = len(episodes)
    if n == 0:
        raise ValueError("Cannot stitch compilation: 0 episodes.")

    if progress_callback:
        progress_callback(10, f"Preparing FFmpeg filtergraph for {n} episodes...")

    # Build FFmpeg command inputs
    input_args: List[str] = []
    filter_parts: List[str] = []

    for idx, ep in enumerate(episodes):
        video_p = ep["video_path"]
        input_args.extend(["-i", video_p])

        eff_dur = ep["effective_duration_seconds"]
        # Video stream: trim, setpts, scale, standardize fps
        filter_parts.append(
            f"[{idx}:v]trim=0:{eff_dur:.3f},setpts=PTS-STARTPTS,fps=30,scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1[v{idx}];"
        )

        # Audio stream: trim, setpts, resample to 48kHz stereo
        # Apply gentle fade-out on intermediate episodes (1.5s)
        fade_out_st = max(0.0, eff_dur - 1.5)
        if idx < n - 1:
            # Intermediate: gentle fade in (1.0s) and fade out (1.5s)
            filter_parts.append(
                f"[{idx}:a]atrim=0:{eff_dur:.3f},asetpts=PTS-STARTPTS,aformat=sample_rates=48000:channel_layouts=stereo,afade=t=in:st=0:d=1.0,afade=t=out:st={fade_out_st:.3f}:d=1.5[a{idx}];"
            )
        else:
            # Last episode: gentle fade in (1.0s) and keep full ending intact
            filter_parts.append(
                f"[{idx}:a]atrim=0:{eff_dur:.3f},asetpts=PTS-STARTPTS,aformat=sample_rates=48000:channel_layouts=stereo,afade=t=in:st=0:d=1.0[a{idx}];"
            )

    # Concat filter
    concat_inputs = "".join([f"[v{i}][a{i}]" for i in range(n)])
    filter_parts.append(f"{concat_inputs}concat=n={n}:v=1:a=1[vcat][acat];")

    # Audio normalization to -14 LUFS, True Peak -1.0 dB
    if normalize_audio:
        filter_parts.append("[acat]loudnorm=I=-14:LRA=7:TP=-1.0[aout]")
    else:
        filter_parts.append("[acat]anull[aout]")

    full_filter = "".join(filter_parts)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_output = output_path.with_name(f"{output_path.stem}_temp.mp4")
    if temp_output.exists():
        temp_output.unlink()

    # Try VideoToolbox hardware encoder on macOS first
    cmd = [
        "ffmpeg",
        "-y",
        *input_args,
        "-filter_complex", full_filter,
        "-map", "[vcat]",
        "-map", "[aout]",
        "-c:v", "h264_videotoolbox",
        "-b:v", "14M",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "320k",
        "-ar", "48000",
        str(temp_output),
    ]

    logger.info("Executing FFmpeg compilation stitch: %s", " ".join(cmd[:12]))
    if progress_callback:
        progress_callback(25, "Encoding compilation master with Apple VideoToolbox (1080p)...")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    _, stderr_text = proc.communicate()

    if proc.returncode != 0:
        logger.warning("VideoToolbox encode failed (code %d), falling back to libx264: %s", proc.returncode, stderr_text[-400:])
        if progress_callback:
            progress_callback(35, "Fallback: Encoding with libx264 software encoder...")
        cmd_fallback = [
            "ffmpeg",
            "-y",
            *input_args,
            "-filter_complex", full_filter,
            "-map", "[vcat]",
            "-map", "[aout]",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "320k",
            "-ar", "48000",
            str(temp_output),
        ]
        proc_fb = subprocess.run(cmd_fallback, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if proc_fb.returncode != 0:
            raise RuntimeError(f"FFmpeg compilation stitching failed: {proc_fb.stderr[-800:]}")

    # Move temp output to final master
    if output_path.exists():
        output_path.unlink()
    shutil.move(str(temp_output), str(output_path))

    if progress_callback:
        progress_callback(90, "Final master video successfully generated and verified.")

    logger.info("FFmpeg compilation completed successfully: %s (%.1f MB)", output_path, output_path.stat().st_size / (1024 * 1024))
    return output_path


def build_compilation_async(
    compilation_id: str,
    title: str,
    selected_slugs: List[str],
    trim_intermediate_outros_seconds: float = 12.0,
    normalize_audio: bool = True,
    playlist_name: Optional[str] = None,
) -> None:
    """Background worker to calculate timeline, stitch video, generate thumbnails and metadata."""
    with _JOB_LOCK:
        ACTIVE_COMPILATION_JOBS[compilation_id] = {
            "compilation_id": compilation_id,
            "title": title,
            "status": "inspecting",
            "progress": 10,
            "current_step": "Inspecting episode durations and calculating timeline...",
            "error": None,
            "created_at": datetime.datetime.utcnow().isoformat(),
            "manifest": None,
        }

    comp_dir = _ensure_compilations_dir() / compilation_id
    comp_dir.mkdir(parents=True, exist_ok=True)

    def update_progress(prog: int, step: str):
        with _JOB_LOCK:
            if compilation_id in ACTIVE_COMPILATION_JOBS:
                ACTIVE_COMPILATION_JOBS[compilation_id]["progress"] = prog
                ACTIVE_COMPILATION_JOBS[compilation_id]["current_step"] = step

    try:
        # 1. Timeline Calculation
        timeline = calculate_compilation_timeline(
            selected_slugs=selected_slugs,
            trim_intermediate_outros_seconds=trim_intermediate_outros_seconds,
        )
        update_progress(20, f"Timeline ready ({timeline['total_duration_formatted']} total). Preparing stitching...")

        # 2. Build metadata & copy
        metadata = generate_compilation_metadata(
            compilation_title=title,
            timeline_data=timeline,
            playlist_name=playlist_name,
        )
        kit_path = comp_dir / "youtube_compilation_kit.json"
        kit_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")

        # 3. Build composite thumbnail
        update_progress(25, "Assembling multi-story composite thumbnail (1280x720)...")
        thumb_paths = [Path(ep["thumbnail_path"]) if ep.get("thumbnail_path") else None for ep in timeline["episodes"]]
        thumbnail_file = comp_dir / "compilation_thumbnail.png"
        build_composite_thumbnail(thumb_paths, thumbnail_file, compilation_title=title)

        # 4. Stitch Master Video via FFmpeg
        master_output = comp_dir / "compilation_master.mp4"
        stitch_compilation_ffmpeg(
            timeline_data=timeline,
            output_path=master_output,
            normalize_audio=normalize_audio,
            progress_callback=update_progress,
        )

        # 5. Save Compilation Manifest
        manifest_data = {
            "compilation_id": compilation_id,
            "compilation_title": title,
            "created_at": datetime.datetime.utcnow().isoformat(),
            "total_duration_seconds": timeline["total_duration_seconds"],
            "total_duration_formatted": timeline["total_duration_formatted"],
            "episodes_count": timeline["episodes_count"],
            "trim_intermediate_outros_seconds": trim_intermediate_outros_seconds,
            "normalize_audio": normalize_audio,
            "episodes": timeline["episodes"],
            "master_video_path": str(master_output),
            "thumbnail_path": str(thumbnail_file),
            "youtube_kit_path": str(kit_path),
            "youtube": {
                "status": "unuploaded",
                "video_id": None,
                "video_url": None,
                "playlist_id": None,
                "playlist_name": metadata.get("playlist_name"),
            },
        }

        manifest_file = comp_dir / "compilation_manifest.json"
        manifest_file.write_text(json.dumps(manifest_data, indent=2, ensure_ascii=False), encoding="utf-8")

        with _JOB_LOCK:
            ACTIVE_COMPILATION_JOBS[compilation_id]["status"] = "completed"
            ACTIVE_COMPILATION_JOBS[compilation_id]["progress"] = 100
            ACTIVE_COMPILATION_JOBS[compilation_id]["current_step"] = "Compilation master ready for dispatch."
            ACTIVE_COMPILATION_JOBS[compilation_id]["manifest"] = manifest_data

        logger.info("Compilation '%s' (%s) successfully built and saved to %s", title, compilation_id, comp_dir)

    except Exception as exc:
        logger.exception("Failed to build compilation '%s': %s", compilation_id, exc)
        with _JOB_LOCK:
            ACTIVE_COMPILATION_JOBS[compilation_id]["status"] = "failed"
            ACTIVE_COMPILATION_JOBS[compilation_id]["progress"] = 100
            ACTIVE_COMPILATION_JOBS[compilation_id]["error"] = str(exc)
            ACTIVE_COMPILATION_JOBS[compilation_id]["current_step"] = f"Failed: {exc}"


def get_compilation_status(compilation_id: str) -> Dict[str, Any]:
    """Retrieve runtime progress or read saved manifest for a compilation."""
    with _JOB_LOCK:
        job = ACTIVE_COMPILATION_JOBS.get(compilation_id)
        if job:
            return dict(job)

    # Check on disk
    comp_dir = COMPILATIONS_DIR / compilation_id
    manifest_file = comp_dir / "compilation_manifest.json"
    if manifest_file.exists():
        try:
            m = json.loads(manifest_file.read_text(encoding="utf-8"))
            return {
                "compilation_id": compilation_id,
                "title": m.get("compilation_title", compilation_id),
                "status": "completed",
                "progress": 100,
                "current_step": "Compilation ready on disk.",
                "error": None,
                "manifest": m,
            }
        except Exception as exc:
            logger.warning("Error reading manifest for %s: %s", compilation_id, exc)

    return {
        "compilation_id": compilation_id,
        "title": compilation_id,
        "status": "not_found",
        "progress": 0,
        "current_step": "Job not found",
        "error": "Compilation ID not found",
        "manifest": None,
    }


def list_compilations() -> List[Dict[str, Any]]:
    """List all compilations stored in projects/_compilations/."""
    _ensure_compilations_dir()
    results = []

    for c_dir in sorted(COMPILATIONS_DIR.iterdir(), reverse=True):
        if not c_dir.is_dir() or c_dir.name.startswith("."):
            continue

        manifest_file = c_dir / "compilation_manifest.json"
        if manifest_file.exists():
            try:
                m = json.loads(manifest_file.read_text(encoding="utf-8"))
                results.append(m)
            except Exception:
                pass
        else:
            # Folder exists but incomplete
            results.append({
                "compilation_id": c_dir.name,
                "compilation_title": c_dir.name.replace("-", " ").title(),
                "created_at": None,
                "status": "incomplete",
            })

    return results


def upload_compilation_to_youtube(
    compilation_id: str,
    title_index: int = 0,
    playlist_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Upload compilation master to YouTube as private draft and assign to playlist."""
    from googleapiclient.http import MediaFileUpload

    comp_dir = COMPILATIONS_DIR / compilation_id
    manifest_file = comp_dir / "compilation_manifest.json"
    kit_file = comp_dir / "youtube_compilation_kit.json"

    if not manifest_file.exists():
        raise HTTPException(status_code=404, detail=f"Compilation manifest not found for {compilation_id}")

    manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))
    kit_data = json.loads(kit_file.read_text(encoding="utf-8")) if kit_file.exists() else {}

    video_path = Path(manifest_data.get("master_video_path", comp_dir / "compilation_master.mp4"))
    if not video_path.exists():
        raise HTTPException(status_code=404, detail=f"Compilation master video not found: {video_path}")

    thumb_path = Path(manifest_data.get("thumbnail_path", comp_dir / "compilation_thumbnail.png"))

    # Resolve Title
    title_options = kit_data.get("title_options", [manifest_data.get("compilation_title", compilation_id)])
    final_title = title_options[title_index] if 0 <= title_index < len(title_options) else title_options[0]

    description = kit_data.get("description", "Red Sun Japan - Multi-Story Compilation")
    tags = kit_data.get("tags", ["Japanese folklore", "Kaidan", "Red Sun Japan"])
    target_playlist = playlist_name or kit_data.get("playlist_name") or "Edo Japan Folklore for Deep Sleep & Rainy Nights (Full Compilations)"

    logger.info("Connecting to YouTube Data API v3 for compilation '%s'...", final_title)
    try:
        youtube = get_youtube_client(interactive=False)
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"YouTube OAuth2 authentication required: {exc}")

    # Upload video as PRIVATE (MANDATORY SAFETY PROTOCOL!)
    body = {
        "snippet": {
            "title": final_title[:100],
            "description": description[:5000],
            "tags": tags[:30],
            "categoryId": "27",  # Education
            "defaultLanguage": "en",
        },
        "status": {
            "privacyStatus": "private",  # Strictly private draft
            "selfDeclaredMadeForKids": False,
        },
    }

    logger.info(
        "Uploading compilation master (%s, %.1f MB) to YouTube as private draft...",
        video_path.name,
        video_path.stat().st_size / (1024 * 1024),
    )

    media = MediaFileUpload(
        str(video_path),
        mimetype="video/mp4",
        chunksize=8 * 1024 * 1024,
        resumable=True,
    )

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            logger.info("[YouTube Compilation] Upload progress: %d%%", int(status.progress() * 100))

    video_id = response.get("id")
    if not video_id:
        raise RuntimeError(f"YouTube API returned no video ID: {response}")

    video_url = f"https://youtu.be/{video_id}"
    logger.info("Compilation uploaded successfully to YouTube: %s", video_url)

    # Upload Custom Thumbnail
    if thumb_path.exists():
        try:
            logger.info("Uploading compilation thumbnail: %s", thumb_path)
            thumb_media = MediaFileUpload(str(thumb_path), mimetype="image/png")
            youtube.thumbnails().set(videoId=video_id, media_body=thumb_media).execute()
            logger.info("Compilation thumbnail attached to %s", video_id)
        except Exception as thumb_exc:
            logger.warning("Failed to upload compilation thumbnail: %s", thumb_exc)

    # Assign to Playlist via YouTube Data API v3
    assigned_playlist_id = None
    if target_playlist:
        try:
            logger.info("Querying playlists for '%s'...", target_playlist)
            pl_req = youtube.playlists().list(part="snippet", mine=True, maxResults=50)
            pl_res = pl_req.execute()

            for item in pl_res.get("items", []):
                if item.get("snippet", {}).get("title", "").strip().lower() == target_playlist.strip().lower():
                    assigned_playlist_id = item.get("id")
                    break

            if not assigned_playlist_id:
                logger.info("Creating new playlist '%s'...", target_playlist)
                create_pl = youtube.playlists().insert(
                    part="snippet,status",
                    body={
                        "snippet": {
                            "title": target_playlist,
                            "description": "Full-length feudal Japanese folklore compilations and uncut ambient chronicles from Red Sun Japan.",
                        },
                        "status": {
                            "privacyStatus": "private",
                        },
                    },
                ).execute()
                assigned_playlist_id = create_pl.get("id")

            if assigned_playlist_id:
                youtube.playlistItems().insert(
                    part="snippet",
                    body={
                        "snippet": {
                            "playlistId": assigned_playlist_id,
                            "resourceId": {
                                "kind": "youtube#video",
                                "videoId": video_id,
                            },
                        },
                    },
                ).execute()
                logger.info("Video %s successfully added to playlist '%s' (%s)", video_id, target_playlist, assigned_playlist_id)
        except Exception as pl_exc:
            logger.warning("Could not add video to playlist: %s", pl_exc)

    # Update manifest
    manifest_data["youtube"] = {
        "status": "private",
        "video_id": video_id,
        "video_url": video_url,
        "playlist_id": assigned_playlist_id,
        "playlist_name": target_playlist,
        "uploaded_at": datetime.datetime.utcnow().isoformat(),
    }
    manifest_file.write_text(json.dumps(manifest_data, indent=2, ensure_ascii=False), encoding="utf-8")

    return {
        "success": True,
        "compilation_id": compilation_id,
        "video_id": video_id,
        "video_url": video_url,
        "playlist_id": assigned_playlist_id,
        "playlist_name": target_playlist,
        "privacy_status": "private",
    }
