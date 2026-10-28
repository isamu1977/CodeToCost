"""Universal Distribution Kit Generator for Red Sun Japan.

Generates multi-platform publication kits for YouTube, Pinterest,
Vertical Video (Shorts, Reels, TikTok), and X/Twitter.
Curates media assets, generates platform-specific copy via LLM,
and persists deliverables to `projects/{slug}/distribution/`.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from database import SessionLocal, VideoProject, TranslationVariant, VideoStatus
from llm_client import LLMConfig, _call_openrouter, extract_json_safely
from redsun.services.audio_manager import AudioManager
from redsun.services.thumbnail_generator import ThumbnailGenerator
from redsun.services.social_links import (
    load_social_links,
    format_social_links_block,
    inject_social_links_into_description,
    get_blog_post_url,
)

logger = logging.getLogger("redsun.distribution_kit")

DEFAULT_RUNTIME_ROOT = Path("/Users/isamumatsuyama/Documents/red-sun-japan-runtime/projects")
PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"


def _format_timestamp(seconds: float) -> str:
    """Format seconds into MM:SS timestamp string."""
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m:02d}:{s:02d}"


def _build_default_instagram_kit(
    title: str,
    slug: str,
    script_text: str = "",
    blog_text: str = "",
) -> dict[str, Any]:
    """Generates a high-quality fallback Instagram kit if LLM response omits it."""
    first_paragraph = ""
    if script_text:
        paras = [p.strip() for p in script_text.split("\n\n") if p.strip() and not p.strip().startswith("#")]
        if paras:
            first_paragraph = paras[0]
    if not first_paragraph and blog_text:
        paras = [p.strip() for p in blog_text.split("\n\n") if p.strip() and not p.strip().startswith("#") and not p.strip().startswith("---")]
        if paras:
            first_paragraph = paras[0]
    if not first_paragraph:
        first_paragraph = f"An archival journey into the eerie folklore of feudal Japan, uncovering the spectral mystery of {title}."

    hook = f"The archival truth behind the haunting of {title}."
    caption_body = (
        f"{first_paragraph}\n\n"
        f"Preserved across municipal records and classical chronicles, this tale reveals "
        f"the deep spiritual boundaries of ancient Japan—where the line between the living "
        f"and the unseen world was dangerously fragile."
    )
    cta = "🎬 Watch the full archival documentary on YouTube & read the historical essay on our website.\n🔗 Link in Bio: @redsunjapan_official"
    hashtags = [
        "#JapaneseFolklore",
        "#Kaidan",
        "#EdoPeriod",
        "#Yokai",
        "#JapaneseHistory",
        "#DarkFolklore",
        "#EerieTales",
        "#HistoricalJapan",
        "#JapaneseMythology",
        "#RedSunJapan",
    ]

    reels = [
        {
            "clip_index": 1,
            "caption": (
                f"The initial encounter that defied the natural order in {title}.\n\n"
                f"🎬 Watch the full 15-minute documentary on YouTube (Link in bio @redsunjapan_official)\n\n"
                f"#JapaneseFolklore #Kaidan #EdoPeriod #Yokai #RedSunJapan"
            ),
        },
        {
            "clip_index": 2,
            "caption": (
                f"When the talisman broke: the supernatural truth of {title}.\n\n"
                f"🎬 Watch the full 15-minute documentary on YouTube (Link in bio @redsunjapan_official)\n\n"
                f"#JapaneseFolklore #Kaidan #EdoPeriod #Yokai #RedSunJapan"
            ),
        },
        {
            "clip_index": 3,
            "caption": (
                f"The chilling conclusion documented in temple records of {title}.\n\n"
                f"🎬 Watch the full 15-minute documentary on YouTube (Link in bio @redsunjapan_official)\n\n"
                f"#JapaneseFolklore #Kaidan #EdoPeriod #Yokai #RedSunJapan"
            ),
        },
    ]

    return {
        "feed_carousel": {
            "hook": hook,
            "caption_body": caption_body,
            "call_to_action": cta,
            "hashtags": hashtags,
        },
        "reels": reels,
    }


def format_instagram_posts_markdown(title: str, insta_data: dict[str, Any]) -> str:
    """Formats the Instagram distribution kit into the canonical markdown deliverable."""
    feed = insta_data.get("feed_carousel", {})
    reels = insta_data.get("reels", [])

    hook = feed.get("hook", "").strip()
    caption_body = feed.get("caption_body", "").strip()
    cta = feed.get("call_to_action", "").strip()
    tags = " ".join(feed.get("hashtags", []))

    def _get_reel_caption(index: int) -> str:
        if index < len(reels):
            return reels[index].get("caption", "").strip()
        return ""

    reel_01 = _get_reel_caption(0)
    reel_02 = _get_reel_caption(1)
    reel_03 = _get_reel_caption(2)

    return f"""# Instagram Distribution Kit: {title}

