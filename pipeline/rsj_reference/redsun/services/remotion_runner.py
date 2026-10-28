"""Remotion Render Orchestration Service for Red Sun Japan.

Handles:
1. Preparation of Remotion manifest + assets synchronization:
   - Scans gemini-assistant/output/ for real generated scene images (.png, .jpg, .jpeg, .webp).
   - Validates existence of audio_main.wav and subtitles_en.json.
   - Generates manifest.json with exact duration, dimensions and asset maps.
   - Syncs all assets into render-remotion/public/.
2. Remotion CLI invocation with real-time logging:
   - Executes `npx remotion render RedSunJapanVideo <slug>_main.mp4`.
   - Captures stdout & stderr in real-time to ws.pipeline_log_path.
   - Transitions project status in database to 'rendered'.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import signal
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from database import SessionLocal, VideoProject, VideoStatus, TranslationVariant
from redsun.workspace import ProjectWorkspace
from redsun.services.audio_manager import AudioManager

logger = logging.getLogger("redsun.remotion_runner")

# Repositories & runtime directories
_MOTOR_DIR = Path(__file__).resolve().parent.parent.parent
_REPO_ROOT = _MOTOR_DIR.parent
REMOTION_DIR = _REPO_ROOT / "render-remotion"
REMOTION_PUBLIC = REMOTION_DIR / "public"


def _get_remotion_bin() -> str:
    """Returns absolute path to local Remotion binary if available, avoiding npx wrapper."""
    local_bin = REMOTION_DIR / "node_modules" / ".bin" / "remotion"
    if local_bin.exists():
        return str(local_bin.resolve())
    return "remotion"

# Valid image extensions (case-insensitive)
VALID_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}

# Active render worker registry: slug -> { "thread": Thread, "started_at": str, "status": str, "error": str | None, "cancelled": bool }
ACTIVE_RENDER_JOBS: dict[str, dict[str, Any]] = {}
# Active subprocess tracker: slug -> subprocess.Popen (for kill switch / process group termination)
ACTIVE_RENDER_PROCESSES: dict[str, subprocess.Popen] = {}
_LOCK = threading.Lock()


def _log_to_pipeline(ws: ProjectWorkspace, level: str, message: str) -> None:
    """Appends a structured log entry into ws.pipeline_log_path with ISO timestamp."""
    try:
        ws.logs_dir.mkdir(parents=True, exist_ok=True)
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        entry = f"[{now_str}] [{level.upper()}] {message}\n"
        with open(ws.pipeline_log_path, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception as exc:
        logger.error("Failed to write to pipeline.log for %s: %s", ws.technical_slug, exc)


def get_pipeline_logs(slug: str, max_lines: int = 150) -> list[str]:
    """Returns the last N lines from the project's pipeline.log."""
    try:
        ws = ProjectWorkspace.from_slug(slug)
        if ws.pipeline_log_path.exists():
            lines = ws.pipeline_log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            return lines[-max_lines:]
    except Exception:
        pass
    return []


def cleanup_and_unify_project_storage(slug: str) -> dict[str, Any]:
    """Deterministic hygiene routine: unifies storage exclusively in gemini-assistant/ and removes legacy folders."""
    ws = ProjectWorkspace.from_slug(slug)
    removed_dirs = []
    removed_files = 0
    freed_bytes = 0

    legacy_dirs = [
        ws.gemini_assistant_dir / "01-references",
        ws.gemini_assistant_dir / "02-scenes",
        ws.gemini_assistant_dir / "reference-generation",
        ws.gemini_assistant_dir / "expected",
        ws.root / "01-references",
        ws.root / "02-scenes",
        ws.root / "reference-generation",
        ws.root / "expected",
        Path.home() / "Downloads" / "Gemini Assistant" / slug,
        Path.home() / "Downloads" / "Gemini Assistant" / f"{slug}-references",
        Path.home() / "Downloads" / "Gemini Assistant" / f"{slug}-scenes",
    ]

    # 1. Deterministic purge of redundant projects/<slug>/shorts
    legacy_shorts = ws.root / "shorts"
    if legacy_shorts.exists() and legacy_shorts.is_dir():
        dest_shorts = ws.root / "video" / "shorts"
        dest_shorts.mkdir(parents=True, exist_ok=True)
        for item in legacy_shorts.iterdir():
            if item.is_file():
                dest_file = dest_shorts / item.name
                if not dest_file.exists():
                    shutil.copy2(item, dest_file)
        shutil.rmtree(legacy_shorts, ignore_errors=True)
        removed_dirs.append(str(legacy_shorts))
        logger.info("Purged redundant legacy shorts folder: %s", legacy_shorts)

    # 2. Strict Single Video Rule: Only video/<slug>_master.mp4 may remain
    video_dir = ws.root / "video"
    video_dir.mkdir(parents=True, exist_ok=True)
    canonical_master = video_dir / f"{slug}_master.mp4"

    if not canonical_master.exists() or canonical_master.stat().st_size < 1000:
        # Search for valid video among duplicates
        candidate_paths = [
            video_dir / f"{slug}_final.mp4",
            video_dir / "output_master.mp4",
            ws.root / "output" / f"{slug}_final.mp4",
            ws.root / "output" / "output_master_with_bumper.mp4",
            ws.root / "render" / f"{slug}_master_with_bumpers.mp4",
            ws.root / "render" / "output_master.mp4",
            ws.root / "render" / "output_master_raw.mp4",
            REMOTION_DIR / f"{slug}_final.mp4",
        ]
        for cand in candidate_paths:
            if cand.exists() and cand.is_file() and cand.stat().st_size > 10000:
                shutil.copy2(cand, canonical_master)
                logger.info("Promoted %s to canonical master: %s", cand, canonical_master)
                break

    # Delete all other long video copies in video/
    for extra_mp4 in [
        video_dir / f"{slug}_final.mp4",
        video_dir / "output_master.mp4",
        REMOTION_DIR / f"{slug}_final.mp4",
    ]:
        if extra_mp4.exists():
            try:
                freed_bytes += extra_mp4.stat().st_size
                extra_mp4.unlink()
                removed_files += 1
            except Exception:
                pass

    # 3. Eliminate output/ folder completely
    legacy_output = ws.root / "output"
    if legacy_output.exists():
        try:
            for f in legacy_output.rglob("*"):
                if f.is_file():
                    freed_bytes += f.stat().st_size
                    removed_files += 1
            shutil.rmtree(legacy_output, ignore_errors=True)
            removed_dirs.append(str(legacy_output))
            logger.info("Purged redundant output folder: %s", legacy_output)
        except Exception as exc:
            logger.debug("Could not remove output dir %s: %s", legacy_output, exc)

    # 4. Clean up render/ temporary chunks and raw renders
    render_dir = ws.root / "render"
    if render_dir.exists():
        for r_file in list(render_dir.iterdir()):
            if r_file.is_file():
                try:
                    freed_bytes += r_file.stat().st_size
                    r_file.unlink()
                    removed_files += 1
                except Exception:
                    pass

    # 5. Eliminate post_kit/ folder completely (migrate unique files to distribution/)
    legacy_post_kit = ws.root / "post_kit"
    if legacy_post_kit.exists():
        dist_dir = ws.root / "distribution"
        dist_dir.mkdir(parents=True, exist_ok=True)
        try:
            for item in legacy_post_kit.iterdir():
                if item.is_file():
                    freed_bytes += item.stat().st_size
                    removed_files += 1
            shutil.rmtree(legacy_post_kit, ignore_errors=True)
            removed_dirs.append(str(legacy_post_kit))
            logger.info("Purged redundant post_kit folder: %s", legacy_post_kit)
        except Exception as exc:
            logger.debug("Could not remove post_kit dir %s: %s", legacy_post_kit, exc)

    # 6. Clean up orphaned root files
    root_audio = ws.root / "audio_main.wav"
    canonical_audio = ws.audio_dir / "audio_main.wav"
    if root_audio.exists():
        if not canonical_audio.exists() or canonical_audio.stat().st_size < 100:
            canonical_audio.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root_audio, canonical_audio)
        try:
            freed_bytes += root_audio.stat().st_size
            root_audio.unlink()
            removed_files += 1
        except Exception:
            pass

    root_subtitles = ws.root / "subtitles_en.json"
    canonical_subtitles = ws.text_dir / "subtitles_en.json"
    if root_subtitles.exists():
        if not canonical_subtitles.exists() or canonical_subtitles.stat().st_size < 50:
            canonical_subtitles.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root_subtitles, canonical_subtitles)
        try:
            freed_bytes += root_subtitles.stat().st_size
            root_subtitles.unlink()
            removed_files += 1
        except Exception:
            pass

    root_manifest = ws.root / "manifest.json"
    if root_manifest.exists():
        try:
            freed_bytes += root_manifest.stat().st_size
            root_manifest.unlink()
            removed_files += 1
        except Exception:
            pass

    # Check any glob matching in ~/Downloads/Gemini Assistant
    dl_ga = Path.home() / "Downloads" / "Gemini Assistant"
    if dl_ga.exists():
        for item in dl_ga.glob(f"*{slug}*"):
            if item.is_dir() and item not in legacy_dirs:
                legacy_dirs.append(item)

    for ld in legacy_dirs:
        if ld.exists():
            try:
                if ld.is_dir():
                    shutil.rmtree(ld, ignore_errors=True)
                    removed_dirs.append(str(ld))
            except Exception as exc:
                logger.debug("Could not remove legacy dir %s: %s", ld, exc)

    ws.gemini_references_dir.mkdir(parents=True, exist_ok=True)
    ws.gemini_output_dir.mkdir(parents=True, exist_ok=True)

    # Remove duplicates from images_dir if they already exist in gemini_output_dir
    if ws.images_dir.exists():
        for f in list(ws.images_dir.iterdir()):
            if f.is_file() and f.suffix.lower() in VALID_IMAGE_EXTS:
                if (ws.gemini_output_dir / f.name).exists():
                    try:
                        freed_bytes += f.stat().st_size
                        f.unlink()
                        removed_files += 1
                    except Exception:
                        pass

    return {
        "slug": slug,
        "removed_dirs": removed_dirs,
        "removed_files_count": removed_files,
        "freed_bytes": freed_bytes,
        "freed_mb": round(freed_bytes / (1024 * 1024), 2),
        "references_dir": str(ws.gemini_references_dir),
        "scenes_dir": str(ws.gemini_output_dir),
    }


