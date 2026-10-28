"""Post-publishing kit generator for Red Sun Japan.

Phase 6+ — turns a fully-rendered project into a complete post kit
(YouTube description, social captions, thumbnail brief, post metadata)
that the operator can copy-paste into YouTube Studio, Twitter, etc.

The kit lives in ``post_kits/<slug>.<lang>/`` (gitignored, regenerated per
project). The operator reviews each file, fills in the thumbnail brief
in Affinity, then publishes manually until Phase 7 wires up the YouTube
Data API.

Output files (per project):
  - youtube_description.md       — full YouTube video description
  - youtube_pinned_comment.md    — engagement hook, fixed to first comment
  - social_captions.md           — Twitter/X, Instagram, TikTok captions
  - thumbnail_brief.md           — Affinity designer spec (1920x1080)
  - post_metadata.json           — consolidated, ready for upload scripts

The content is derived from the existing project artifacts:
  - title, slug, keywords from VideoProject
  - script (narration) from TranslationVariant.script
  - blog article + frontmatter from TranslationVariant.blog_article
  - duration, audio path, video path from variant
  - BGM metadata from manifest.json (if present)
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("redsun.postkit")

ROOT = Path(__file__).parent
POST_KITS_DIR = ROOT / "post_kits"

# YouTube category IDs we care about.
# Source: https://developers.google.com/youtube/v3/docs/videoCategories/list
YOUTUBE_CATEGORY_EDUCATION = "27"
YOUTUBE_CATEGORY_PEOPLE_BLOGS = "22"
YOUTUBE_CATEGORY_ENTERTAINMENT = "24"
YOUTUBE_CATEGORY_HOWTO_STYLE = "26"

# Default hashtags by mood (used across all social platforms)
DEFAULT_HASHTAGS = ["#RedSunJapan", "#JapaneseHistory", "#Documentary"]
MOOD_HASHTAGS = {
    "yokai": ["#yokai", "#JapaneseFolklore", "#Yukai"],
    "kaidan": ["#GhostStories", "#Kaidan", "#Horror"],
    "samurai": ["#Samurai", "#Bushido", "#Shogun"],
    "battle": ["#FamousBattles", "#History"],
    "snow": ["#Winter", "#SnowSpirits"],
    "mountain": ["#MountainFolk", "#RuralJapan"],
}


@dataclass
class PostKitPaths:
    """All paths in a generated post kit."""

    root: Path
    youtube_description: Path
    youtube_pinned_comment: Path
    social_captions: Path
    thumbnail_brief: Path
    post_metadata: Path


def _detect_mood(slug: str, tags: list[str], title: str) -> str:
    """Heuristic mood detection for hashtag/tone selection."""
    text = (slug + " " + " ".join(tags) + " " + title).lower()
    for mood in MOOD_HASHTAGS:
        if mood in text:
            return mood
    return "default"


def _gather_hashtags(slug: str, tags: list[str], title: str, script: str = "") -> list[str]:
    """Build the final hashtag list for the project, dynamically sanitizing non-samurai chronicles."""
    full_text = f"{title} {slug} {' '.join(tags)} {script}".lower()
    has_samurai = bool(re.search(r"\b(samurai|bushi|katana|shogun|ronin|daimyo|swordsman)\b", full_text))

    mood = _detect_mood(slug, tags, title)
    if mood == "samurai" and not has_samurai:
        mood = "kaidan" if any(k in full_text for k in ("ghost", "kaidan", "curse", "yurei")) else "yokai"

    out = list(DEFAULT_HASHTAGS)
    if mood in MOOD_HASHTAGS:
        out = MOOD_HASHTAGS[mood] + out

    # Map project tags to hashtags
    for tag in tags[:3]:
        h = "#" + tag.title().replace(" ", "")
        if h not in out:
            out.append(h)

    # Strictly filter out samurai hashtags if the script has no samurai entities
    if not has_samurai:
        out = [h for h in out if not any(s in h.lower() for s in ("samurai", "bushido", "shogun"))]

    return out


def _extract_chapters(script: str, audio_duration_sec: float) -> list[dict]:
    """Best-effort chapter detection from the narration script.

    We split on sentence boundaries, then map each section's character
    offset to a timestamp proportional to audio duration. Crude, but
    good enough for a "first 10s = intro, middle = story, last 10s = outro"
    shape. Operators can refine in YouTube Studio.
    """
    if not script or audio_duration_sec <= 0:
        return []

    # Split on sentence-ending punctuation
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", script) if s.strip()]
    if not sentences:
        return []

    # Group sentences into ~5 chapters for a typical 90s video
    target_chapters = 5
    sentences_per_chapter = max(1, len(sentences) // target_chapters)

    chapters: list[dict] = []
    total_chars = sum(len(s) for s in sentences)

    for i in range(0, len(sentences), sentences_per_chapter):
        chunk = sentences[i : i + sentences_per_chapter]
        if not chunk:
            continue
        # Cumulative char position before this chunk
        chars_before = sum(len(s) for s in sentences[:i])
        start_sec = (chars_before / total_chars) * audio_duration_sec
        chars_in_chunk = sum(len(s) for s in chunk)
        end_sec = ((chars_before + chars_in_chunk) / total_chars) * audio_duration_sec

        # First sentence's first 5 words as the chapter title
        first = chunk[0]
        title_words = " ".join(first.split()[:5])
        if len(first.split()) > 5:
            title_words += "..."

        chapters.append({
            "title": title_words,
            "start_sec": round(start_sec, 1),
            "end_sec": round(end_sec, 1),
        })

    return chapters


def _format_chapters_youtube(chapters: list[dict]) -> str:
    """Format chapters list as YouTube's required `0:00 Title` format."""
    lines: list[str] = []
    for ch in chapters:
        total_sec = int(ch["start_sec"])
        mm = total_sec // 60
        ss = total_sec % 60
        lines.append(f"{mm}:{ss:02d} {ch['title']}")
    return "\n".join(lines)