---

## 📸 1. Feed / Carousel Post (Attach images from pins_media/)
**Hook:**
{hook}

**Caption:**
{caption_body}

{cta}

{tags}

---

## 🎬 2. Reel 01 Caption (video/shorts/short_01.mp4)
{reel_01}

---

## 🎬 3. Reel 02 Caption (video/shorts/short_02.mp4)
{reel_02}

---

## 🎬 4. Reel 03 Caption (video/shorts/short_03.mp4)
{reel_03}
"""


def _enrich_shorts_kit(
    raw_shorts: list[dict[str, Any]],
    title: str,
    slug: str,
    script_text: str = "",
    blog_text: str = "",
    shorts_clips_meta: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Enriches and ensures all 3 YouTube shorts have high-CTR titles, full descriptions, and hashtags."""
    enriched: list[dict[str, Any]] = []
    blog_url = f"https://redsunjapan.com/posts/{slug}"

    # Clean short title for branding
    short_title = title.split(":")[0].split("—")[0].split("-")[0].strip()
    if len(short_title) > 30:
        short_title = short_title[:30].strip()

    clips_meta_map: dict[int, dict[str, Any]] = {}
    if shorts_clips_meta:
        for idx, cm in enumerate(shorts_clips_meta, start=1):
            clips_meta_map[idx] = cm

    for idx in range(1, 4):
        existing = next((s for s in raw_shorts if s.get("clip_index") == idx), None)
        if not existing and idx <= len(raw_shorts):
            existing = raw_shorts[idx - 1]

        cm = clips_meta_map.get(idx, {})
        clip_id = (existing and existing.get("clip_id")) or cm.get("id") or f"{slug}_short_{idx:02d}"

        # 1. Caption / Hook
        caption = ""
        if existing and existing.get("caption"):
            caption = str(existing["caption"]).strip()
        elif cm.get("hookText"):
            caption = str(cm["hookText"]).strip()
        elif cm.get("highlight"):
            caption = f"{cm['highlight']} in the chronicle of {title}."
        else:
            defaults = [
                f"The initial eerie encounter from {title} that defied every natural law.",
                f"The terrifying supernatural truth and broken talisman in {title}.",
                f"The chilling climax preserved in historical magistrate records of {title}.",
            ]
            caption = defaults[idx - 1]

        # 2. Title (High CTR, <= 65 chars, ending with #Shorts)
        custom_title = existing.get("title") if existing else None
        if not custom_title or len(str(custom_title).strip()) < 8:
            highlight = cm.get("highlight")
            if highlight:
                candidate = f"{highlight} | {short_title} #Shorts"
            elif idx == 1:
                candidate = f"The Ominous Encounter | {short_title} #Shorts"
            elif idx == 2:
                candidate = f"The Curse Unfolds | {short_title} #Shorts"
            else:
                candidate = f"The Supernatural Truth | {short_title} #Shorts"

            if len(candidate) > 65:
                candidate = f"{short_title} Part {idx} #Shorts"
            custom_title = candidate
        else:
            custom_title = str(custom_title).strip()
            if not custom_title.endswith("#Shorts") and not custom_title.endswith("#shorts"):
                if len(custom_title) <= 57:
                    custom_title = f"{custom_title} #Shorts"

        # 3. Hashtags
        tags = existing.get("hashtags") if existing else None
        if not tags or not isinstance(tags, list) or len(tags) < 3:
            tags = ["#Shorts", "#YouTubeShorts", "#JapaneseFolklore", "#Kaidan", "#Yokai", "#EdoPeriod", "#EerieTales"]
        else:
            tags = [t if t.startswith("#") else f"#{t}" for t in tags]
            if not any(t.lower() == "#shorts" for t in tags):
                tags.insert(0, "#Shorts")
            if not any(t.lower() == "#youtubeshorts" for t in tags) and len(tags) < 8:
                tags.insert(1, "#YouTubeShorts")

        tags_str = " ".join(tags)

        # 4. Description
        custom_desc = existing.get("description") if existing else None
        if not custom_desc or len(str(custom_desc).strip()) < 30:
            custom_desc = (
                f"{caption}\n\n"
                f"From the Japanese historical chronicle: \"{title}\".\n"
                f"Watch the full 15-minute documentary & read the historical footnotes:\n"
                f"🔗 Full Chronicle: {blog_url}\n\n"
                f"⛩️ Subscribe to @RedSunJapan for weekly archival Japanese folklore & kaidan documentaries.\n\n"
                f"{tags_str}"
            )
        else:
            custom_desc = str(custom_desc).strip()
            if blog_url not in custom_desc:
                custom_desc += f"\n\n🔗 Full Chronicle & Essay: {blog_url}"
            if "@RedSunJapan" not in custom_desc:
                custom_desc += "\n⛩️ Subscribe to @RedSunJapan for weekly archival Japanese folklore documentaries."
            if "#Shorts" not in custom_desc and "#shorts" not in custom_desc:
                custom_desc += f"\n\n{tags_str}"

        enriched.append({
            "clip_index": idx,
            "clip_id": clip_id,
            "title": custom_title,
            "description": custom_desc,
            "caption": caption,
            "hashtags": tags,
        })

    return enriched