def prepare_remotion_assets_and_manifest(slug: str) -> dict[str, Any]:
    """Audits input files and materializes render-remotion/public/manifest.json + assets.

    Step 10: Remotion Manifest Generation.
    """
    ws = ProjectWorkspace.from_slug(slug)
    ws.root.mkdir(parents=True, exist_ok=True)
    ws.logs_dir.mkdir(parents=True, exist_ok=True)
    ws.checkpoints_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Preparing Remotion assets and manifest for slug=%s", slug)
    _log_to_pipeline(ws, "INFO", f"Iniciando Passo #10: Remotion Manifest Generation para {slug}")

    # Hygiene: cleanup legacy folders before manifest generation
    cleanup_and_unify_project_storage(slug)

    # Materialize canonical image tree (symlinks or verified mirror)
    ws.materialize_canonical_image_tree()

    # 1. Scan images from gemini-assistant/output/
    output_dir = ws.gemini_output_dir
    images: list[str] = []
    if output_dir.exists():
        images = sorted([
            f.name for f in output_dir.iterdir()
            if f.is_file() and f.suffix.lower() in VALID_IMAGE_EXTS and not f.name.startswith(".")
        ])

    # Fallback to images_final_dir or images_dir if output_dir is empty
    if not images and ws.images_final_dir.exists():
        images = sorted([
            f.name for f in ws.images_final_dir.iterdir()
            if f.is_file() and f.suffix.lower() in VALID_IMAGE_EXTS and not f.name.startswith(".")
        ])

    if not images and ws.images_dir.exists():
        images = sorted([
            f.name for f in ws.images_dir.iterdir()
            if f.is_file() and f.suffix.lower() in VALID_IMAGE_EXTS and not f.name.startswith(".")
        ])

    if not images:
        msg = f"Nenhuma imagem válida encontrada em {output_dir} para compilação Remotion."
        _log_to_pipeline(ws, "ERROR", msg)
        raise FileNotFoundError(msg)

    _log_to_pipeline(ws, "INFO", f"Passo #10: Detectadas {len(images)} imagens de cena (.jpeg/.jpg/.png).")

    # 2. Confirm audio_main.wav existence
    audio_file = ws.audio_dir / "audio_main.wav"
    if not audio_file.exists():
        # Check fallback candidates
        candidate_paths = [
            ws.root / "audio_main.wav",
            ws.audio_main_path,
            Path("/Users/isamumatsuyama/Documents/red-sun-japan-runtime/render-remotion/public/audio_en.wav"),
            Path("/Users/isamumatsuyama/Documents/red-sun-japan-runtime/motor-python/storage/27/audio_en.wav"),
            Path("/Users/isamumatsuyama/Documents/online-projects/red-sun-japan/red-sun-project-files/channel-redsunjapan/render-remotion/public/audio_en.wav"),
        ]
        found_audio = None
        for cand in candidate_paths:
            if cand.exists() and cand.is_file() and cand.stat().st_size > 1000:
                found_audio = cand
                break

        if found_audio:
            ws.audio_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(found_audio, audio_file)
            _log_to_pipeline(ws, "INFO", f"Passo #10: audio_main.wav recuperado e alocado de {found_audio} ({audio_file.stat().st_size} bytes)")
        else:
            msg = f"Arquivo de áudio audio_main.wav não encontrado em {ws.audio_dir} nem em fallbacks."
            _log_to_pipeline(ws, "ERROR", msg)
            raise FileNotFoundError(msg)

    # 3. Confirm subtitles_en.json existence
    subtitles_file = ws.text_dir / "subtitles_en.json"
    if not subtitles_file.exists():
        alt_subs = [
            ws.root / "subtitles_en.json",
            ws.root / "transcript.json",
            ws.text_dir / "transcript.json",
        ]
        for alt in alt_subs:
            if alt.exists() and alt.is_file():
                ws.text_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(alt, subtitles_file)
                _log_to_pipeline(ws, "INFO", f"Passo #10: subtitles_en.json materializado de {alt}")
                break

    if not subtitles_file.exists():
        msg = f"Arquivo de alinhamento de legendas subtitles_en.json não encontrado em {ws.text_dir}."
        _log_to_pipeline(ws, "ERROR", msg)
        raise FileNotFoundError(msg)

    # 4. Compute accurate duration from subtitles or audio
    duration_sec = 60.0
    try:
        subs_data = json.loads(subtitles_file.read_text(encoding="utf-8"))
        if isinstance(subs_data, list) and subs_data:
            last_end = float(subs_data[-1].get("end", 0.0))
            if last_end > 0:
                duration_sec = last_end + 1.0
    except Exception as exc:
        logger.warning("Could not read duration from subtitles: %s", exc)

    # Also check wave duration if possible
    try:
        import wave
        with wave.open(str(audio_file), "rb") as w:
            rate = w.getframerate()
            frames = w.getnframes()
            if rate and frames:
                duration_sec = max(duration_sec, frames / rate)
    except Exception:
        pass

    # 5. Build Remotion Manifest Dictionary
    p_id = 1
    p_title = slug
    p_title_ja = ""
    db = SessionLocal()
    try:
        project_row = db.query(VideoProject).filter(VideoProject.slug == slug).first()
        if project_row:
            p_id = project_row.id
            p_title = project_row.title or slug
            p_title_ja = getattr(project_row, "title_ja", "") or ""
            variant = db.query(TranslationVariant).filter(
                TranslationVariant.project_id == project_row.id,
                TranslationVariant.language_code == "en"
            ).first()
            if variant:
                variant.audio_path = str(audio_file.resolve())
                variant.timestamps_path = str(subtitles_file.resolve())
                db.commit()
    finally:
        db.close()

    # 5. Deterministic BGM selection via AudioManager
    import re
    db_bgm = SessionLocal()
    selected_bgm_meta = None
    bgm_filename = None
    bgm_src_path = None
    try:
        if p_id:
            selected_bgm_meta = AudioManager.select_next_bgm(db_bgm, p_id)
            if selected_bgm_meta:
                bgm_src_path = AudioManager.get_bgm_file_path(selected_bgm_meta["id"])
                if bgm_src_path and bgm_src_path.exists():
                    raw_id = str(selected_bgm_meta["id"]).strip()
                    clean_id = re.sub(r"^(bgm_)+", "bgm_", raw_id)
                    if not clean_id.startswith("bgm_"):
                        clean_id = f"bgm_{clean_id}"
                    bgm_filename = f"{clean_id}{bgm_src_path.suffix}"
                    _log_to_pipeline(
                        ws,
                        "INFO",
                        f"Passo #10: Trilha BGM vinculada: '{selected_bgm_meta.get('title')}' por {selected_bgm_meta.get('artist')} ({selected_bgm_meta.get('license')}).",
                    )
    except Exception as exc:
        logger.warning("Error resolving BGM via AudioManager: %s", exc)
    finally:
        db_bgm.close()

    if not bgm_filename or not bgm_src_path or not bgm_src_path.exists():
        # Fallback to catalog or existing audio
        try:
            catalog = AudioManager.load_catalog()
            if catalog:
                selected_bgm_meta = catalog[0]
                bgm_src_path = AudioManager.get_bgm_file_path(selected_bgm_meta["id"])
                if bgm_src_path and bgm_src_path.exists():
                    raw_id = str(selected_bgm_meta["id"]).strip()
                    clean_id = re.sub(r"^(bgm_)+", "bgm_", raw_id)
                    if not clean_id.startswith("bgm_"):
                        clean_id = f"bgm_{clean_id}"
                    bgm_filename = f"{clean_id}{bgm_src_path.suffix}"
        except Exception as exc:
            logger.warning("Fallback catalog BGM error: %s", exc)

    if not bgm_filename:
        # Fallback to any available audio file or none
        flacs = list(REMOTION_PUBLIC.glob("*.flac")) + list(REMOTION_PUBLIC.glob("*.mp3"))
        if flacs:
            bgm_filename = flacs[0].name
            bgm_src_path = flacs[0]
        else:
            bgm_filename = None
            bgm_src_path = None

    # 5.1 Identify Archival Bridge window and scene index
    archival_bridge_scene_idx = 0
    arch_start = 45.0
    arch_end = 65.0
    arch_kw = re.compile(
        r"\b(story|written|manuscript|recorded|archive|collection|period|era|century|chronicle|account)\b",
        re.IGNORECASE,
    )
    try:
        if isinstance(subs_data, list) and subs_data and images and duration_sec > 0:
            candidate_times = []
            for item in subs_data:
                t = float(item.get("start", 0.0))
                txt = str(item.get("text") or item.get("word") or "")
                if 35.0 <= t <= 90.0 and arch_kw.search(txt):
                    candidate_times.append(t)
            target_t = candidate_times[0] if candidate_times else 50.0
            archival_bridge_scene_idx = min(
                len(images) - 1, max(0, int((target_t / duration_sec) * len(images)))
            )
            arch_start = round((archival_bridge_scene_idx / len(images)) * duration_sec, 2)
            arch_end = round(((archival_bridge_scene_idx + 1) / len(images)) * duration_sec, 2)
    except Exception as exc:
        logger.debug("Archival bridge calculation notice: %s", exc)

    manifest = {
        "project_id": p_id,
        "slug": slug,
        "language_code": "en",
        "title_en": p_title,
        "title_ja": p_title_ja,
        "audio_duration_seconds": round(duration_sec, 2),
        "duration_seconds": round(duration_sec + 5.0, 2),
        "fps": 30,
        "width": 1920,
        "height": 1080,
        "audio_filename": "audio_en.wav",
        "timestamps_filename": "timestamps.json",
        "image_theme": "historical_dark_fantasy",
        "image_count": len(images),
        "image_filenames": images,
        "bgm_filename": bgm_filename,
        "bgm_mood": "horror_mystery",
        "bgm_track": selected_bgm_meta,
        "archival_bridge_scene_index": archival_bridge_scene_idx,
        "archival_bridge_start_sec": arch_start,
        "archival_bridge_end_sec": arch_end,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    # 6. Save manifest in workspace
    manifest_bytes = json.dumps(manifest, indent=2, ensure_ascii=False)
    ws.manifest_path.write_text(manifest_bytes, encoding="utf-8")
    (ws.root / "render").mkdir(parents=True, exist_ok=True)
    (ws.root / "render" / "remotion_manifest.json").write_text(manifest_bytes, encoding="utf-8")

    # 7. Sync assets into render-remotion/public/
    from redsun.services.shorts_service import ShortsService
    ShortsService.purge_remotion_public()
    REMOTION_PUBLIC.mkdir(parents=True, exist_ok=True)

    # Build or update scene_timing.json with isArchivalBridge
    if images and duration_sec > 0:
        sec_per_img = duration_sec / len(images)
        scene_timings = []
        for i in range(len(images)):
            s_sec = round(i * sec_per_img, 2)
            e_sec = round((i + 1) * sec_per_img, 2)
            scene_timings.append({
                "scene_index": i + 1,
                "start_sec": s_sec,
                "end_sec": e_sec,
                "duration_sec": round(e_sec - s_sec, 2),
                "merged_from": [i + 1],
                "isArchivalBridge": (i == archival_bridge_scene_idx),
            })
        timing_bytes = json.dumps(scene_timings, indent=2)
        (ws.root / "scene_timing.json").write_text(timing_bytes, encoding="utf-8")
        (REMOTION_PUBLIC / "scene_timing.json").write_text(timing_bytes, encoding="utf-8")

    # Copy manifest
    (REMOTION_PUBLIC / "manifest.json").write_text(manifest_bytes, encoding="utf-8")

    # Copy images
    img_source_dir = output_dir if (output_dir / images[0]).exists() else (
        ws.images_final_dir if (ws.images_final_dir / images[0]).exists() else ws.images_dir
    )
    for img_name in images:
        src = img_source_dir / img_name
        dst = REMOTION_PUBLIC / img_name
        if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
            shutil.copy2(src, dst)

    # Copy audio as BOTH audio_en.wav and audio_main.wav
    shutil.copy2(audio_file, REMOTION_PUBLIC / "audio_en.wav")
    shutil.copy2(audio_file, REMOTION_PUBLIC / "audio_main.wav")

    # Copy institutional bumpers (intro.mp4, outro.mp4)
    intro_bumper = _find_bumper_file("intro")
    if intro_bumper and intro_bumper.exists():
        shutil.copy2(intro_bumper, REMOTION_PUBLIC / "intro.mp4")
    outro_bumper = _find_bumper_file("outro")
    if outro_bumper and outro_bumper.exists():
        shutil.copy2(outro_bumper, REMOTION_PUBLIC / "outro.mp4")

    # Copy timestamps as timestamps.json
    shutil.copy2(subtitles_file, REMOTION_PUBLIC / "timestamps.json")

    # Copy BGM file
    if bgm_filename and bgm_src_path and bgm_src_path.exists():
        bgm_dst = REMOTION_PUBLIC / bgm_filename
        shutil.copy2(bgm_src_path, bgm_dst)

    # Validate that BGM exists physically and has size > 0 bytes
    if bgm_filename:
        bgm_dst = REMOTION_PUBLIC / bgm_filename
        if not bgm_dst.exists() or bgm_dst.stat().st_size == 0:
            err_msg = f"Arquivo de BGM '{bgm_filename}' ausente ou com 0 bytes em {REMOTION_PUBLIC} pós-sincronização."
            _log_to_pipeline(ws, "ERROR", err_msg)
            raise FileNotFoundError(err_msg)
        _log_to_pipeline(
            ws,
            "INFO",
            f"Passo #10: Trilha BGM sincronizada e validada: {bgm_filename} ({bgm_dst.stat().st_size} bytes)."
        )

    # Clean stale scene_timing.json unless project explicitly defines one
    stale_timing = REMOTION_PUBLIC / "scene_timing.json"
    ws_timing = ws.root / "scene_timing.json"
    if ws_timing.exists():
        shutil.copy2(ws_timing, stale_timing)
    elif stale_timing.exists():
        try:
            stale_timing.unlink()
        except Exception:
            pass

    # Bundle fresh Remotion code so all chunk renders share the exact bundle
    try:
        build_dir = REMOTION_DIR / "build"
        if build_dir.exists():
            shutil.rmtree(build_dir, ignore_errors=True)
        subprocess.run(
            [_get_remotion_bin(), "bundle", "src/index.ts", "build"],
            cwd=str(REMOTION_DIR),
            check=True,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=60,
        )
    except Exception as exc:
        logger.warning("Remotion bundle build warning: %s", exc)

    _log_to_pipeline(ws, "SUCCESS", f"Passo #10 Concluído: Remotion Manifest criado com {len(images)} imagens e duração de {duration_sec:.1f}s.")
    return manifest


def _find_bumper_file(name: str) -> Path | None:
    """Finds intro or outro bumper video in standard assets or branding directories."""
    candidates = [
        _REPO_ROOT / "channel-redsunjapan" / "assets" / f"{name}.mp4",
        _REPO_ROOT / "assets" / f"{name}.mp4",
        _REPO_ROOT / "assets" / f"{name}_norm.mp4",
        _REPO_ROOT / "render-remotion" / "public" / f"redsun_{name}.mp4",
        Path("/Users/isamumatsuyama/Documents/online-projects/red-sun-japan/branding") / f"red sun japan {name}.mp4",
        Path("/Users/isamumatsuyama/Documents/online-projects/red-sun-japan/branding") / f"red sun japan {'intro' if name == 'intro' else 'ending'}.mp4",
    ]
    for c in candidates:
        if c.exists() and c.is_file() and c.stat().st_size > 10000:
            return c
    return None


def _normalize_bumper_if_needed(bumper: Path) -> Path:
    """Ensures bumper video timescale is 90000 to prevent FFmpeg -c copy timescale corruption."""
    if not bumper or not bumper.exists():
        return bumper
    try:
        p = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=time_base", "-of", "default=noprint_wrappers=1:nokey=1", str(bumper.resolve())],
            capture_output=True, text=True
        )
        tb = p.stdout.strip()
        if tb == "1/90000":
            return bumper
        norm_file = bumper.parent / f"{bumper.stem}_norm90k.mp4"
        if not norm_file.exists() or norm_file.stat().st_size < 10000:
            subprocess.run([
                "ffmpeg", "-y", "-i", str(bumper.resolve()),
                "-video_track_timescale", "90000",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
                "-c:a", "aac", "-b:a", "320k", "-ar", "48000",
                str(norm_file.resolve())
            ], check=True, capture_output=True)
        return norm_file
    except Exception as exc:
        logger.warning("Bumper normalization check failed for %s: %s", bumper, exc)
        return bumper