def _summarize_for_description(blog_md: str, max_chars: int = 800) -> str:
    """Pull the first paragraph(s) of the blog body for the YouTube description.

    Stops at the first ``## `` (H2) heading boundary, so we don't pull
    Sources / Further Reading / etc. into the description.
    """
    if not blog_md:
        return ""
    # Strip frontmatter
    parts = blog_md.split("---", 2)
    body = parts[2].lstrip("\n") if len(parts) >= 3 else blog_md
    # Find paragraphs between the H1 and the first H2
    lines = body.splitlines()
    collected: list[str] = []
    past_h1 = False
    for line in lines:
        s = line.strip()
        if s.startswith("# "):
            past_h1 = True
            continue
        # Stop at the first H2 (## ...) — that's where Sources / etc. start
        if past_h1 and s.startswith("## "):
            break
        if past_h1 and s and not s.startswith("#"):
            collected.append(s)
        if sum(len(c) for c in collected) > max_chars:
            break
    summary = " ".join(collected)
    if len(summary) > max_chars:
        summary = summary[: max_chars - 3].rsplit(" ", 1)[0] + "..."
    return summary


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0] + "…"


def generate_youtube_description(
    project_title: str,
    project_slug: str,
    summary: str,
    hashtags: list[str],
    chapters: list[dict],
    sources: list[str],
    blog_url: str,
    bgm_attribution: str | None,
) -> str:
    """Build the YouTube description (markdown — YouTube renders a subset)."""
    parts: list[str] = []
    parts.append(f"# {project_title}\n")
    parts.append(summary)
    parts.append("")

    if blog_url:
        parts.append(f"📚 Read the full article: {blog_url}")
        parts.append("")

    if chapters:
        parts.append("**Chapters**")
        parts.append("```")
        parts.append(_format_chapters_youtube(chapters))
        parts.append("```")
        parts.append("")

    if bgm_attribution:
        parts.append(f"🎵 Music: {bgm_attribution}")
        parts.append("")

    if sources:
        parts.append("**Sources & Further Reading**")
        for s in sources[:6]:
            parts.append(f"- {s}")
        parts.append("")

    parts.append("**Follow Red Sun Japan**")
    parts.append("- YouTube: https://youtube.com/@redsunjapan")
    parts.append("- Blog: https://redsunjapan.com")
    parts.append("")
    parts.append(" ".join(hashtags))
    parts.append("")
    parts.append(
        "© Red Sun Japan. Historical content presented for educational purposes. "
        "All public-domain sources cited above."
    )
    return "\n".join(parts).strip() + "\n"


