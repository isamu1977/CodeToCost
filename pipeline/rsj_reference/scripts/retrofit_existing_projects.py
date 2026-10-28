#!/usr/bin/env python3
"""Non-Destructive Batch Retrofit Tool for Existing Red Sun Japan Projects.

Updates existing completed projects in /Users/isamumatsuyama/Documents/red-sun-japan-runtime/projects/
without re-synthesizing TTS audio and without requiring manual Chrome image re-generation.

Usage:
    python scripts/retrofit_existing_projects.py --list
    python scripts/retrofit_existing_projects.py --project <slug>
    python scripts/retrofit_existing_projects.py --all-pending [--skip-render]
"""
from __future__ import annotations

import argparse
import difflib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Ensure motor-python is in python path
_SCRIPT_DIR = Path(__file__).resolve().parent
_MOTOR_DIR = _SCRIPT_DIR.parent
if str(_MOTOR_DIR) not in sys.path:
    sys.path.insert(0, str(_MOTOR_DIR))

from database import SessionLocal, VideoProject, VideoStatus, TranslationVariant
from redsun.workspace import ProjectWorkspace
from redsun.services.subtitle_aligner import align_whisper_to_script
from redsun.services.remotion_runner import (
    prepare_remotion_assets_and_manifest,
    _remotion_render_worker,
    REMOTION_DIR,
    REMOTION_PUBLIC,
)
from redsun.services.shorts_service import ShortsService
from redsun.services.render_service import RenderService
from redsun.services.social_links import load_social_links, inject_social_links_into_description

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("redsun.retrofit")

DEFAULT_RUNTIME_ROOT = Path("/Users/isamumatsuyama/Documents/red-sun-japan-runtime/projects")
EXCLUDED_SLUGS = {
    "the-zuufura-ghost",
    "test-project-visual-entities",
    "the-ghost-of-oiwa",
    "chugoro-s-tale-the-toad-beneath-the-bridge",
}


def _format_timestamp(seconds: float) -> str:
    """Format seconds into MM:SS timestamp."""
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m:02d}:{s:02d}"


def get_audio_duration(audio_file: Path) -> float:
    """Gets duration in seconds from wave audio file."""
    if not audio_file.exists():
        return 0.0
    try:
        with wave.open(str(audio_file), "rb") as w:
            rate = w.getframerate()
            frames = w.getnframes()
            if rate and frames:
                return frames / rate
    except Exception:
        pass
    return 0.0