def _compute_file_md5(file_path: Path, block_size: int = 65536) -> str:
    """Calculates MD5 hash of a physical file."""
    hasher = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(block_size), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _stitch_master_with_bumpers(slug: str, main_video: Path, ws: ProjectWorkspace) -> Path:
    """Exports Master directly from native Remotion composition.

    External intro.mp4 and outro.mp4 FFmpeg stitching has been deactivated.
    Atmospheric branding (00:00-00:18), silent mid-roll CTA (at 30%), and
    sleep-safe end screen (last 15s) are now rendered natively within Remotion.
    """
    output_with_bumpers = ws.root / "render" / f"{slug}_master_with_bumpers.mp4"
    shutil.copy2(main_video, output_with_bumpers)
    _log_to_pipeline(
        ws,
        "SUCCESS",
        f"Passo #12/13 Concluído: Master exportado diretamente da composição principal Remotion com branding nativo ({output_with_bumpers.stat().st_size} bytes)."
    )
    return output_with_bumpers



def _execute_parallel_step_14_blog(slug: str, ws: ProjectWorkspace, title: str, script: str) -> Path | None:
    _log_to_pipeline(ws, "INFO", "Passo #14: Gerando e exportando artigo para o blog Astro com QA Linter e Media Curator...")
    ws.blog_dir.mkdir(parents=True, exist_ok=True)
    (ws.root / "text").mkdir(parents=True, exist_ok=True)

    try:
        from redsun.services.render_service import RenderService
        dest_astro = RenderService.execute_step_14_blog(
            slug=slug,
            title=title,
            script=script,
            workspace_root=ws.root,
        )
        _log_to_pipeline(ws, "SUCCESS", f"Passo #14 Concluído: Artigo Blog Astro publicado com sucesso em redsunjapan-blog/src/content/blog/{slug}.md com imagens curadas.")
        return dest_astro
    except Exception as exc:
        _log_to_pipeline(ws, "ERROR", f"Passo #14 Erro: Falha na orquestração do blog: {exc}")
        logger.exception("Failed Step #14 blog generation: %s", exc)
        return None