def generate_pinned_comment(
    project_title: str,
    blog_summary: str,
) -> str:
    """Build a YouTube pinned-comment hook (engagement)."""
    hook = (
        f"Which detail from **{project_title}** surprised you most?\n\n"
        "I spent weeks researching this one — the source material was "
        "largely untranslated Edo-period kaidan, so there are a few "
        "details that don't usually make it into English retellings. "
    )
    if blog_summary:
        hook += (
            "I've written up one of them in the blog post (link in description) — "
            "but I'd love to hear which one catches your ear first."
        )
    hook += "\n\n👇 Tell me in the comments."
    return hook + "\n"


def generate_social_captions(
    project_title: str,
    summary: str,
    hashtags: list[str],
    video_url: str,
    slug: str,
) -> str:
    """Build social-media captions (Twitter, Instagram, TikTok)."""
    parts: list[str] = []
    hashtag_str = " ".join(hashtags[:5])

    # Twitter / X (max 280 chars, but be safe)
    parts.append("# Twitter / X (≤ 280 chars)\n")
    tweet = f"{project_title} — a Japanese snow spirit who kills with a kiss, told in 86 seconds.\n\n{video_url}\n\n{hashtag_str}"
    parts.append("```")
    parts.append(_truncate(tweet, 280))
    parts.append("```\n")

    # Instagram (longer, more atmospheric)
    parts.append("# Instagram caption\n")
    ig = (
        f"❄️ **{project_title}**\n\n"
        f"{_truncate(summary, 600)}\n\n"
        f"🎬 Watch the full video: {video_url}\n\n"
        f"Full story on the blog (link in bio).\n\n"
        f"{hashtag_str}\n"
    )
    parts.append("```")
    parts.append(ig)
    parts.append("```\n")

    # TikTok (hook-driven, max 300 chars)
    parts.append("# TikTok caption (≤ 300 chars)\n")
    tt = (
        f"\"NEVER go out after dark when it snows.\"\n\n"
        f"The yuki-onna is waiting. ❄️\n\n"
        f"Full story → {video_url}\n\n"
        f"{hashtag_str}"
    )
    parts.append("```")
    parts.append(_truncate(tt, 300))
    parts.append("```\n")

    # YouTube Shorts (if applicable, max 100 chars)
    parts.append("# YouTube Shorts title (≤ 100 chars)\n")
    short_title = _truncate(f"{project_title} | Red Sun Japan", 100)
    parts.append("```")
    parts.append(short_title)
    parts.append("```\n")

    return "\n".join(parts)


def generate_thumbnail_brief(
    project_title: str,
    project_slug: str,
    mood: str,
    bgm_attribution: str | None,
    blog_summary: str,
    audio_path: str,
    blog_path: str,
) -> str:
    """Build a designer brief for the thumbnail (1920x1080)."""
    parts: list[str] = []
    parts.append(f"# Thumbnail Brief — {project_title}\n")
    parts.append(f"**Project slug:** `{project_slug}`")
    parts.append(f"**Detected mood:** {mood}")
    parts.append("")
    parts.append("## Spec")
    parts.append("- **Resolution:** 1920×1080 (YouTube standard)")
    parts.append("- **Aspect:** 16:9")
    parts.append("- **Color space:** sRGB")
    parts.append("- **Format:** PNG or JPG, max 2MB")
    parts.append("")
    parts.append("## Composition (rule-of-thirds)")
    parts.append("- **Top-left third:** Subject face / character silhouette")
    parts.append("- **Bottom-right third:** Title text overlay")
    parts.append("- **Eye magnet:** high contrast zone, opposite the text")
    parts.append("")
    parts.append("## Suggested style")
    parts.append("- **Palette:** dark blue/white (cold) or red/black (dramatic), "
                  "depending on mood")
    parts.append("- **Font:** Bebas Neue, all caps, 80pt, white with red outline")
    parts.append("- **Text:** THE SNOW WOMAN (or a punchy 2-3 word hook)")
    parts.append("- **Accent:** brand red circle, bottom-right (1.5% of frame)")
    parts.append("")
    parts.append("## Story context (use to inform composition)")
    parts.append("```")
    parts.append(_truncate(blog_summary, 500))
    parts.append("```\n")
    parts.append("## Reference files (in Affinity)")
    parts.append(f"- Audio (for waveform / mood reference): `{audio_path}`")
    parts.append(f"- Blog article: `{blog_path}`\n")
    parts.append("## Brand assets")
    parts.append("- Red Sun Japan logo: see `branding/red-sun-logo-tate.png` (TBD)")
    parts.append("- Accent red: `#E60012`")
    parts.append("- Background navy: `#1a2a6c`")
    parts.append("")
    parts.append("## Don't")
    parts.append("- ❌ No clickbait faces or shocked expressions")
    parts.append("- ❌ No text smaller than 60pt (mobile legibility)")
    parts.append("- ❌ No busy backgrounds that fight the subject")
    return "\n".join(parts) + "\n"