def format_youtube_shorts_text(title: str, slug: str, shorts: list[dict[str, Any]]) -> str:
    """Formats plain-text YouTube Shorts kit for easy copy-pasting."""
    lines = [
        "=" * 80,
        "RED SUN JAPAN // YOUTUBE SHORTS DISTRIBUTION KIT",
        f"Project: {title} (Slug: {slug})",
        "=" * 80,
        "",
    ]
    for s in shorts:
        idx = s.get("clip_index", 1)
        s_title = s.get("title", f"{title} Part {idx} #Shorts")
        s_desc = s.get("description", "")
        s_tags = " ".join(s.get("hashtags", []))
        lines.extend([
            f"--- YOUTUBE SHORT #{idx:02d} (video/shorts/short_{idx:02d}.mp4) ---",
            "[TITLE]",
            s_title,
            "",
            "[DESCRIPTION]",
            s_desc,
            "",
            "[HASHTAGS]",
            s_tags,
            "",
            "=" * 80,
            "",
        ])
    return "\n".join(lines).strip() + "\n"


def sanitize_distribution_hashtags(
    kit_data: dict[str, Any],
    title: str,
    script_text: str,
    slug: str,
) -> dict[str, Any]:
    """Dynamically purges hardcoded #SamuraiHistory if the script/title does not contain
    samurai entities, and injects thematic tags (yokai, kaidan, folklore, etc.)."""
    full_text = f"{title} {slug} {script_text}".lower()
    has_samurai = bool(re.search(r"\b(samurai|bushi|katana|shogun|ronin|daimyo|swordsman|hatamoto)\b", full_text))

    thematic_tags = ["#JapaneseFolklore"]
    if any(k in full_text for k in ("snow", "blizzard", "yuki", "winter", "ice")):
        thematic_tags.append("#SnowSpirit")
    if any(k in full_text for k in ("yokai", "yōkai", "spirit", "monster", "demon", "kappa", "oni")):
        thematic_tags.append("#Yokai")
    if any(k in full_text for k in ("kaidan", "ghost", "haunting", "curse", "yurei", "spectral")):
        thematic_tags.append("#Kaidan")
    thematic_tags.extend(["#EdoPeriod", "#EerieTales"])

    def clean_tags(tags: list[str]) -> list[str]:
        cleaned = []
        for t in tags:
            tag_clean = t.strip()
            if not has_samurai and any(s in tag_clean.lower() for s in ("samurai", "bushido", "shogun")):
                continue
            if tag_clean and tag_clean not in cleaned:
                cleaned.append(tag_clean)
        for th in thematic_tags:
            if th not in cleaned and len(cleaned) < 5:
                cleaned.append(th)
        return cleaned

    # Sanitize shorts hashtags
    for short_item in kit_data.get("shorts", []):
        current_tags = short_item.get("hashtags", [])
        clean = clean_tags(current_tags)
        if not any(t.lower() == "#shorts" for t in clean):
            clean.insert(0, "#Shorts")
        if not any(t.lower() == "#youtubeshorts" for t in clean) and len(clean) < 8:
            clean.insert(1, "#YouTubeShorts")
        short_item["hashtags"] = clean

    # Sanitize youtube tags / hashtags
    yt = kit_data.get("youtube", {})
    if "hashtags" in yt:
        yt["hashtags"] = clean_tags(yt.get("hashtags", []))
    if "tags" in yt:
        cleaned_yt_tags = []
        for tag in yt.get("tags", []):
            if not has_samurai and any(s in tag.lower() for s in ("samurai", "bushido", "shogun")):
                continue
            if tag not in cleaned_yt_tags:
                cleaned_yt_tags.append(tag)
        yt["tags"] = cleaned_yt_tags

    # Sanitize instagram hashtags
    insta = kit_data.get("instagram", {})
    if isinstance(insta, dict):
        feed = insta.get("feed_carousel", {})
        if isinstance(feed, dict) and "hashtags" in feed:
            feed["hashtags"] = clean_tags(feed.get("hashtags", []))

    return kit_data