def _execute_parallel_step_15_shorts(
    slug: str,
    ws: ProjectWorkspace,
    title: str,
    script: str,
    subtitles_file: Path,
    master_video: Path,
) -> list[Path]:
    _log_to_pipeline(ws, "INFO", "Passo #15: Curadoria de momentos dramáticos e renderização de 3 Shorts Verticais nativos (9:16)...")
    from redsun.services.shorts_service import ShortsService

    # Resolve audio file
    audio_file = ws.audio_dir / "audio_main.wav"
    if not audio_file.exists():
        audio_file = ws.audio_main_path
    if not audio_file.exists():
        audio_file = REMOTION_PUBLIC / "audio_en.wav"

    def ws_log_adapter(level: str, msg: str):
        _log_to_pipeline(ws, level, msg)

    try:
        generated_shorts = ShortsService.render_shorts(
            slug=slug,
            project_dir=ws.root,
            title=title,
            subtitles_file=subtitles_file,
            audio_file=audio_file,
            force_regenerate=True,
            ws_logger=ws_log_adapter,
        )
        return generated_shorts
    except Exception as exc:
        _log_to_pipeline(ws, "ERROR", f"Passo #15 Falha Crítica nos Shorts: {exc}")
        logger.exception("Failed Step #15 shorts generation for %s: %s", slug, exc)
        raise exc