def build_post_metadata(
    project_title: str,
    project_slug: str,
    lang: str,
    hashtags: list[str],
    chapters: list[dict],
    summary: str,
    video_path: str | None,
    audio_path: str | None,
    bgm_attribution: str | None,
    sources: list[str],
    blog_url: str,
    pinterest_url: str | None,
    image_credits: list[str],
) -> dict:
    """Build a consolidated post_metadata.json."""
    return {
        "title": project_title,
        "title_alternatives": [
            _truncate(f"{project_title} | Red Sun Japan", 100),
            _truncate(f"The {project_title.split(':')[0].strip()} — Red Sun Japan", 100),
        ],
        "slug": project_slug,
        "default_language": lang,
        "default_audio_language": lang,
        "privacy_status": "private",  # operator publishes manually
        "category_id": YOUTUBE_CATEGORY_EDUCATION,
        "tags": [h.lstrip("#") for h in hashtags],
        "hashtags": hashtags,
        "chapters": chapters,
        "description_excerpt": _truncate(summary, 500),
        "video_path": video_path,
        "audio_path": audio_path,
        "links": {
            "blog_url": blog_url,
            "pinterest_url": pinterest_url,
            "audio_attribution": "Qwen3-TTS CustomVoice (Apache-2.0)",
            "bgm_attribution": bgm_attribution,
            "image_credits": image_credits,
        },
        "sources": sources,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": "1.0",
    }