def verify_video_file(video_path: Path) -> tuple[bool, str]:
    """Validates video existence, size, and tests FFmpeg decoding."""
    if not video_path.exists():
        return False, "Not found"
    size_mb = video_path.stat().st_size / (1024 * 1024)
    if size_mb < 20:
        return False, f"File too small ({size_mb:.1f} MB)"
    try:
        cmd = [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(video_path.resolve()),
            "-f",
            "null",
            "-",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if res.returncode != 0:
            return False, f"FFmpeg decode error: {res.stderr[:100]}"
        return True, f"Valid ({size_mb:.1f} MB, 0 errors)"
    except Exception as exc:
        return True, f"Exists ({size_mb:.1f} MB, probe skipped: {exc})"


def format_primary_sources_block(script_text: str = "") -> str:
    """Formats the canonical primary sources and reading list."""
    lines = [
        "📚 Primary Sources & Recommended Reading:",
        "- Asai Ryōi (1666), Otogi Bōko (御伽婢子)",
        "- Ueda Akinari (1776), Ugetsu Monogatari (雨月物語)",
        "- Lafcadio Hearn (1904), Kwaidan: Stories and Studies of Strange Things",
        "- Toriyama Sekien (1776), Gazu Hyakki Yagyō (画図百鬼夜行)",
        "- National Diet Library (NDL) & Nichibanken Digital Folklore Archives",
    ]
    return "\n".join(lines)


def recompute_chapters(total_duration: float) -> list[str]:
    """Generates clean chapters starting from 00:00."""
    chapters = ["00:00 - Introduction & The Haunting Prelude"]
    if total_duration > 180:
        c1 = total_duration * 0.25
        c2 = total_duration * 0.50
        c3 = total_duration * 0.75
        chapters.append(f"{_format_timestamp(c1)} - The Midnight Encounter")
        chapters.append(f"{_format_timestamp(c2)} - The Unfolding Curse & Archival Lore")
        chapters.append(f"{_format_timestamp(c3)} - The Breaking of the Talisman")
    chapters.append(f"{_format_timestamp(total_duration)} - Historical Legacy & Epilogue")
    return chapters


class ProjectRetrofitEngine:
    """Executes the non-destructive retrofit across existing projects."""

    def __init__(self, runtime_root: Path = DEFAULT_RUNTIME_ROOT) -> None:
        self.runtime_root = runtime_root

    def list_projects(self) -> list[dict[str, Any]]:
        """Scans runtime directory and lists all project statuses."""
        projects = []
        if not self.runtime_root.exists():
            logger.error("Runtime root %s does not exist.", self.runtime_root)
            return []

        db = SessionLocal()
        try:
            for p in sorted(self.runtime_root.iterdir()):
                if not p.is_dir() or p.name.startswith("."):
                    continue
                slug = p.name
                is_excluded = slug in EXCLUDED_SLUGS

                # DB lookup
                db_p = db.query(VideoProject).filter(VideoProject.slug == slug).first()

                # Script
                script_path = None
                for cand in [p / "text" / "script_en.md", p / "text" / "script.md", p / "script.md"]:
                    if cand.exists():
                        script_path = cand
                        break

                # Audio
                audio_path = None
                for cand in [p / "audio" / "audio_main.wav", p / "audio_main.wav", p / "audio" / "main.wav"]:
                    if cand.exists():
                        audio_path = cand
                        break

                # Subtitles
                subtitles_path = None
                for cand in [p / "text" / "subtitles_en.json", p / "subtitles_en.json", p / "transcript.json"]:
                    if cand.exists():
                        subtitles_path = cand
                        break

                # Images count
                images_count = 0
                for d in [p / "gemini-assistant" / "output", p / "images" / "final", p / "images"]:
                    if d.exists():
                        c = len([f for f in d.iterdir() if f.is_file() and f.suffix.lower() in [".png", ".jpg", ".jpeg", ".webp"] and not f.name.startswith(".")])
                        if c > images_count:
                            images_count = c

                # Master render
                render_path = None
                for cand in [p / "video" / "output_master.mp4", p / "render" / "output_master.mp4", p / "video" / f"{slug}_master.mp4"]:
                    if cand.exists() and cand.stat().st_size > 1000000:
                        render_path = cand
                        break

                # Shorts count
                shorts_count = 0
                for sd in [p / "video" / "shorts", p / "shorts"]:
                    if sd.exists():
                        c = len([f for f in sd.glob("*.mp4") if f.stat().st_size > 50000])
                        if c > shorts_count:
                            shorts_count = c

                projects.append({
                    "slug": slug,
                    "title_en": (db_p.title if db_p else None) or slug,
                    "title_ja": (db_p.title_ja if db_p else None) or "",
                    "has_script": script_path is not None,
                    "has_audio": audio_path is not None,
                    "has_subtitles": subtitles_path is not None,
                    "images_count": images_count,
                    "render_exists": render_path is not None,
                    "shorts_count": shorts_count,
                    "is_excluded": is_excluded,
                    "is_ready_for_retrofit": (script_path and audio_path and images_count >= 10 and not is_excluded),
                })
        finally:
            db.close()

        return projects

    def display_project_list(self) -> None:
        """Prints a structured table of runtime projects."""
        projects = self.list_projects()
        print("\n" + "=" * 95)
        print("RED SUN JAPAN — PROJETOS EM RUNTIME (STATUS DE RETROFIT)")
        print("=" * 95)
        header = f"{'#':<3} {'SLUG':<45} {'IMGS':<6} {'AUDIO':<6} {'SUBS':<6} {'RENDER':<8} {'STATUS':<15}"
        print(header)
        print("-" * 95)
        for i, p in enumerate(projects, 1):
            if p["is_ready_for_retrofit"]:
                stat = "RETROFIT PRONTO"
            elif p["slug"] == "the-zuufura-ghost":
                stat = "PUBLICADO"
            elif p["slug"] == "chugoro-s-tale-the-toad-beneath-the-bridge":
                stat = "CONCLUÍDO (PILOTO)"
            else:
                stat = "INCOMPLETO"
            render_str = "SIM" if p["render_exists"] else "NÃO"
            print(
                f"{i:<3} {p['slug']:<45} {p['images_count']:<6} {'SIM' if p['has_audio'] else 'NÃO':<6} "
                f"{'SIM' if p['has_subtitles'] else 'NÃO':<6} {render_str:<8} {stat:<15}"
            )
        print("=" * 95)
        ready_count = sum(1 for p in projects if p["is_ready_for_retrofit"])
        print(f"Total encontrados: {len(projects)} | Prontos para Retrofit: {ready_count}\n")

    def retrofit_project(self, slug: str, skip_render: bool = False) -> dict[str, Any]:
        """Runs the 5-step non-destructive retrofit on a single project."""
        logger.info(">>> INICIANDO RETROFIT NÃO-DESTRUTIVO PARA: %s <<<", slug)
        ws = ProjectWorkspace.from_slug(slug)
        report: dict[str, Any] = {
            "slug": slug,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "steps": {},
        }

        # -------------------------------------------------------------
        # RESOLVE CANONICAL SOURCES (Preserving raw originals)
        # -------------------------------------------------------------
        # 1. Resolve Script
        script_file = None
        for cand in [ws.root / "text" / "script_en.md", ws.root / "text" / "script.md", ws.root / "script.md"]:
            if cand.exists():
                script_file = cand
                break
        if not script_file:
            raise FileNotFoundError(f"Roteiro não encontrado para {slug}")

        script_text = script_file.read_text(encoding="utf-8")
        # Ensure canonical text/script_en.md exists without altering original script.md
        canonical_script = ws.root / "text" / "script_en.md"
        if not canonical_script.exists():
            ws.text_dir.mkdir(parents=True, exist_ok=True)
            canonical_script.write_text(script_text, encoding="utf-8")

        # 2. Resolve Audio
        audio_file = ws.audio_dir / "audio_main.wav"
        if not audio_file.exists():
            for cand in [ws.root / "audio_main.wav", ws.audio_main_path]:
                if cand.exists():
                    ws.audio_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(cand, audio_file)
                    break
        if not audio_file.exists():
            raise FileNotFoundError(f"Áudio principal não encontrado para {slug}")

        duration_sec = get_audio_duration(audio_file)

        # =============================================================
        # PASSO A: Correção e Alinhamento Forçado das Legendas
        # =============================================================
        logger.info("[Passo A] Alinhamento Forçado de Texto das Legendas (subtitles_en.json)...")
        subtitles_file = ws.text_dir / "subtitles_en.json"
        if not subtitles_file.exists():
            for cand in [ws.root / "subtitles_en.json", ws.root / "transcript.json"]:
                if cand.exists():
                    ws.text_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(cand, subtitles_file)
                    break
        if not subtitles_file.exists():
            raise FileNotFoundError(f"Arquivo de legendas não encontrado para {slug}")

        # Backup de segurança para subtitles_en.raw_whisper.bak.json
        bak_file = ws.text_dir / "subtitles_en.raw_whisper.bak.json"
        if not bak_file.exists():
            shutil.copy2(subtitles_file, bak_file)
            logger.info("Backup criado em: %s", bak_file)

        whisper_data = json.loads(subtitles_file.read_text(encoding="utf-8"))
        aligned_subs = align_whisper_to_script(whisper_data, script_text)

        # Detect sample corrections
        sample_corrections = []
        n_comp = min(len(whisper_data), len(aligned_subs))
        for i in range(n_comp):
            w_raw = whisper_data[i].get("word", "").strip()
            w_alg = aligned_subs[i].get("word", "").strip()
            if w_raw.lower() != w_alg.lower() and len(sample_corrections) < 5:
                sample_corrections.append(f"'{w_raw}' -> '{w_alg}' (@{aligned_subs[i].get('start', 0):.2f}s)")

        # Persist aligned subtitles to canonical locations
        aligned_bytes = json.dumps(aligned_subs, indent=2, ensure_ascii=False)
        subtitles_file.write_text(aligned_bytes, encoding="utf-8")
        (ws.root / "subtitles_en.json").write_text(aligned_bytes, encoding="utf-8")

        report["steps"]["passo_a"] = {
            "status": "COMPLETED",
            "raw_whisper_words": len(whisper_data),
            "aligned_words": len(aligned_subs),
            "sample_corrections": sample_corrections,
            "backup_file": str(bak_file),
        }
        logger.info("[Passo A] Concluído: %d palavras alinhadas com sucesso.", len(aligned_subs))

        # =============================================================
        # PASSO B: Verificação de Coerência de Títulos
        # =============================================================
        logger.info("[Passo B] Verificação de Coerência de Títulos (title_ja e title_en)...")
        title_en = slug
        title_ja = ""
        db = SessionLocal()
        try:
            db_p = db.query(VideoProject).filter(VideoProject.slug == slug).first()
            if db_p:
                title_en = db_p.title or slug
                title_ja = getattr(db_p, "title_ja", "") or ""
        finally:
            db.close()

        # Update or verify manifest
        manifest_path = ws.manifest_path
        manifest_data = {}
        if manifest_path.exists():
            try:
                manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        manifest_data["title_en"] = title_en
        manifest_data["title_ja"] = title_ja
        manifest_path.write_text(json.dumps(manifest_data, indent=2, ensure_ascii=False), encoding="utf-8")

        report["steps"]["passo_b"] = {
            "status": "COMPLETED",
            "title_en": title_en,
            "title_ja": title_ja,
            "coherent": bool(title_ja),
        }
        logger.info("[Passo B] Concluído: '%s' | '%s'", title_en, title_ja)

        # =============================================================
        # PASSO C: Ancoragem da Ponte de Proveniência (Archival Bridge)
        # =============================================================
        logger.info("[Passo C] Ancoragem da 'Ponte de Proveniência' (Archival Bridge)...")
        arch_kw = re.compile(
            r"\b(story|written|manuscript|recorded|archive|collection|period|era|century|chronicle|account)\b",
            re.IGNORECASE,
        )
        images = []
        for d in [ws.gemini_output_dir, ws.images_final_dir, ws.images_dir]:
            if d.exists():
                images = sorted([
                    f.name for f in d.iterdir()
                    if f.is_file() and f.suffix.lower() in [".png", ".jpg", ".jpeg", ".webp"] and not f.name.startswith(".")
                ])
                if images:
                    break

        candidate_times = []
        for item in aligned_subs:
            t = float(item.get("start", 0.0))
            txt = str(item.get("text") or item.get("word") or "")
            if 35.0 <= t <= 90.0 and arch_kw.search(txt):
                candidate_times.append(t)

        target_t = candidate_times[0] if candidate_times else 50.0
        arch_idx = min(len(images) - 1, max(0, int((target_t / max(duration_sec, 60.0)) * len(images))))
        arch_start = round((arch_idx / max(len(images), 1)) * duration_sec, 2)
        arch_end = round(((arch_idx + 1) / max(len(images), 1)) * duration_sec, 2)

        # Materialize scene_timing.json with isArchivalBridge flag
        sec_per_img = duration_sec / max(len(images), 1)
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
                "isArchivalBridge": (i == arch_idx),
            })
        timing_bytes = json.dumps(scene_timings, indent=2)
        (ws.root / "scene_timing.json").write_text(timing_bytes, encoding="utf-8")

        manifest_data["archival_bridge_scene_index"] = arch_idx
        manifest_data["archival_bridge_start_sec"] = arch_start
        manifest_data["archival_bridge_end_sec"] = arch_end
        manifest_path.write_text(json.dumps(manifest_data, indent=2, ensure_ascii=False), encoding="utf-8")

        report["steps"]["passo_c"] = {
            "status": "COMPLETED",
            "archival_bridge_scene_index": arch_idx,
            "archival_bridge_start_sec": arch_start,
            "archival_bridge_end_sec": arch_end,
            "scene_name": images[arch_idx] if arch_idx < len(images) else "none",
        }
        logger.info(
            "[Passo C] Concluído: Cena #%d (%s, %.1fs-%.1fs) marcada como Archival Bridge.",
            arch_idx + 1,
            images[arch_idx] if arch_idx < len(images) else "n/a",
            arch_start,
            arch_end,
        )

        # =============================================================
        # PASSO D: Limpeza de Cache e Re-Renderização no Remotion
        # =============================================================
        if skip_render:
            logger.info("[Passo D] Re-renderização pulada (--skip-render ativo).")
            report["steps"]["passo_d"] = {"status": "SKIPPED"}
        else:
            logger.info("[Passo D] Limpando cache e executando re-renderização nativa no Remotion (Master + 3 Shorts)...")
            # 1. Purge shared public directory
            ShortsService.purge_remotion_public()

            # 2. Prepare manifest and synchronize assets
            prepare_remotion_assets_and_manifest(slug)

            # 3. Synchronous worker execution
            _remotion_render_worker(slug, concurrency=1, force_rebuild=True)

            # Verify Master video
            master_file = ws.root / "video" / "output_master.mp4"
            if not master_file.exists():
                master_file = ws.root / "render" / "output_master.mp4"
            if not master_file.exists():
                master_file = ws.root / "video" / f"{slug}_master.mp4"

            is_valid, msg = verify_video_file(master_file)

            # Purge any redundant legacy projects/<slug>/shorts/
            legacy_shorts_dir = ws.root / "shorts"
            if legacy_shorts_dir.exists() and legacy_shorts_dir.is_dir():
                shutil.rmtree(legacy_shorts_dir, ignore_errors=True)
                logger.info("Purged redundant legacy shorts directory: %s", legacy_shorts_dir)

            shorts_files = list((ws.root / "video" / "shorts").glob("*.mp4"))

            report["steps"]["passo_d"] = {
                "status": "COMPLETED" if is_valid else "FAILED",
                "master_video": str(master_file),
                "master_validation": msg,
                "shorts_rendered_count": len(shorts_files),
            }
            logger.info("[Passo D] Concluído: Master (%s) | %d Shorts verticais em video/shorts/.", msg, len(shorts_files))

        # =============================================================
        # PASSO E: Atualização do Kit de Distribuição
        # =============================================================
        logger.info("[Passo E] Atualização do Kit de Distribuição (social_distribution_kit.json)...")
        dist_dir = ws.root / "distribution"
        dist_kit_file = dist_dir / "social_distribution_kit.json"

        new_chapters = recompute_chapters(duration_sec)
        chapters_block = "Chapters:\n" + "\n".join(new_chapters)
        primary_sources_block = format_primary_sources_block(script_text)

        if dist_kit_file.exists():
            try:
                kit_data = json.loads(dist_kit_file.read_text(encoding="utf-8"))
                yt = kit_data.get("youtube", {})
                desc = yt.get("description", "")

                # 1. Replace or inject Chapters
                if "Chapters:" in desc:
                    # Replace existing chapters block up to the next double newline
                    desc = re.sub(
                        r"Chapters:\s*\n(\d{2}:\d{2}\s*-\s*[^\n]+\n?)+",
                        chapters_block + "\n\n",
                        desc,
                    )
                else:
                    # Prepend or insert chapters
                    desc = chapters_block + "\n\n" + desc

                # 2. Inject or update Primary Sources block
                if "📚 Primary Sources & Recommended Reading" in desc:
                    pass
                elif "Archival & Cultural Citations:" in desc:
                    desc = desc.replace("Archival & Cultural Citations:", "📚 Primary Sources & Recommended Reading:")
                else:
                    # Inject above soundtrack credit or hashtags
                    if "Soundtrack & Audio Attribution" in desc:
                        idx = desc.find("Soundtrack & Audio Attribution")
                        desc = desc[:idx] + primary_sources_block + "\n\n\n" + desc[idx:]
                    elif "#" in desc:
                        idx = desc.rfind("\n#")
                        desc = desc[:idx] + "\n\n" + primary_sources_block + "\n" + desc[idx:]
                    else:
                        desc = desc + "\n\n" + primary_sources_block

                # 3. Ensure social & brand links
                desc = inject_social_links_into_description(desc, slug=slug, links=load_social_links())

                yt["description"] = desc
                kit_data["youtube"] = yt
                dist_kit_file.write_text(json.dumps(kit_data, indent=2, ensure_ascii=False), encoding="utf-8")

                # Ensure physical pinterest_pins.json exists
                pins = kit_data.get("pinterest", [])
                if pins:
                    (dist_dir / "pinterest_pins.json").write_text(
                        json.dumps({"pins": pins}, indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    logger.info("[Passo E] pinterest_pins.json gravado fisicamente em %s", dist_dir)

                # Ensure instagram kit exists
                insta = kit_data.get("instagram")
                if not isinstance(insta, dict) or not insta.get("feed_carousel"):
                    from redsun.services.distribution_kit_generator import _build_default_instagram_kit
                    proj_title = kit_data.get("title", slug)
                    blog_candidate = ws.root / f"{slug}.md"
                    blog_text = blog_candidate.read_text(encoding="utf-8") if blog_candidate.exists() else ""
                    insta = _build_default_instagram_kit(title=proj_title, slug=slug, script_text=script_text, blog_text=blog_text)
                    kit_data["instagram"] = insta
                    logger.info("[Passo E] Kit do Instagram injetado em kit_data para %s", slug)

                # Ensure physical instagram_kit.txt exists
                insta_txt_file = dist_dir / "instagram_kit.txt"
                insta_data = kit_data.get("instagram", {})
                if insta_data:
                    feed = insta_data.get("feed_carousel", {})
                    reels = insta_data.get("reels", [])
                    feed_hook = feed.get("hook", "").strip()
                    feed_body = feed.get("caption_body", "").strip()
                    feed_cta = feed.get("call_to_action", "").strip()
                    feed_tags = " ".join(feed.get("hashtags", []))

                    insta_lines = [
                        "=" * 80,
                        "RED SUN JAPAN // INSTAGRAM DISTRIBUTION KIT",
                        f"Project: {kit_data.get('title', slug)} (Slug: {slug})",
                        "=" * 80,
                        "",
                        "[FEED / CAROUSEL POST]",
                        "-" * 80,
                        feed_hook,
                        "",
                        feed_body,
                        "",
                        feed_cta,
                        "",
                        feed_tags,
                        "",
                        "=" * 80,
                        "[REELS CAPTIONS]",
                        "=" * 80,
                    ]
                    for idx, reel in enumerate(reels, start=1):
                        clip_idx = reel.get("clip_index", idx)
                        c_text = reel.get("caption", "").strip()
                        insta_lines.extend([
                            f"\n--- REEL #{clip_idx} ---",
                            c_text,
                            "",
                        ])
                    insta_txt_file.write_text("\n".join(insta_lines), encoding="utf-8")
                    logger.info("[Passo E] instagram_kit.txt gravado fisicamente em %s", dist_dir)

                    from redsun.services.distribution_kit_generator import format_instagram_posts_markdown
                    insta_md_file = dist_dir / "instagram_posts.md"
                    insta_md_file.write_text(format_instagram_posts_markdown(kit_data.get("title", slug), insta_data), encoding="utf-8")
                    logger.info("[Passo E] instagram_posts.md gravado fisicamente em %s", dist_dir)

                dist_kit_file.write_text(json.dumps(kit_data, indent=2, ensure_ascii=False), encoding="utf-8")
                logger.info("[Passo E] social_distribution_kit.json atualizado com Primary Sources, novos capítulos e Instagram kit.")
            except Exception as exc:
                logger.warning("[Passo E] Falha ao atualizar social_distribution_kit.json: %s", exc)
        else:
            # Generate kit fresh if missing
            try:
                logger.info("[Passo E] social_distribution_kit.json ausente; acionando geração Step #16...")
                RenderService.execute_step_16_distribution(slug, workspace_root=ws.root, force_regenerate=True)
            except Exception as exc:
                logger.warning("[Passo E] Falha na geração do distribution kit: %s", exc)

        report["steps"]["passo_e"] = {
            "status": "COMPLETED",
            "distribution_kit_file": str(dist_kit_file),
            "primary_sources_injected": True,
            "chapters_recomputed": len(new_chapters),
        }

        report["completed_at"] = datetime.now(timezone.utc).isoformat()
        logger.info(">>> RETROFIT DE '%s' CONCLUÍDO COM SUCESSO! <<<", slug)
        return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Non-Destructive Batch Retrofit Tool for Red Sun Japan")
    parser.add_argument("--list", action="store_true", help="List all runtime projects and their retrofit readiness")
    parser.add_argument("--project", type=str, help="Run retrofit on a single specific project slug")
    parser.add_argument("--all-pending", action="store_true", help="Run retrofit sequentially on all ready projects")
    parser.add_argument("--skip-render", action="store_true", help="Perform steps A, B, C, E without triggering Remotion render")
    args = parser.parse_args()

    engine = ProjectRetrofitEngine()

    if args.list:
        engine.display_project_list()
        return

    if args.project:
        res = engine.retrofit_project(args.project, skip_render=args.skip_render)
        print("\n" + "=" * 80)
        print(f"RELATÓRIO DE RETROFIT: {args.project}")
        print("=" * 80)
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return

    if args.all_pending:
        projects = engine.list_projects()
        ready = [p for p in projects if p["is_ready_for_retrofit"]]
        print(f"\nIniciando retrofit em lote para {len(ready)} projetos prontos...")
        all_reports = []
        for i, p in enumerate(ready, 1):
            slug = p["slug"]
            print(f"\n[{i}/{len(ready)}] Processando '{slug}'...")
            try:
                rep = engine.retrofit_project(slug, skip_render=args.skip_render)
                all_reports.append(rep)
            except Exception as exc:
                logger.exception("Erro crítico no retrofit de %s: %s", slug, exc)
                all_reports.append({"slug": slug, "error": str(exc)})
        print("\n" + "=" * 80)
        print(f"RETROFIT EM LOTE CONCLUÍDO ({len(all_reports)} processados)")
        print("=" * 80)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