def _execute_parallel_step_16_post_kit(
    slug: str,
    ws: ProjectWorkspace,
    title: str,
    duration_sec: float,
    image_count: int,
    subtitles_file: Path,
) -> dict[str, Any]:
    _log_to_pipeline(ws, "INFO", "Passo #16: Gerando Universal Distribution Kit (YouTube, Pinterest, Shorts, X/Twitter)...")
    try:
        from redsun.services.render_service import RenderService
        kit = RenderService.execute_step_16_distribution(slug, workspace_root=ws.root, force_regenerate=True)
        _log_to_pipeline(ws, "SUCCESS", "Passo #16 Concluído: Universal Distribution Kit gerado com sucesso (distribution/).")
        return kit
    except Exception as exc:
        logger.exception("Failed to generate Universal Distribution Kit for %s: %s", slug, exc)
        _log_to_pipeline(ws, "WARNING", f"Passo #16: Fallback básico ativado ({exc})")

        post_kit_dir = ws.root / "post_kit"
        post_kit_dir.mkdir(parents=True, exist_ok=True)

        chapters = ["00:00 - Introduction & Historical Context"]
        if duration_sec > 180:
            c1 = duration_sec * 0.25
            c2 = duration_sec * 0.50
            c3 = duration_sec * 0.75
            m1, s1 = int(c1 // 60), int(c1 % 60)
            m2, s2 = int(c2 // 60), int(c2 % 60)
            m3, s3 = int(c3 // 60), int(c3 % 60)
            chapters.append(f"{m1:02d}:{s1:02d} - The Ominous Encounter")
            chapters.append(f"{m2:02d}:{s2:02d} - The Ghost Curse Unfolds")
            chapters.append(f"{m3:02d}:{s3:02d} - Retribution & Climax")
        m_end, s_end = int(duration_sec // 60), int(duration_sec % 60)
        chapters.append(f"{m_end:02d}:{s_end:02d} - The Legacy of the Tale")

        title_options = [
            f"{title}: The Chilling Ghost Legend of Edo Japan",
            f"The Terrifying Secret Behind {title} (Japanese Kaidan Documentary)",
            f"Why Feudal Japan Whispered the Curse of {title}",
        ]

        tags = [
            "Red Sun Japan", "Kaidan", "Japanese Folklore", "Yokai", "Edo Period",
            "Ghost Story", "Horror Documentary", "Japanese Mythology", "Historical Japan"
        ]

        description_text = f"""{title_options[0]}

Step into the atmospheric world of Edo period Japan with this authentic Kaidan chronicle of {title}.

TIMESTAMPS:
{chr(10).join(chapters)}

SUBSCRIBE to Red Sun Japan for weekly deep-dives into authentic Japanese folklore, samurai history, and legendary Kaidan ghost stories.

#RedSunJapan #Kaidan #JapaneseFolklore #Yokai #EdoJapan #HorrorDocumentary
"""

        (post_kit_dir / "youtube_description.txt").write_text(description_text, encoding="utf-8")

        post_kit_data = {
            "slug": slug,
            "title": title,
            "title_options": title_options,
            "video_master": f"video/{slug}_master.mp4",
            "duration_seconds": duration_sec,
            "image_count": image_count,
            "chapters": chapters,
            "tags": tags,
            "thumbnail_prompt": f"Dramatic cinematic woodblock ukiyo-e composition depicting {title}, low candle flame glow, deep indigo mist, terrifying traditional presence, ultra detailed, 16:9",
            "rendered_at": datetime.now(timezone.utc).isoformat(),
            "status": "READY_FOR_PUBLISHING"
        }

        (post_kit_dir / "post_kit.json").write_text(json.dumps(post_kit_data, indent=2, ensure_ascii=False), encoding="utf-8")
        _log_to_pipeline(ws, "SUCCESS", "Passo #16 Concluído: Post-kit YouTube gerado com sucesso (fallback).")
        return post_kit_data


def _remotion_render_worker(slug: str, concurrency: int = 1, force_rebuild: bool = True) -> None:
    """Background worker that drives Remotion CLI rendering with live logging."""
    ws = ProjectWorkspace.from_slug(slug)
    ws.root.mkdir(parents=True, exist_ok=True)
    ws.logs_dir.mkdir(parents=True, exist_ok=True)
    ws.checkpoints_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Starting background Remotion render for %s", slug)
    _log_to_pipeline(ws, "INFO", f"Passo #11/16: Disparando worker de renderização Remotion CLI (concorrência: {concurrency})")

    # Update DB status to scripted/processing only if not already RENDERED
    db = SessionLocal()
    try:
        p = db.query(VideoProject).filter(VideoProject.slug == slug).first()
        if p and p.status != VideoStatus.RENDERED:
            p.status = VideoStatus.SCRIPTED
            p.error_message = None
            db.commit()
    finally:
        db.close()

    try:
        # Step 1: Ensure manifest and public assets are completely synced
        manifest = prepare_remotion_assets_and_manifest(slug)

        # Step 2: Remotion CLI Chunked Render (Passo 11/16)
        fps = int(manifest.get("fps", 30))
        duration_sec = float(manifest.get("duration_seconds", 60.0))
        total_frames = max(int(duration_sec * fps), 30)
        chunk_size = 900  # 30 seconds per chunk
        total_chunks = (total_frames + chunk_size - 1) // chunk_size

        chunks_dir = REMOTION_DIR / "projects" / slug / "chunks"
        chunks_dir.mkdir(parents=True, exist_ok=True)
        concat_list_file = chunks_dir / "concat_list.txt"

        # Invalidate chunks cache if manifest changed (e.g. image count changed from 20 to 71) or force_rebuild
        manifest_str = json.dumps(manifest, sort_keys=True)
        manifest_hash = hashlib.md5(manifest_str.encode("utf-8")).hexdigest()
        hash_file = chunks_dir / "manifest_hash.txt"
        should_clean_chunks = force_rebuild
        if not should_clean_chunks:
            if not hash_file.exists() or hash_file.read_text().strip() != manifest_hash:
                should_clean_chunks = True

        images = manifest.get("image_filenames", [])
        if should_clean_chunks:
            _log_to_pipeline(ws, "INFO", f"Passo #11/16: Manifesto ou ativos atualizados ({len(images)} imagens). Limpando chunks legados para renderização completa...")
            for old_chunk in chunks_dir.glob("chunk_*.mp4"):
                try:
                    old_chunk.unlink()
                except Exception:
                    pass
            hash_file.write_text(manifest_hash, encoding="utf-8")

        output_filename = f"{slug}_main.mp4"
        remotion_out = REMOTION_DIR / output_filename

        env = os.environ.copy()
        env["PATH"] = f"/Users/isamumatsuyama/.local/bin:/usr/local/bin:/usr/bin:/bin:{env.get('PATH', '')}"

        # Validação física obrigatória dos assets em render-remotion/public/ antes do render Remotion CLI
        if manifest.get("bgm_filename"):
            clean_bgm = manifest["bgm_filename"].replace("/public/", "").replace("public/", "").strip()
            bgm_check = REMOTION_PUBLIC / clean_bgm
            if not bgm_check.exists() or bgm_check.stat().st_size == 0:
                err_msg = f"Arquivo de BGM obrigatório '{clean_bgm}' não encontrado ou com 0 bytes em {REMOTION_PUBLIC} antes do CLI Remotion."
                _log_to_pipeline(ws, "ERROR", err_msg)
                raise FileNotFoundError(err_msg)
            _log_to_pipeline(ws, "INFO", f"Passo #11/16: BGM verificado em public/ ({clean_bgm}, {bgm_check.stat().st_size} bytes).")

        audio_check = REMOTION_PUBLIC / "audio_en.wav"
        if not audio_check.exists() or audio_check.stat().st_size == 0:
            err_msg = f"Arquivo de narração 'audio_en.wav' não encontrado ou com 0 bytes em {REMOTION_PUBLIC} antes do CLI Remotion."
            _log_to_pipeline(ws, "ERROR", err_msg)
            raise FileNotFoundError(err_msg)

        _log_to_pipeline(ws, "INFO", f"Passo #11/16: Render Chunks Remotion ({total_chunks} chunks de 30s, {total_frames} frames totais)")

        rendered_chunk_files: list[Path] = []

        for i in range(total_chunks):
            with _LOCK:
                if ACTIVE_RENDER_JOBS.get(slug, {}).get("cancelled"):
                    _log_to_pipeline(ws, "WARN", "Renderização cancelada pelo usuário. Interrompendo worker.")
                    return

            start = i * chunk_size
            end = min(start + chunk_size - 1, total_frames - 1)
            chunk_file = chunks_dir / f"chunk_{start:06d}.mp4"

            # Check if chunk already successfully rendered
            if chunk_file.exists() and chunk_file.stat().st_size > 10000:
                _log_to_pipeline(ws, "INFO", f"[Chunk {i+1}/{total_chunks}] frames {start}-{end} já renderizado ({chunk_file.name}). Aproveitando cache.")
                rendered_chunk_files.append(chunk_file)
                continue

            _log_to_pipeline(ws, "INFO", f"[Chunk {i+1}/{total_chunks}] Renderizando frames {start}-{end}...")

            # Render chunk with retry
            max_retries = 3
            success = False
            for attempt in range(1, max_retries + 1):
                with _LOCK:
                    if ACTIVE_RENDER_JOBS.get(slug, {}).get("cancelled"):
                        _log_to_pipeline(ws, "WARN", "Renderização cancelada pelo usuário. Interrompendo worker.")
                        return

                # Switch GL backends on retry to avoid target-closed / GPU crashes
                gl_backend = "angle" if attempt == 1 else "swiftshader" if attempt == 2 else "swangle"
                bundle_target = "build" if (REMOTION_DIR / "build").exists() else "RedSunJapanVideo"
                cmd = [
                    _get_remotion_bin(),
                    "render",
                    bundle_target,
                ]
                if bundle_target == "build":
                    cmd.append("RedSunJapanVideo")
                cmd.extend([
                    str(chunk_file),
                    f"--frames={start}-{end}",
                    f"--concurrency={concurrency}",
                    f"--gl={gl_backend}",
                    "--pixel-format=yuv420p",
                    '--chromium-options=--disable-web-security --disable-dev-shm-usage --no-sandbox',
                    "--timeout=180000",
                ])
                manifest_file = REMOTION_PUBLIC / "manifest.json"
                if manifest_file.exists():
                    cmd.append(f"--props={str(manifest_file.resolve())}")

                proc = subprocess.Popen(
                    cmd,
                    cwd=str(REMOTION_DIR),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    env=env,
                    text=True,
                    bufsize=1,
                    preexec_fn=os.setsid,
                )
                with _LOCK:
                    ACTIVE_RENDER_PROCESSES[slug] = proc

                if proc.stdout:
                    for line in iter(proc.stdout.readline, ""):
                        with _LOCK:
                            if ACTIVE_RENDER_JOBS.get(slug, {}).get("cancelled"):
                                break
                        line_str = line.strip()
                        if line_str and not line_str.startswith("Copying public dir"):
                            if "Rendered" in line_str:
                                # Log progress periodically (every few ticks)
                                if any(x in line_str for x in ("0/900", "300/900", "600/900", "900/900")) or "Rendered" in line_str:
                                    _log_to_pipeline(ws, "INFO", f"[Remotion Chunk {i+1}/{total_chunks}] {line_str}")
                            elif "Error" in line_str or "error" in line_str:
                                _log_to_pipeline(ws, "WARN", f"[Remotion Chunk {i+1}] {line_str}")

                proc.wait()
                with _LOCK:
                    if ACTIVE_RENDER_PROCESSES.get(slug) == proc:
                        ACTIVE_RENDER_PROCESSES.pop(slug, None)

                with _LOCK:
                    if ACTIVE_RENDER_JOBS.get(slug, {}).get("cancelled"):
                        _log_to_pipeline(ws, "WARN", "Renderização cancelada pelo usuário.")
                        return

                if proc.returncode == 0 and chunk_file.exists() and chunk_file.stat().st_size > 1000:
                    _log_to_pipeline(ws, "SUCCESS", f"[Chunk {i+1}/{total_chunks}] Concluído com sucesso ({chunk_file.stat().st_size} bytes).")
                    rendered_chunk_files.append(chunk_file)
                    success = True
                    break
                else:
                    _log_to_pipeline(ws, "WARN", f"[Chunk {i+1}/{total_chunks}] Tentativa {attempt} (GL: {gl_backend}) falhou. Tentando novamente...")
                    time.sleep(2)

            if not success:
                with _LOCK:
                    if ACTIVE_RENDER_JOBS.get(slug, {}).get("cancelled"):
                        _log_to_pipeline(ws, "WARN", "Renderização cancelada pelo usuário.")
                        return
                err_msg = f"Falha ao renderizar chunk {i+1}/{total_chunks} (frames {start}-{end})."
                _log_to_pipeline(ws, "ERROR", err_msg)
                db = SessionLocal()
                try:
                    p = db.query(VideoProject).filter(VideoProject.slug == slug).first()
                    if p:
                        p.status = VideoStatus.FAILED
                        p.error_message = err_msg
                        db.commit()
                finally:
                    db.close()
                with _LOCK:
                    if slug in ACTIVE_RENDER_JOBS:
                        ACTIVE_RENDER_JOBS[slug]["status"] = "failed"
                        ACTIVE_RENDER_JOBS[slug]["error"] = err_msg
                return

        _log_to_pipeline(ws, "SUCCESS", f"Passo #11/16 Concluído: Todos os {total_chunks} chunks foram renderizados.")

        # Step 3: Concatenação Final (Passo 12/16)
        with _LOCK:
            if ACTIVE_RENDER_JOBS.get(slug, {}).get("cancelled"):
                _log_to_pipeline(ws, "WARN", "Renderização cancelada pelo usuário.")
                return

        _log_to_pipeline(ws, "INFO", f"Passo #12/16: Concatenação sem perdas de {len(rendered_chunk_files)} chunks via ffmpeg...")
        with open(concat_list_file, "w", encoding="utf-8") as f:
            for ch in rendered_chunk_files:
                f.write(f"file '{ch.resolve()}'\n")

        concat_cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list_file.resolve()),
            "-c",
            "copy",
            str(remotion_out.resolve()),
        ]

        c_proc = subprocess.Popen(
            concat_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            preexec_fn=os.setsid,
        )
        with _LOCK:
            ACTIVE_RENDER_PROCESSES[slug] = c_proc
        c_stdout, c_stderr = c_proc.communicate()
        with _LOCK:
            if ACTIVE_RENDER_PROCESSES.get(slug) == c_proc:
                ACTIVE_RENDER_PROCESSES.pop(slug, None)

        with _LOCK:
            if ACTIVE_RENDER_JOBS.get(slug, {}).get("cancelled"):
                _log_to_pipeline(ws, "WARN", "Concatenação cancelada pelo usuário.")
                return

        if c_proc.returncode != 0:
            err_msg = f"Falha na concatenação ffmpeg: {c_stderr[:300]}"
            _log_to_pipeline(ws, "ERROR", err_msg)
            raise RuntimeError(err_msg)

        _log_to_pipeline(ws, "SUCCESS", f"Passo #12/16 Concluído: Chunks renderizados unificados ({remotion_out.stat().st_size} bytes).")

        # Step 3b: Armazenamento do Master Bruto (output_master_raw.mp4)
        (ws.root / "render").mkdir(parents=True, exist_ok=True)
        raw_master_dest = ws.root / "render" / "output_master_raw.mp4"
        shutil.copy2(remotion_out, raw_master_dest)
        _log_to_pipeline(
            ws,
            "INFO",
            f"Passo #12b: Master bruto Remotion alocado em render/output_master_raw.mp4 ({raw_master_dest.stat().st_size} bytes)."
        )

        # Step 3c: Master Nativo Remotion (Abertura, Mid-Roll CTA e End-Screen integrados nativamente)
        master_with_bumpers = _stitch_master_with_bumpers(slug, raw_master_dest, ws)
        md5_stitched = _compute_file_md5(master_with_bumpers)

        # Step 4: Materialize master video strictly to video/<slug>_master.mp4 (Regra do Arquivo Único)
        (ws.root / "video").mkdir(parents=True, exist_ok=True)
        final_video_dest = ws.root / "video" / f"{slug}_master.mp4"
        if master_with_bumpers.resolve() != final_video_dest.resolve():
            shutil.copy2(master_with_bumpers, final_video_dest)

        _log_to_pipeline(
            ws,
            "SUCCESS",
            f"Passo #13/16 Concluído: Master de vídeo HD alocado estritamente em video/{slug}_master.mp4 ({final_video_dest.stat().st_size} bytes, MD5: {md5_stitched})"
        )

        # Retrieve project script and titles for steps 14-16
        script_text = ""
        for sf in (ws.root / "text" / "script_en.md", ws.root / "text" / "script.md", ws.script_path):
            if sf.exists():
                try:
                    script_text = sf.read_text(encoding="utf-8")
                    if script_text:
                        break
                except Exception:
                    pass

        subtitles_file = ws.text_dir / "subtitles_en.json"
        if not subtitles_file.exists() and (ws.root / "transcript.json").exists():
            subtitles_file = ws.root / "transcript.json"
        images = manifest.get("image_filenames", [])

        # Step 14: Parallel output - Artigo Blog (Astro)
        _execute_parallel_step_14_blog(slug, ws, manifest.get("title_en", slug), script_text)

        # Step 15: Parallel output - Shorts Verticais (9:16)
        _execute_parallel_step_15_shorts(slug, ws, manifest.get("title_en", slug), script_text, subtitles_file, final_video_dest)

        # Step 16: Parallel output - Post-Kit YouTube
        _execute_parallel_step_16_post_kit(slug, ws, manifest.get("title_en", slug), duration_sec, len(images), subtitles_file)

        # Hygiene sweep: clean up any redundant files
        cleanup_and_unify_project_storage(slug)

        # Update Project Status in DB
        db = SessionLocal()
        try:
            p = db.query(VideoProject).filter(VideoProject.slug == slug).first()
            if p:
                p.status = VideoStatus.RENDERED
                p.error_message = None
                db.commit()
        finally:
            db.close()

        # Write summary & checkpoint
        ws.checkpoints_dir.mkdir(parents=True, exist_ok=True)
        (ws.checkpoints_dir / "phase_render_video.json").write_text(
            json.dumps({"status": "COMPLETED", "video": str(final_video_dest), "timestamp": datetime.now(timezone.utc).isoformat()}, indent=2),
            encoding="utf-8"
        )
        ws.pipeline_summary_path.write_text(
            json.dumps({"stage": "rendered", "slug": slug, "master_path": str(final_video_dest), "completed_at": datetime.now(timezone.utc).isoformat()}, indent=2),
            encoding="utf-8"
        )

        _log_to_pipeline(ws, "SUCCESS", f"Pipeline concluído com sucesso: MASTER PRONTO (16/16).")
        logger.info("Remotion rendering completed successfully for %s", slug)

        # Trigger Hook Stage 4 & 5: Notify macOS and dispatch JingJing for Release Audit and Distribution
        try:
            from services.agent_dispatcher import notify_macos, dispatch_hermes_agent, dispatch_background
            notify_macos(
                title="Red Sun Japan // Render Concluído",
                message=f"Vídeo Master de '{slug}' compilado. Acionando JingJing para auditoria e despacho.",
                sound="Submarine",
            )
            prompt_stage_4_5 = (
                f"JingJing, o pipeline concluiu com sucesso a renderização completa (16/16) do projeto '{slug}'. "
                f"Execute imediatamente o Stage 4 (Auditoria de Integridade do Master sem bumpers externos, "
                f"conferência de legendas contra o script e integridade dos 3 Shorts) "
                f"e o Stage 5 (Git push do blog Astro e upload privado no YouTube). "
                f"Emita o Release Audit Report final ao Isamu conforme o AGENTS.md."
            )
            dispatch_background(dispatch_hermes_agent(prompt_stage_4_5, working_dir=ws.root))
        except Exception as exc:
            logger.warning("Could not dispatch Stage 4/5 agent hook in remotion_runner: %s", exc)


        with _LOCK:
            if slug in ACTIVE_RENDER_JOBS:
                ACTIVE_RENDER_JOBS[slug]["status"] = "complete"
                ACTIVE_RENDER_JOBS[slug]["output_path"] = str(final_video_dest)

    except Exception as exc:
        logger.exception("Unexpected error during Remotion rendering for %s", slug)
        _log_to_pipeline(ws, "ERROR", f"Exceção durante a compilação Remotion: {exc}")
        db = SessionLocal()
        try:
            p = db.query(VideoProject).filter(VideoProject.slug == slug).first()
            if p:
                p.status = VideoStatus.FAILED
                p.error_message = str(exc)
                db.commit()
        finally:
            db.close()
        with _LOCK:
            if slug in ACTIVE_RENDER_JOBS:
                ACTIVE_RENDER_JOBS[slug]["status"] = "failed"
                ACTIVE_RENDER_JOBS[slug]["error"] = str(exc)


def start_remotion_render(slug: str, concurrency: int = 2, force_rebuild: bool = True) -> dict[str, Any]:
    """Prepares the manifest and dispatches the Remotion rendering in a background thread."""
    with _LOCK:
        if slug in ACTIVE_RENDER_JOBS and ACTIVE_RENDER_JOBS[slug].get("status") == "running":
            t_prev = ACTIVE_RENDER_JOBS[slug].get("thread")
            if t_prev and t_prev.is_alive():
                return {
                    "success": True,
                    "status": "running",
                    "message": f"Renderização Remotion já em andamento para '{slug}'.",
                    "slug": slug,
                }

        # Step 10 is executed synchronously to validate assets before background render
        manifest = prepare_remotion_assets_and_manifest(slug)

        # Step 11+ dispatched in background
        t = threading.Thread(
            target=_remotion_render_worker,
            args=(slug, concurrency, force_rebuild),
            daemon=True,
            name=f"RemotionWorker-{slug}",
        )
        t.start()

        ACTIVE_RENDER_JOBS[slug] = {
            "thread": t,
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "manifest": manifest,
            "error": None,
            "cancelled": False,
        }

    return {
        "success": True,
        "status": "compiling",
        "step": 10,
        "slug": slug,
        "message": f"Manifesto Remotion gerado com sucesso ({manifest['image_count']} imagens). Worker Remotion CLI iniciado.",
        "manifest": manifest,
    }


def cancel_remotion_render(slug: str) -> dict[str, Any]:
    """Cancels any running Remotion render for the given project slug.

    1. Sends SIGTERM to process group, falling back to SIGKILL after 3 seconds.
    2. Cleans up unfinished temporary files in render/ and tmp/.
    3. Restores project status to RENDERED if valid master mp4 exists, otherwise FAILED.
    4. Unregisters process and job.
    """
    logger.info("Cancelling Remotion render requested for '%s'...", slug)
    ws = ProjectWorkspace.from_slug(slug)

    # 1. Mark job as cancelled in memory
    proc = None
    with _LOCK:
        if slug in ACTIVE_RENDER_JOBS:
            ACTIVE_RENDER_JOBS[slug]["cancelled"] = True
            ACTIVE_RENDER_JOBS[slug]["status"] = "cancelled"
        proc = ACTIVE_RENDER_PROCESSES.get(slug)

    # 2. Terminate active process group if running
    if proc:
        try:
            if proc.poll() is None:
                pgid = os.getpgid(proc.pid)
                logger.info("Sending SIGTERM to process group %d for slug '%s' (PID %d)", pgid, slug, proc.pid)
                os.killpg(pgid, signal.SIGTERM)

                # Grace period: wait up to 3 seconds for graceful shutdown
                t_start = time.time()
                while time.time() - t_start < 3.0:
                    if proc.poll() is not None:
                        break
                    time.sleep(0.2)

                # Force kill with SIGKILL if still alive
                if proc.poll() is None:
                    logger.warning("Process group %d did not exit after 3s. Sending SIGKILL...", pgid)
                    os.killpg(pgid, signal.SIGKILL)
                    try:
                        proc.wait(timeout=1.0)
                    except Exception:
                        pass
        except ProcessLookupError:
            pass
        except Exception as exc:
            logger.warning("Error during process group termination for '%s': %s", slug, exc)

    # Secondary check: kill any rogue/zombie process matching remotion and slug
    try:
        import psutil
        for p_info in psutil.process_iter(['pid', 'cmdline']):
            try:
                cmdline = " ".join(p_info.info['cmdline'] or [])
                if slug in cmdline and ("remotion" in cmdline or "ffmpeg" in cmdline):
                    logger.info("Killing rogue child/zombie process %d for slug '%s'", p_info.info['pid'], slug)
                    os.kill(p_info.info['pid'], signal.SIGKILL)
            except Exception:
                pass
    except Exception:
        pass

    with _LOCK:
        ACTIVE_RENDER_PROCESSES.pop(slug, None)
        if slug in ACTIVE_RENDER_JOBS:
            ACTIVE_RENDER_JOBS[slug]["status"] = "cancelled"

    # 3. Clean up unfinished temporary files in render/ and tmp/
    try:
        render_dir = ws.root / "render"
        if render_dir.exists():
            for f in render_dir.iterdir():
                if f.is_file():
                    if f.name.startswith("chunk_") or f.name.endswith(".tmp") or f.name in ("output_master_raw.mp4", "concat_list.txt"):
                        try:
                            f.unlink()
                        except Exception:
                            pass

        chunks_dir = REMOTION_DIR / "projects" / slug / "chunks"
        if chunks_dir.exists():
            for f in chunks_dir.glob("chunk_*.mp4"):
                try:
                    f.unlink()
                except Exception:
                    pass

        tmp_dir = ws.root / "tmp"
        if tmp_dir.exists():
            for f in tmp_dir.iterdir():
                if f.is_file():
                    try:
                        f.unlink()
                    except Exception:
                        pass
    except Exception as exc:
        logger.warning("Error cleaning temporary files during cancel for '%s': %s", slug, exc)

    # 4. Update project status in SQLite (video_projects)
    db = SessionLocal()
    restored_status = "failed"
    try:
        p = db.query(VideoProject).filter(VideoProject.slug == slug).first()
        if p:
            canonical_master = ws.root / "video" / f"{slug}_master.mp4"
            if canonical_master.exists() and canonical_master.stat().st_size > 10000:
                p.status = VideoStatus.RENDERED
                p.error_message = None
                restored_status = "rendered"
                _log_to_pipeline(ws, "WARN", "Renderização cancelada pelo usuário. Vídeo master pré-existente preservado e status restaurado para RENDERED.")
            else:
                p.status = VideoStatus.FAILED
                p.error_message = "Renderização cancelada pelo usuário."
                restored_status = "failed"
                _log_to_pipeline(ws, "WARN", "Renderização cancelada pelo usuário. Status definido como FAILED.")
            db.commit()
    finally:
        db.close()

    return {
        "success": True,
        "message": "Renderização cancelada com sucesso.",
        "slug": slug,
        "status": restored_status,
    }