def generate_post_kit(
    *,
    project_id: int,
    project_title: str,
    project_slug: str,
    lang: str,
    script: str,
    blog_article: str,
    audio_path: str | None,
    video_path: str | None,
    bgm_attribution: str | None,
    blog_url: str | None,
    pinterest_url: str | None,
    image_credits: list[str] | None = None,
    output_video_duration_sec: float | None = None,
) -> PostKitPaths:
    """Generate the complete post kit for a project.

    Returns PostKitPaths with all written files. Raises FileNotFoundError
    if video_path doesn't exist (operator must render first).
    """
    if video_path and not Path(video_path).exists():
        raise FileNotFoundError(
            f"Video not found at {video_path} — render before generating post kit"
        )

    if image_credits is None:
        image_credits = []

    # 1) Parse frontmatter to get tags
    from blog_export import parse_frontmatter

    fm, _ = parse_frontmatter(blog_article or "")
    tags = fm.tags if fm.is_valid else []

    # 2) Build content pieces
    summary = _summarize_for_description(blog_article or "")
    hashtags = _gather_hashtags(project_slug, tags, project_title)
    mood = _detect_mood(project_slug, tags, project_title)

    # Estimate duration from the script length if not provided
    if output_video_duration_sec is None and script:
        # Rough heuristic: ~0.5s per char for narration
        output_video_duration_sec = len(script) * 0.5

    chapters = _extract_chapters(script or "", output_video_duration_sec or 0)

    # Extract sources from the blog
    sources: list[str] = []
    if blog_article:
        # Find "## Sources" section
        m = re.search(r"##\s+Sources.*?\n(.*?)(?=\n##|\Z)", blog_article, re.DOTALL)
        if m:
            for line in m.group(1).splitlines():
                line = line.strip()
                if line.startswith("- "):
                    sources.append(line[2:])

    # 3) Generate files
    kit_root = POST_KITS_DIR / f"{project_slug}.{lang}"
    kit_root.mkdir(parents=True, exist_ok=True)

    paths = PostKitPaths(
        root=kit_root,
        youtube_description=kit_root / "youtube_description.md",
        youtube_pinned_comment=kit_root / "youtube_pinned_comment.md",
        social_captions=kit_root / "social_captions.md",
        thumbnail_brief=kit_root / "thumbnail_brief.md",
        post_metadata=kit_root / "post_metadata.json",
    )

    paths.youtube_description.write_text(
        generate_youtube_description(
            project_title=project_title,
            project_slug=project_slug,
            summary=summary,
            hashtags=hashtags,
            chapters=chapters,
            sources=sources,
            blog_url=blog_url or f"https://redsunjapan.com/blog/{project_slug}",
            bgm_attribution=bgm_attribution,
        ),
        encoding="utf-8",
    )

    paths.youtube_pinned_comment.write_text(
        generate_pinned_comment(project_title, summary),
        encoding="utf-8",
    )

    paths.social_captions.write_text(
        generate_social_captions(
            project_title=project_title,
            summary=summary,
            hashtags=hashtags,
            video_url=blog_url or f"https://youtube.com/@redsunjapan/{project_slug}",
            slug=project_slug,
        ),
        encoding="utf-8",
    )

    blog_path = (
        f"blog_articles/{project_slug}.{lang}.md"
    )
    paths.thumbnail_brief.write_text(
        generate_thumbnail_brief(
            project_title=project_title,
            project_slug=project_slug,
            mood=mood,
            bgm_attribution=bgm_attribution,
            blog_summary=summary,
            audio_path=audio_path or "TBD",
            blog_path=blog_path,
        ),
        encoding="utf-8",
    )

    paths.post_metadata.write_text(
        json.dumps(
            build_post_metadata(
                project_title=project_title,
                project_slug=project_slug,
                lang=lang,
                hashtags=hashtags,
                chapters=chapters,
                summary=summary,
                video_path=video_path,
                audio_path=audio_path,
                bgm_attribution=bgm_attribution,
                sources=sources,
                blog_url=blog_url or f"https://redsunjapan.com/blog/{project_slug}",
                pinterest_url=pinterest_url,
                image_credits=image_credits,
            ),
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    logger.info("Generated post kit for project %s at %s", project_id, kit_root)
    return paths


# ─────────────────────────────────────────────────────────────────────────────
# Smoke test
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    import argparse

    parser = argparse.ArgumentParser(description="post_kit_generator smoke test")
    parser.add_argument(
        "--fixture", type=str, default="kappa",
        help="Catalog slug to use as the sample fixture.",
    )
    parser.add_argument(
        "--video-duration", type=float, default=85.9,
        help="Sample output video duration in seconds.",
    )
    args = parser.parse_args()

    # Build the sample from the catalog fixture so the smoke test
    # does not hardcode a specific theme.
    from redsun.themes import catalog

    entry = catalog.find(args.fixture) or {
        "name": args.fixture.replace("-", " ").title(),
        "slug": args.fixture,
    }

    title = f"{entry['name']}: A Smoke Test"
    slug = f"{entry['slug']}-smoke-test"
    sample_blog = f"""---
title: "{title}"
description: "Smoke test fixture for the post_kit_generator module."
pubDate: "2025-04-10"
tags: ["{entry['slug']}", "smoke-test"]
slug: "{slug}"
lang: "en"
---

# {title}

Smoke test fixture body.
"""

    sample_script = (
        f"This is a smoke-test script for {entry['name']}. The narrator "
        f"speaks slowly and clearly, testing the post kit pipeline end to end."
    )

    paths = generate_post_kit(
        project_id=99,
        project_title=title,
        project_slug=slug,
        lang="en",
        script=sample_script,
        blog_article=sample_blog,
        audio_path=f"/tmp/{entry['slug']}-smoke.wav",
        video_path=f"/tmp/{entry['slug']}-smoke.mp4",
        bgm_attribution="Public Domain, Wikimedia Commons",
        blog_url=f"https://redsunjapan.com/blog/{slug}",
        pinterest_url=None,
        image_credits=[],
        output_video_duration_sec=args.video_duration,
    )

    print("\n=== Generated files ===")
    for label, p in [
        ("youtube_description", paths.youtube_description),
        ("youtube_pinned_comment", paths.youtube_pinned_comment),
        ("social_captions", paths.social_captions),
        ("thumbnail_brief", paths.thumbnail_brief),
        ("post_metadata", paths.post_metadata),
    ]:
        print(f"  {label}: {p.name} ({p.stat().st_size} bytes)")