class DistributionKitGenerator:
    """Orchestrates asset curation and multi-platform copy generation."""

    def __init__(self, runtime_root: Path | None = None) -> None:
        self.runtime_root = runtime_root or DEFAULT_RUNTIME_ROOT

    def select_pin_images(self, project_dir: Path) -> list[tuple[Path, str]]:
        """Select 4 representative images across the narrative timeline and map to pin names."""
        candidate_dirs = [
            project_dir / "gemini-assistant" / "output",
            project_dir / "images" / "final",
            project_dir / "images",
        ]

        shots: list[Path] = []
        for cdir in candidate_dirs:
            if cdir.exists():
                found = [
                    f
                    for f in cdir.iterdir()
                    if f.is_file()
                    and f.suffix.lower() in {".jpeg", ".jpg", ".png", ".webp"}
                    and not f.name.startswith(".")
                ]
                if found:
                    shots = found
                    break

        def _sort_key(p: Path) -> int:
            m = re.search(r"(\d+)", p.stem)
            return int(m.group(1)) if m else 999999

        sorted_shots = sorted(shots, key=_sort_key)
        if not sorted_shots:
            return []

        n = len(sorted_shots)
        if n <= 4:
            selected = sorted_shots
        else:
            indices = [
                0,
                int(n * 0.25),
                int(n * 0.50),
                min(int(n * 0.75), n - 1),
            ]
            # Ensure unique indices in ascending order
            seen = set()
            clean_indices = []
            for idx in indices:
                if idx not in seen:
                    clean_indices.append(idx)
                    seen.add(idx)
            selected = [sorted_shots[i] for i in clean_indices]

        # Map to pin_01.jpeg, pin_02.jpeg, etc.
        result = []
        for i, s in enumerate(selected, start=1):
            pin_name = f"pin_{i:02d}{s.suffix.lower()}"
            result.append((s, pin_name))

        return result

    def _inject_bgm_credit_to_blog(self, slug: str, credit_text: str) -> None:
        """Inject BGM legal attribution into the blog post Markdown footer if not already present."""
        if not credit_text:
            return

        blog_candidates = [
            Path("/Users/isamumatsuyama/Documents/online-projects/red-sun-japan/red-sun-project-files/redsunjapan-blog/src/content/blog") / f"{slug}.md",
            self.runtime_root / slug / f"{slug}.md",
        ]

        attribution_section = (
            f"\n\n### Soundtrack & Audio Credits\n\n"
            f"*{credit_text}*\n"
        )

        for bp in blog_candidates:
            if bp.exists():
                try:
                    text = bp.read_text(encoding="utf-8")
                    if "Kraosando" not in text and credit_text not in text:
                        new_text = text.rstrip() + attribution_section
                        bp.write_text(new_text, encoding="utf-8")
                        logger.info("Injected soundtrack attribution into blog post %s", bp)
                except Exception as exc:
                    logger.warning("Could not update blog file %s with soundtrack credit: %s", bp, exc)

    def extract_chapters_from_subtitles(
        self, subtitles_path: Path, video_path: Path | None = None
    ) -> tuple[float, list[str]]:
        """Derive narrative chapters and estimated duration from subtitles or video."""
        total_duration = 0.0
        words_data: list[dict[str, Any]] = []

        if subtitles_path.exists():
            try:
                raw = json.loads(subtitles_path.read_text(encoding="utf-8"))
                if isinstance(raw, list):
                    words_data = raw
                    if words_data and "end" in words_data[-1]:
                        total_duration = float(words_data[-1]["end"])
                elif isinstance(raw, dict) and "segments" in raw:
                    segs = raw["segments"]
                    if segs and "end" in segs[-1]:
                        total_duration = float(segs[-1]["end"])
            except Exception as exc:
                logger.warning("Could not read subtitles for timestamps: %s", exc)

        if total_duration <= 0 and video_path and video_path.exists():
            # Fallback estimation based on file size or default 15 min
            total_duration = 900.0

        if total_duration <= 0:
            total_duration = 600.0

        chapters = ["00:00 - Introduction & The Haunting Prelude"]
        if total_duration > 180:
            c1 = total_duration * 0.25
            c2 = total_duration * 0.50
            c3 = total_duration * 0.75
            chapters.append(f"{_format_timestamp(c1)} - The Midnight Encounter")
            chapters.append(f"{_format_timestamp(c2)} - The Unfolding Curse & Archival Lore")
            chapters.append(f"{_format_timestamp(c3)} - The Breaking of the Talisman")
        chapters.append(f"{_format_timestamp(total_duration)} - Historical Legacy & Epilogue")

        return total_duration, chapters

    def generate_distribution_kit(
        self,
        project_id: int | str,
        force_regenerate: bool = False,
        source_image_filename: Optional[str] = None,
    ) -> dict[str, Any]:
        """Execute complete generation of the Universal Distribution Kit for a project."""
        logger.info("Starting Universal Distribution Kit generation for '%s'...", project_id)

        # 1. Load project from Database
        db = SessionLocal()
        try:
            if isinstance(project_id, int) or (isinstance(project_id, str) and project_id.isdigit()):
                project = db.query(VideoProject).filter(VideoProject.id == int(project_id)).first()
            else:
                project = db.query(VideoProject).filter(VideoProject.slug == str(project_id)).first()

            if not project:
                raise ValueError(f"Project '{project_id}' not found in database.")

            p_id = project.id
            slug = project.slug or f"project-{project.id}"
            title = project.title

            variant = db.query(TranslationVariant).filter(
                TranslationVariant.project_id == p_id,
                TranslationVariant.language_code == "en",
            ).first()

            script_text = (variant.script if variant else "") or ""
            blog_text = (variant.blog_article if variant else "") or ""
        finally:
            db.close()

        from redsun.workspace import ProjectWorkspace

        ws = ProjectWorkspace.from_slug(slug, project_id=p_id, projects_root=self.runtime_root)
        project_dir = ws.root
        if not project_dir.exists():
            ws.create()

        dist_dir = project_dir / "distribution"
        dist_dir.mkdir(parents=True, exist_ok=True)
        pins_media_dir = dist_dir / "pins_media"
        pins_media_dir.mkdir(parents=True, exist_ok=True)

        json_out_file = dist_dir / "social_distribution_kit.json"
        if not force_regenerate and json_out_file.exists():
            try:
                cached = json.loads(json_out_file.read_text(encoding="utf-8"))
                cached_shorts = cached.get("shorts", [])
                has_complete_shorts = (
                    isinstance(cached_shorts, list)
                    and len(cached_shorts) >= 3
                    and all(s.get("title") and s.get("description") for s in cached_shorts)
                )
                if (
                    "youtube" in cached
                    and "pinterest" in cached
                    and "twitter_thread" in cached
                    and "instagram" in cached
                    and has_complete_shorts
                    and (dist_dir / "youtube_shorts.txt").exists()
                ):
                    logger.info("Found cached distribution kit for '%s'.", slug)
                    return cached
            except Exception:
                pass

        # 2. Extract chapters and duration from subtitles
        subtitles_file = project_dir / "text" / "subtitles_en.json"
        if not subtitles_file.exists():
            subtitles_file = project_dir / "transcript.json"

        video_path = project_dir / "video" / f"{slug}_master.mp4"
        duration_sec, chapters = self.extract_chapters_from_subtitles(subtitles_file, video_path)
        timestamps_str = "\n".join(chapters)

        # 3. Read generated shorts context (Passo #15)
        shorts_meta_candidates = [
            project_dir / "video" / "shorts" / "shorts_metadata.json",
            project_dir / "shorts" / "shorts_metadata.json",
        ]
        shorts_meta_file = next((p for p in shorts_meta_candidates if p.exists()), None)

        # Purge legacy project_dir / "shorts" if present
        legacy_shorts = project_dir / "shorts"
        if legacy_shorts.exists() and legacy_shorts.is_dir():
            shutil.rmtree(legacy_shorts, ignore_errors=True)

        shorts_context = ""
        shorts_clips_meta: list[dict[str, Any]] = []
        if shorts_meta_file and shorts_meta_file.exists():
            try:
                shorts_data = json.loads(shorts_meta_file.read_text(encoding="utf-8"))
                shorts_clips_meta = shorts_data.get("clips", [])
                shorts_context = json.dumps(shorts_clips_meta, indent=2)
            except Exception as exc:
                logger.warning("Failed to parse shorts_metadata.json: %s", exc)

        if not shorts_context:
            shorts_context = f"- Short 01: Initial mysterious encounter from {title}\n- Short 02: Supernatural revelation and curse\n- Short 03: Fatal climax and broken seal"

        # 4. Copy 4 Pin images to pins_media/
        selected_pin_images = self.select_pin_images(project_dir)
        copied_pins: list[dict[str, Any]] = []
        for src, pin_filename in selected_pin_images:
            dst = pins_media_dir / pin_filename
            shutil.copy2(src, dst)
            copied_pins.append({
                "filename": pin_filename,
                "source_file": src.name,
                "local_path": str(dst),
            })
            logger.info("Copied pin image for %s: %s -> %s", slug, src.name, pin_filename)

        # 5. Call LLM for Structured Kit
        sys_prompt_path = PROMPTS_DIR / "distribution_kit_system.md"
        user_prompt_path = PROMPTS_DIR / "distribution_kit_user.md"

        if not sys_prompt_path.exists() or not user_prompt_path.exists():
            raise FileNotFoundError("Missing distribution_kit prompt templates in prompts/ directory.")

        sys_prompt = sys_prompt_path.read_text(encoding="utf-8")
        user_template = user_prompt_path.read_text(encoding="utf-8")

        # Load official brand and shop links (Printify, Instagram, Pinterest, X, Blog)
        social_links = load_social_links()
        social_links_block = format_social_links_block(slug=slug, links=social_links)
        blog_post_url = get_blog_post_url(slug=slug, links=social_links)

        # Trim script/blog to safe context length if very long
        user_prompt = user_template.format(
            title=title,
            slug=slug,
            blog_post_url=blog_post_url,
            social_links_block=social_links_block,
            timestamps_text=timestamps_str,
            shorts_context=shorts_context,
            script=script_text[:12000] if script_text else "(Script not available)",
            blog_article=blog_text[:12000] if blog_text else "(Blog article not available)",
        )

        cfg = LLMConfig.from_env().for_utility()
        logger.info("Calling LLM (%s) for Universal Distribution Kit for '%s'...", cfg.model, slug)

        res = _call_openrouter(
            sys_prompt,
            user_prompt,
            cfg=cfg,
            json_mode=False,
            temperature=0.5,
            max_tokens=4000,
        )

        parsed = extract_json_safely(res.text)
        if not isinstance(parsed, dict) or "youtube" not in parsed:
            raise ValueError(f"LLM did not return valid distribution kit JSON. Snippet: {res.text[:500]}")

        # Dynamically sanitize hashtags and copy
        parsed = sanitize_distribution_hashtags(parsed, title=title, script_text=script_text, slug=slug)

        # Enrich and guarantee YouTube Shorts metadata (titles, full descriptions, hashtags)
        raw_shorts = parsed.get("shorts", [])
        enriched_shorts = _enrich_shorts_kit(
            raw_shorts=raw_shorts,
            title=title,
            slug=slug,
            script_text=script_text,
            blog_text=blog_text,
            shorts_clips_meta=shorts_clips_meta,
        )
        parsed["shorts"] = enriched_shorts

        # Ensure instagram block is present and properly structured
        insta = parsed.get("instagram")
        if not isinstance(insta, dict) or "feed_carousel" not in insta or "reels" not in insta:
            logger.warning("LLM response missing complete instagram kit for '%s'; building default fallback.", slug)
            insta = _build_default_instagram_kit(title=title, slug=slug, script_text=script_text, blog_text=blog_text)
            parsed["instagram"] = insta

        # Ensure Pinterest items have local pin file associations
        pinterest_items = parsed.get("pinterest", [])
        for i, pin in enumerate(pinterest_items):
            if i < len(copied_pins):
                pin["media_file"] = copied_pins[i]["filename"]
                pin["media_path"] = copied_pins[i]["local_path"]

        # Ensure destination links use the parameterized post URL
        for pin in pinterest_items:
            pin["destination_link"] = blog_post_url

        # Resolve BGM and Legal Attribution
        bgm_track = None
        bgm_credit = ""
        try:
            db_bgm = SessionLocal()
            try:
                bgm_track = AudioManager.select_next_bgm(db_bgm, p_id)
                if bgm_track:
                    bgm_credit = AudioManager.format_attribution_text(bgm_track)
            finally:
                db_bgm.close()
        except Exception as exc:
            logger.warning("Could not resolve BGM attribution for %s: %s", slug, exc)

        # Ensure official brand links and legal attribution are present in YouTube description
        yt = parsed.get("youtube", {})
        desc = yt.get("description", "")

        # 1. Inject official brand & shop links if not already present
        desc = inject_social_links_into_description(desc, slug=slug, links=social_links)

        # 1b. Inject Ko-fi support line right after official links and before audio attribution if not present
        kofi_line = "🍵 Support our independent translations on Ko-fi: https://ko-fi.com/redsunjapan"
        if kofi_line not in desc:
            if "Soundtrack & Audio Attribution" in desc:
                idx = desc.find("Soundtrack & Audio Attribution")
                desc = desc[:idx].rstrip() + f"\n\n{kofi_line}\n\n" + desc[idx:].lstrip()
            elif "\n#" in desc:
                idx = desc.rfind("\n#")
                desc = desc[:idx].rstrip() + f"\n\n{kofi_line}\n\n" + desc[idx:].lstrip()
            else:
                desc = desc.rstrip() + f"\n\n{kofi_line}\n"

        yt["description"] = desc

        # Inject legal soundtrack attribution into blog post footer
        if bgm_credit:
            self._inject_bgm_credit_to_blog(slug, bgm_credit)

        # 6. Render Official YouTube Thumbnail (Passo #16 Deliverable)
        thumbnail_meta: dict[str, Any] = {}
        try:
            tg = ThumbnailGenerator()
            thumb_copy = yt.get("thumbnail") if isinstance(yt, dict) else None
            thumb_res = tg.render_thumbnail(
                project_dir=project_dir,
                slug=slug,
                title=title,
                copy_data=thumb_copy,
                source_image_filename=source_image_filename,
            )
            thumbnail_meta = thumb_res
            logger.info("Official YouTube thumbnail rendered for %s: %s", slug, thumb_res.get("path"))
        except Exception as exc:
            logger.error("Failed to render YouTube thumbnail for %s: %s", slug, exc)
            thumbnail_meta = {
                "status": "FAILED",
                "error": str(exc),
                "relative_path": "distribution/thumbnail.png",
            }

        # 7. Create Artifact Files

        # File A: social_distribution_kit.json
        full_kit_payload = {
            "project_id": p_id,
            "slug": slug,
            "title": title,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "selected_bgm": bgm_track.get("id") if bgm_track else None,
            "bgm": bgm_track,
            "bgm_credit": bgm_credit,
            "social_links": social_links,
            "thumbnail": thumbnail_meta,
            "youtube": yt,
            "pinterest": pinterest_items,
            "shorts": parsed.get("shorts", []),
            "instagram": insta,
            "twitter_thread": parsed.get("twitter_thread", []),
            "pins_media": copied_pins,
            "timestamps": chapters,
            "duration_seconds": duration_sec,
        }
        json_out_file.write_text(json.dumps(full_kit_payload, indent=2, ensure_ascii=False), encoding="utf-8")

        # File D: pinterest_pins.json
        # File D: pinterest_pins.json & pinterest_pins.txt
        (dist_dir / "pinterest_pins.json").write_text(
            json.dumps({"pins": pinterest_items}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        pin_txt_lines = [
            "=" * 80,
            "RED SUN JAPAN // PINTEREST DISTRIBUTION KIT",
            f"Project: {title} (Slug: {slug})",
            "=" * 80,
            "",
        ]
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
        logger.info("Saved Pinterest pins text kit to %s", dist_dir / "pinterest_pins.txt")

        # File E: instagram_posts.md & instagram_posts.txt
        insta_md_content = format_instagram_posts_markdown(title, insta)
        (dist_dir / "instagram_posts.md").write_text(insta_md_content, encoding="utf-8")
        logger.info("Saved Instagram posts markdown to %s", dist_dir / "instagram_posts.md")

        feed = insta.get("feed_carousel", {})
        reels = insta.get("reels", [])

        feed_hook = feed.get("hook", "").strip()
        feed_body = feed.get("caption_body", "").strip()
        feed_cta = feed.get("call_to_action", "").strip()
        feed_tags = " ".join(feed.get("hashtags", []))

        insta_lines = [
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
            insta_lines.extend([
                f"\n--- REEL #{clip_idx:02d} (video/shorts/short_{clip_idx:02d}.mp4) ---",
                c_text,
                "",
            ])
        (dist_dir / "instagram_posts.txt").write_text("\n".join(insta_lines), encoding="utf-8")
        (dist_dir / "instagram_kit.txt").write_text("\n".join(insta_lines), encoding="utf-8")
        logger.info("Saved Instagram text kit to %s", dist_dir / "instagram_posts.txt")

        # File B: twitter_thread.md & twitter_thread.txt
        tweets = parsed.get("twitter_thread", [])
        total_tweets = len(tweets)
        thread_md_lines = [f"# X/Twitter Thread: {title}\n"]
        thread_txt_lines = [
            "=" * 80,
            "RED SUN JAPAN // X / TWITTER THREAD",
            f"Project: {title} (Slug: {slug})",
            "=" * 80,
            "",
        ]
        kofi_url = "https://ko-fi.com/redsunjapan"
        for i, t in enumerate(tweets, start=1):
            text = t.get("text", "").strip()
            num = t.get("tweet_number", i)
            # Ensure Tweet 6 (or last tweet) mentions Ko-fi support
            if (num == 6 or (i == total_tweets and total_tweets >= 5)) and kofi_url not in text:
                text = f"{text}\n🍵 Support our independent translations: {kofi_url}"
                t["text"] = text

            thread_md_lines.append(f"---\n🧵 ({num}/{total_tweets})\n{text}\n")
            thread_txt_lines.extend([
                f"--- Tweet {num}/{total_tweets} ---",
                text,
                "",
            ])
        (dist_dir / "twitter_thread.md").write_text("\n".join(thread_md_lines), encoding="utf-8")
        (dist_dir / "twitter_thread.txt").write_text("\n".join(thread_txt_lines), encoding="utf-8")
        logger.info("Saved Twitter thread text kit to %s", dist_dir / "twitter_thread.txt")

        # File C: youtube_kit.txt
        title_opts = yt.get("title_options", [])
        tags = yt.get("tags", [])
        pinned_comment = (
            f"⛩️ Which moment of the {title} chronicle chilled you the most?\n\n"
            f"• Read the archival essay & historical footnotes: {blog_post_url}\n"
            f"• Buy the Archive a Tea: https://ko-fi.com/redsunjapan\n\n"
            f"Subscribe to Red Sun Japan for weekly archival folklore & kaidan documentaries."
        )
        yt["pinned_comment"] = pinned_comment

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
            f"Kicker: {thumbnail_meta.get('kicker', 'N/A')}",
            f"Headline Line 1: {thumbnail_meta.get('headline_line1', 'N/A')}",
            f"Headline Highlight: {thumbnail_meta.get('headline_highlight', 'N/A')}",
            "",
            "-" * 80,
            "[OFFICIAL BRAND & STORE LINKS]",
            social_links_block,
            "",
            "-" * 80,
            "[DESCRIPTION]",
            desc,
            "",
            "-" * 80,
            "[CHAPTERS / TIMESTAMPS]",
            timestamps_str,
            "",
            "-" * 80,
            "[SUGGESTED PINNED COMMENT]",
            pinned_comment,
            "",
            "-" * 80,
            "[SOUNDTRACK / BGM CREDITS]",
            bgm_credit or "N/A",
            "",
            "-" * 80,
            "[TAGS]",
            ", ".join(tags),
            "",
            "=" * 80,
            "[YOUTUBE SHORTS (3 CLIPS)]",
            "=" * 80,
        ])
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
        logger.info("Saved YouTube text kit to %s", dist_dir / "youtube_kit.txt")

        # File F: youtube_shorts.txt
        shorts_txt_content = format_youtube_shorts_text(title, slug, enriched_shorts)
        (dist_dir / "youtube_shorts.txt").write_text(shorts_txt_content, encoding="utf-8")
        logger.info("Saved YouTube Shorts text kit to %s", dist_dir / "youtube_shorts.txt")

        # Eliminate legacy post_kit folder completely
        legacy_dir = project_dir / "post_kit"
        if legacy_dir.exists():
            shutil.rmtree(legacy_dir, ignore_errors=True)
            logger.info("Purged redundant legacy post_kit folder: %s", legacy_dir)

        # 7. Update status in Database and checkpoints (never force RENDERED if unrendered)
        db = SessionLocal()
        try:
            p = db.query(VideoProject).filter(VideoProject.id == p_id).first()
            if p and p.status in (VideoStatus.RENDERED, VideoStatus.PUBLISHED):
                pass  # Keep rendered/published status
        finally:
            db.close()

        checkpoints_dir = project_dir / "checkpoints"
        checkpoints_dir.mkdir(parents=True, exist_ok=True)
        (checkpoints_dir / "phase_distribution_kit.json").write_text(
            json.dumps({
                "status": "COMPLETED",
                "distribution_dir": str(dist_dir),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }, indent=2),
            encoding="utf-8",
        )

        logger.info("Universal Distribution Kit successfully generated for '%s' in %s", slug, dist_dir)
        return full_kit_payload


def generate_distribution_kit(
    project_id: int | str,
    force_regenerate: bool = False,
    runtime_root: Path | None = None,
    source_image_filename: Optional[str] = None,
) -> dict[str, Any]:
    """Helper entry point for distribution kit generation."""
    generator = DistributionKitGenerator(runtime_root=runtime_root)
    return generator.generate_distribution_kit(
        project_id,
        force_regenerate=force_regenerate,
        source_image_filename=source_image_filename,
    )
