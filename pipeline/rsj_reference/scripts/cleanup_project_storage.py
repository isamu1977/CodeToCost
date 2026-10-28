#!/usr/bin/env python3
"""Red Sun Japan — Storage Hygiene & Deduplication Tool.

Enforces:
1. Single Video Rule: Only projects/{slug}/video/{slug}_master.mp4 is kept.
   Deletes redundant output/ directory, video/*_final.mp4, and render/ chunks.
2. Distribution Kit Consolidation: Eliminates post_kit/ and ensures all 4 pure .txt
   files (youtube_kit.txt, twitter_thread.txt, instagram_posts.txt, pinterest_pins.txt)
   and markdown deliverables reside exclusively in distribution/.
3. Root Hygiene: Removes duplicate audio_main.wav, subtitles_en.json, and manifest.json
   from the project root, keeping them strictly in audio/ and text/.
4. Reports total disk space freed in MB and GB.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
from pathlib import Path

# Add parent directory to sys.path so redsun packages can be imported
MOTOR_PYTHON_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MOTOR_PYTHON_DIR))

from redsun.services.distribution_kit_generator import (
    _build_default_instagram_kit,
    format_instagram_posts_markdown,
    sanitize_distribution_hashtags,
    _enrich_shorts_kit,
    format_youtube_shorts_text,
)

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("redsun.cleanup")

DEFAULT_RUNTIME_ROOT = Path("/Users/isamumatsuyama/Documents/red-sun-japan-runtime/projects")


def get_dir_size(path: Path) -> int:
    """Recursively calculate total bytes in a directory."""
    if not path.exists():
        return 0
    total = 0
    try:
        for entry in path.rglob("*"):
            if entry.is_file() and not entry.is_symlink():
                try:
                    total += entry.stat().st_size
                except (OSError, FileNotFoundError):
                    pass
    except Exception:
        pass
    return total


class StorageCleanupEngine:
    def __init__(self, runtime_root: Path | None = None) -> None:
        self.runtime_root = runtime_root or DEFAULT_RUNTIME_ROOT

    def ensure_distribution_text_files(self, project_dir: Path, slug: str) -> None:
        """Ensure the 4 required .txt files and instagram_posts.md exist in distribution/."""
        dist_dir = project_dir / "distribution"
        dist_dir.mkdir(parents=True, exist_ok=True)
        dist_kit_file = dist_dir / "social_distribution_kit.json"

        kit_data: dict = {}
        if dist_kit_file.exists():
            try:
                kit_data = json.loads(dist_kit_file.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("Could not parse %s: %s", dist_kit_file, exc)

        title = kit_data.get("title", slug)
        script_file = project_dir / "text" / "script_en.md"
        script_text = script_file.read_text(encoding="utf-8") if script_file.exists() else ""
        blog_file = project_dir / f"{slug}.md"
        blog_text = blog_file.read_text(encoding="utf-8") if blog_file.exists() else ""

        # 1. Instagram Kit
        insta = kit_data.get("instagram")
        if not isinstance(insta, dict) or not insta.get("feed_carousel"):
            insta = _build_default_instagram_kit(title=title, slug=slug, script_text=script_text, blog_text=blog_text)
            temp_kit = {"instagram": insta, "shorts": [], "youtube": {}}
            temp_kit = sanitize_distribution_hashtags(temp_kit, title=title, script_text=script_text, slug=slug)
            insta = temp_kit["instagram"]
            kit_data["instagram"] = insta
            dist_kit_file.write_text(json.dumps(kit_data, indent=2, ensure_ascii=False), encoding="utf-8")

        # instagram_posts.md
        insta_md_file = dist_dir / "instagram_posts.md"
        if not insta_md_file.exists():
            insta_md_file.write_text(format_instagram_posts_markdown(title, insta), encoding="utf-8")

        # instagram_posts.txt
        feed = insta.get("feed_carousel", {})
        reels = insta.get("reels", [])
        feed_hook = feed.get("hook", "").strip()
        feed_body = feed.get("caption_body", "").strip()
        feed_cta = feed.get("call_to_action", "").strip()
        feed_tags = " ".join(feed.get("hashtags", []))

        insta_txt_lines = [
            "=" * 80,
            "RED SUN JAPAN // INSTAGRAM DISTRIBUTION KIT",
            f"Project: {title} (Slug: {slug})",
            "=" * 80,
            "",
            "[FEED / CAROUSEL POST] (Attach images from distribution/pins_media/)",
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
            insta_txt_lines.extend([
                f"\n--- REEL #{clip_idx:02d} (video/shorts/short_{clip_idx:02d}.mp4) ---",
                c_text,
                "",
            ])
        (dist_dir / "instagram_posts.txt").write_text("\n".join(insta_txt_lines), encoding="utf-8")
        (dist_dir / "instagram_kit.txt").write_text("\n".join(insta_txt_lines), encoding="utf-8")

        # 2. Twitter Thread text & md
        tweets = kit_data.get("twitter_thread", [])
        total_tweets = len(tweets)
        thread_txt_lines = [
            "=" * 80,
            "RED SUN JAPAN // X / TWITTER THREAD",
            f"Project: {title} (Slug: {slug})",
            "=" * 80,
            "",
        ]
        thread_md_lines = [f"# X/Twitter Thread: {title}\n"]
        for i, t in enumerate(tweets, start=1):
            text = t.get("text", "").strip()
            num = t.get("tweet_number", i)
            thread_md_lines.append(f"---\n🧵 ({num}/{total_tweets})\n{text}\n")
            thread_txt_lines.extend([
                f"--- Tweet {num}/{total_tweets} ---",
                text,
                "",
            ])
        (dist_dir / "twitter_thread.txt").write_text("\n".join(thread_txt_lines), encoding="utf-8")
        if not (dist_dir / "twitter_thread.md").exists():
            (dist_dir / "twitter_thread.md").write_text("\n".join(thread_md_lines), encoding="utf-8")

        # 3. Pinterest Pins text & json
        pinterest_items = kit_data.get("pinterest", [])
        pin_txt_lines = [
            "=" * 80,
            "RED SUN JAPAN // PINTEREST DISTRIBUTION KIT",
            f"Project: {title} (Slug: {slug})",
            "=" * 80,
            "",
        ]
        blog_post_url = f"https://redsunjapan.com/posts/{slug}"
        for idx, pin in enumerate(pinterest_items, start=1):
            p_title = pin.get("pin_title", "")
            p_desc = pin.get("pin_description", "")
            p_link = pin.get("destination_link", blog_post_url)
            p_media = pin.get("media_file", f"pin_{idx:02d}.jpeg")
            pin_txt_lines.extend([
                f"--- PIN #{idx:02d} (Media: pins_media/{p_media}) ---",
                f"Title: {p_title}",
                f"Description: {p_desc}",
                f"Destination Link: {p_link}",
                "",
            ])
        (dist_dir / "pinterest_pins.txt").write_text("\n".join(pin_txt_lines), encoding="utf-8")

        # 4. YouTube Kit text
        yt = kit_data.get("youtube", {})
        title_opts = yt.get("title_options", [])
        tags = yt.get("tags", [])
        desc = yt.get("description", "")
        thumb_meta = kit_data.get("thumbnail", {})
        timestamps = "\n".join(kit_data.get("timestamps", []))
        pinned_comment = (
            f"⛩️ Which moment of the {title} chronicle chilled you the most? "
            f"Read the archival essay & historical footnotes: {blog_post_url}\n\n"
            f"Subscribe to Red Sun Japan for weekly archival folklore & kaidan documentaries."
        )

        yt_lines = [
            "=" * 80,
            f"RED SUN JAPAN // YOUTUBE DISTRIBUTION KIT",
            f"Project: {title} (Slug: {slug})",
            "=" * 80,
            "",
            "[TITLE OPTIONS]",
        ]
        for idx, topt in enumerate(title_opts, start=1):
            yt_lines.append(f"{idx}. {topt}")
        yt_lines.extend([
            "",
            "-" * 80,
            "[OFFICIAL THUMBNAIL]",
            f"File: distribution/thumbnail.png",
            f"Kicker: {thumb_meta.get('kicker', 'N/A')}",
            f"Headline Line 1: {thumb_meta.get('headline_line1', 'N/A')}",
            f"Headline Highlight: {thumb_meta.get('headline_highlight', 'N/A')}",
            "",
            "-" * 80,
            "[DESCRIPTION]",
            desc,
            "",
            "-" * 80,
            "[CHAPTERS / TIMESTAMPS]",
            timestamps,
            "",
            "-" * 80,
            "[SUGGESTED PINNED COMMENT]",
            pinned_comment,
            "",
            "-" * 80,
            "[SOUNDTRACK / BGM CREDITS]",
            kit_data.get("bgm_credit") or "N/A",
            "",
            "-" * 80,
            "[TAGS]",
            ", ".join(tags),
            "",
            "=" * 80,
            "[YOUTUBE SHORTS (3 CLIPS)]",
            "=" * 80,
        ])

        # 5. YouTube Shorts Kit
        shorts_meta_candidates = [
            project_dir / "video" / "shorts" / "shorts_metadata.json",
            project_dir / "shorts" / "shorts_metadata.json",
        ]
        shorts_meta_file = next((p for p in shorts_meta_candidates if p.exists()), None)
        shorts_clips_meta: list[dict] = []
        if shorts_meta_file:
            try:
                shorts_clips_meta = json.loads(shorts_meta_file.read_text(encoding="utf-8")).get("clips", [])
            except Exception:
                pass

        raw_shorts = kit_data.get("shorts", [])
        enriched_shorts = _enrich_shorts_kit(
            raw_shorts=raw_shorts,
            title=title,
            slug=slug,
            script_text=script_text,
            blog_text=blog_text,
            shorts_clips_meta=shorts_clips_meta,
        )

        for s in enriched_shorts:
            idx = s.get("clip_index", 1)
            yt_lines.extend([
                f"\n--- YOUTUBE SHORT #{idx:02d} (video/shorts/short_{idx:02d}.mp4) ---",
                f"Title: {s.get('title', '')}",
                "",
                "Description:",
                s.get("description", ""),
                "",
                "Hashtags:",
                " ".join(s.get("hashtags", [])),
                "",
            ])
        (dist_dir / "youtube_kit.txt").write_text("\n".join(yt_lines), encoding="utf-8")

        # Save dedicated youtube_shorts.txt
        shorts_txt_content = format_youtube_shorts_text(title, slug, enriched_shorts)
        (dist_dir / "youtube_shorts.txt").write_text(shorts_txt_content, encoding="utf-8")

        # Persist enriched shorts back to social_distribution_kit.json if updated
        if dist_kit_file.exists() and kit_data:
            kit_data["shorts"] = enriched_shorts
            dist_kit_file.write_text(json.dumps(kit_data, indent=2, ensure_ascii=False), encoding="utf-8")

    def cleanup_project(self, project_dir: Path) -> dict[str, int]:
        """Cleans up a single project directory according to strict architectural rules."""
        slug = project_dir.name
        before_bytes = get_dir_size(project_dir)
        files_removed = 0

        # ── 1. Unificação Estrita de Vídeo (Regra do Arquivo Único) ──
        video_dir = project_dir / "video"
        video_dir.mkdir(parents=True, exist_ok=True)
        canonical_master = video_dir / f"{slug}_master.mp4"

        # If canonical master does not exist, find a valid source to promote
        if not canonical_master.exists() or canonical_master.stat().st_size < 1000:
            candidates = [
                video_dir / f"{slug}_final.mp4",
                video_dir / "output_master.mp4",
                project_dir / "output" / f"{slug}_final.mp4",
                project_dir / "output" / "output_master_with_bumper.mp4",
                project_dir / "render" / f"{slug}_master_with_bumpers.mp4",
                project_dir / "render" / "output_master.mp4",
                project_dir / "render" / "output_master_raw.mp4",
            ]
            for cand in candidates:
                if cand.exists() and cand.is_file() and cand.stat().st_size > 1000000:
                    shutil.copy2(cand, canonical_master)
                    logger.info("[%s] Promovido vídeo para canonical master: %s", slug, canonical_master.name)
                    break

        # Remove redundant long videos in video/
        for extra in [video_dir / f"{slug}_final.mp4", video_dir / "output_master.mp4"]:
            if extra.exists():
                try:
                    extra.unlink()
                    files_removed += 1
                except Exception:
                    pass

        # Purge output/ folder completely
        output_dir = project_dir / "output"
        if output_dir.exists():
            try:
                shutil.rmtree(output_dir, ignore_errors=True)
                logger.info("[%s] Pasta output/ excluída com sucesso.", slug)
            except Exception as exc:
                logger.warning("[%s] Falha ao excluir output/: %s", slug, exc)

        # Purge temporary files in render/
        render_dir = project_dir / "render"
        if render_dir.exists():
            for rf in list(render_dir.iterdir()):
                if rf.is_file():
                    try:
                        rf.unlink()
                        files_removed += 1
                    except Exception:
                        pass
            try:
                shutil.rmtree(render_dir, ignore_errors=True)
                logger.info("[%s] Pasta temporária render/ limpa e removida.", slug)
            except Exception:
                pass

        # Purge temporary tmp/ folder
        tmp_dir = project_dir / "tmp"
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)

        # Purge legacy shorts folder if present
        legacy_shorts = project_dir / "shorts"
        if legacy_shorts.exists() and legacy_shorts.is_dir():
            dest_shorts = video_dir / "shorts"
            dest_shorts.mkdir(parents=True, exist_ok=True)
            for item in legacy_shorts.iterdir():
                if item.is_file() and not (dest_shorts / item.name).exists():
                    shutil.copy2(item, dest_shorts / item.name)
            shutil.rmtree(legacy_shorts, ignore_errors=True)
            logger.info("[%s] Pasta legacy shorts/ consolidada em video/shorts/ e excluída.", slug)

        # ── 2. Consolidação de distribution/ e Eliminação de post_kit/ ──
        self.ensure_distribution_text_files(project_dir, slug)

        post_kit_dir = project_dir / "post_kit"
        dist_dir = project_dir / "distribution"
        if post_kit_dir.exists():
            # Migrate any missing assets from post_kit to distribution
            for p_item in post_kit_dir.rglob("*"):
                if p_item.is_file():
                    rel = p_item.relative_to(post_kit_dir)
                    target = dist_dir / rel
                    if not target.exists():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(p_item, target)
            shutil.rmtree(post_kit_dir, ignore_errors=True)
            logger.info("[%s] Pasta redundante post_kit/ consolidada em distribution/ e excluída com sucesso.", slug)

        # ── 3. Limpeza da Raiz do Projeto ──
        # Audio
        root_audio = project_dir / "audio_main.wav"
        canon_audio = project_dir / "audio" / "audio_main.wav"
        if root_audio.exists():
            if not canon_audio.exists() or canon_audio.stat().st_size < 100:
                canon_audio.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(root_audio, canon_audio)
            try:
                root_audio.unlink()
                files_removed += 1
            except Exception:
                pass

        # Subtitles
        root_subtitles = project_dir / "subtitles_en.json"
        canon_subtitles = project_dir / "text" / "subtitles_en.json"
        if root_subtitles.exists():
            if not canon_subtitles.exists() or canon_subtitles.stat().st_size < 50:
                canon_subtitles.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(root_subtitles, canon_subtitles)
            try:
                root_subtitles.unlink()
                files_removed += 1
            except Exception:
                pass

        # Script.md duplicate on root
        root_script = project_dir / "script.md"
        canon_script = project_dir / "text" / "script.md"
        canon_script_en = project_dir / "text" / "script_en.md"
        if root_script.exists():
            if not canon_script.exists() and not canon_script_en.exists():
                canon_script.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(root_script, canon_script)
            try:
                root_script.unlink()
                files_removed += 1
            except Exception:
                pass

        # Manifest and temporary audits
        for root_junk in [
            project_dir / "manifest.json",
            project_dir / "audit_headless_shots.json",
            project_dir / "audit_narration_headless.json",
        ]:
            if root_junk.exists():
                try:
                    root_junk.unlink()
                    files_removed += 1
                except Exception:
                    pass

        after_bytes = get_dir_size(project_dir)
        freed = max(0, before_bytes - after_bytes)
        return {
            "before_bytes": before_bytes,
            "after_bytes": after_bytes,
            "freed_bytes": freed,
            "files_removed": files_removed,
        }

    def execute_all(self, distribution_only: bool = False, specific_project: str | None = None) -> None:
        """Run housekeeping across all or specific project directories in runtime."""
        if specific_project:
            projects = [self.runtime_root / specific_project]
        else:
            projects = sorted([d for d in self.runtime_root.iterdir() if d.is_dir() and not d.name.startswith(".")])

        if distribution_only:
            logger.info("Atualizando kits de distribuição em %d projetos em %s...", len(projects), self.runtime_root)
            for p in projects:
                if p.exists() and p.is_dir():
                    self.ensure_distribution_text_files(p, p.name)
            logger.info("Kits de distribuição atualizados com sucesso.")
            return

        logger.info("Iniciando faxina arquitetural em %d projetos em %s...", len(projects), self.runtime_root)

        total_before = 0
        total_after = 0
        total_freed = 0
        results = []

        for p in projects:
            if not p.exists() or not p.is_dir():
                continue
            res = self.cleanup_project(p)
            total_before += res["before_bytes"]
            total_after += res["after_bytes"]
            total_freed += res["freed_bytes"]
            results.append((p.name, res))

        print("\n" + "=" * 90)
        print("RED SUN JAPAN // RELATÓRIO DE FAXINA E DESDUPLICAÇÃO DE ARMAZENAMENTO")
        print("=" * 90)
        print(f"{'Projeto (Slug)':<55} | {'Antes':>9} | {'Depois':>9} | {'Economia':>9}")
        print("-" * 90)
        for name, r in results:
            b_mb = r["before_bytes"] / (1024 * 1024)
            a_mb = r["after_bytes"] / (1024 * 1024)
            f_mb = r["freed_bytes"] / (1024 * 1024)
            print(f"{name:<55} | {b_mb:>6.1f} MB | {a_mb:>6.1f} MB | {f_mb:>6.1f} MB")

        print("=" * 90)
        total_before_gb = total_before / (1024 * 1024 * 1024)
        total_after_gb = total_after / (1024 * 1024 * 1024)
        total_freed_gb = total_freed / (1024 * 1024 * 1024)
        print(f"ESPAÇO TOTAL ANTES DA FAXINA:  {total_before_gb:>6.2f} GB ({total_before / (1024*1024):,.1f} MB)")
        print(f"ESPAÇO TOTAL APÓS A FAXINA:   {total_after_gb:>6.2f} GB ({total_after / (1024*1024):,.1f} MB)")
        print(f"ESPAÇO EM DISCO SSD LIBERADO:  {total_freed_gb:>6.2f} GB ({total_freed / (1024*1024):,.1f} MB)")
        print("=" * 90 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean up duplicate videos, post_kit, and root clutter in runtime.")
    parser.add_argument("--runtime-root", type=str, default=str(DEFAULT_RUNTIME_ROOT), help="Path to projects directory")
    parser.add_argument("--project", type=str, default=None, help="Specific project slug to process")
    parser.add_argument("--distribution-only", action="store_true", help="Only refresh distribution kits and text files")
    args = parser.parse_args()

    engine = StorageCleanupEngine(runtime_root=Path(args.runtime_root))
    engine.execute_all(distribution_only=args.distribution_only, specific_project=args.project)


if __name__ == "__main__":
    main()
