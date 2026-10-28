"""Blog article export for the Red Sun Japan pipeline.

Phase 6 — turns a TranslationVariant's ``blog_article`` field into a
publishable Markdown file with validated frontmatter, and emits a
Pinterest-ready metadata JSON for cross-posting.

Output layout (gitignored by parent .gitignore):
  blog_articles/
    <slug>.<lang>.md              # the full article
  pinterest_queue/
    <slug>.<lang>.json            # pinterest metadata
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("redsun.blog")

ROOT = Path(__file__).parent
BLOG_DIR = ROOT / "blog_articles"
PINTEREST_DIR = ROOT / "pinterest_queue"

# Frontmatter schema we expect. The LLM generates these; we validate and
# can fill in missing defaults.
REQUIRED_FRONTMATTER_KEYS = {
    "title",
    "description",
    "pubDate",
    "tags",
    "slug",
    "lang",
}
OPTIONAL_FRONTMATTER_KEYS = {
    "heroImage",
    "target_keywords",
    "estimated_read_time",
    "canonical_url",
    "og_image",
}

# Pinterest board candidates — chosen by keyword match in title/tags.
# Operator can rename these boards in Pinterest UI without code change.
PINTEREST_BOARD_ALIASES = {
    "yokai": "Japanese Folklore",
    "kaidan": "Ghost Stories",
    "samurai": "Samurai & Bushido",
    "edo": "Edo Period",
    "meiji": "Meiji Restoration",
    "shogun": "Shogunate",
    "battle": "Famous Battles",
    "legend": "Legends & Myths",
    "snow": "Winter in Japan",
    "mountain": "Mountain Folk",
}


@dataclass
class Frontmatter:
    """Parsed + validated blog frontmatter."""

    raw: dict[str, Any]
    is_valid: bool
    missing: list[str]
    errors: list[str]

    @property
    def title(self) -> str:
        return str(self.raw.get("title", ""))

    @property
    def slug(self) -> str:
        return str(self.raw.get("slug", ""))

    @property
    def lang(self) -> str:
        return str(self.raw.get("lang", "en"))

    @property
    def tags(self) -> list[str]:
        tags = self.raw.get("tags", [])
        if isinstance(tags, list):
            return [str(t) for t in tags]
        return []

    @property
    def pubDate(self) -> str:
        return str(self.raw.get("pubDate", ""))


def parse_frontmatter(blog_md: str) -> tuple[Frontmatter, str]:
    """Parse the YAML frontmatter from a blog markdown string.

    Returns (Frontmatter, body_markdown). Body is everything after the
    closing `---`. If the frontmatter is missing/invalid, returns
    is_valid=False and a body that includes the original text.
    """
    if not blog_md.startswith("---"):
        return (
            Frontmatter(raw={}, is_valid=False, missing=list(REQUIRED_FRONTMATTER_KEYS),
                        errors=["missing opening '---'"]),
            blog_md,
        )

    # Split on the closing fence
    parts = blog_md.split("---", 2)
    if len(parts) < 3:
        return (
            Frontmatter(raw={}, is_valid=False, missing=list(REQUIRED_FRONTMATTER_KEYS),
                        errors=["missing closing '---'"]),
            blog_md,
        )

    _, fm_text, body = parts
    body = body.lstrip("\n")

    try:
        raw = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError as exc:
        return (
            Frontmatter(raw={}, is_valid=False, missing=[],
                        errors=[f"YAML parse error: {exc}"]),
            body,
        )

    if not isinstance(raw, dict):
        return (
            Frontmatter(raw={}, is_valid=False, missing=[],
                        errors=[f"frontmatter must be a mapping, got {type(raw).__name__}"]),
            body,
        )

    missing = [k for k in REQUIRED_FRONTMATTER_KEYS if k not in raw]
    errors: list[str] = []

    # Field-level validation
    if "title" in raw and (not isinstance(raw["title"], str) or not raw["title"].strip()):
        errors.append("title must be a non-empty string")
    if "description" in raw and (not isinstance(raw["description"], str) or len(raw["description"]) < 50):
        errors.append("description must be 50+ chars (SEO best practice)")
    if "slug" in raw and not re.match(r"^[a-z0-9-]+$", str(raw["slug"])):
        errors.append("slug must be lowercase letters, digits, and hyphens only")
    if "tags" in raw and not isinstance(raw["tags"], list):
        errors.append("tags must be a list")

    return (
        Frontmatter(
            raw=raw,
            is_valid=not missing and not errors,
            missing=missing,
            errors=errors,
        ),
        body,
    )


def fill_default_frontmatter(fm: Frontmatter, *, slug: str | None = None,
                              lang: str | None = None) -> dict[str, Any]:
    """Return the frontmatter dict with missing keys filled in by safe defaults.

    Never overwrites a value that's already in the LLM output — only fills gaps.
    """
    out = dict(fm.raw)

    if "title" not in out and slug:
        out["title"] = slug.replace("-", " ").title()
    if "description" not in out:
        out["description"] = "An article from Red Sun Japan exploring Japanese history and folklore."
    if "pubDate" not in out:
        out["pubDate"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if "heroImage" not in out:
        out["heroImage"] = ""
    if "tags" not in out:
        out["tags"] = []
    if "slug" not in out and slug:
        out["slug"] = slug
    if "lang" not in out and lang:
        out["lang"] = lang
    if "estimated_read_time" not in out:
        out["estimated_read_time"] = "5 min"

    return out


def write_blog_article(blog_md: str, *, slug: str, lang: str = "en",
                       overwrite: bool = False) -> Path:
    """Write the blog article to ``blog_articles/<slug>.<lang>.md``.

    Validates and re-serializes the frontmatter (round-trip via yaml.dump)
    to catch any structural issues before they hit the file system.

    Returns the absolute path of the written file.
    """
    fm, body = parse_frontmatter(blog_md)

    # Fill defaults so the file is always loadable
    filled = fill_default_frontmatter(fm, slug=slug, lang=lang)
    is_valid = fm.is_valid  # before filling

    fm_text = yaml.dump(
        filled,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
        width=120,
    )

    out_path = BLOG_DIR / f"{slug}.{lang}.md"
    if out_path.exists() and not overwrite:
        logger.info("Blog %s already exists; not overwriting", out_path.name)
        return out_path
    BLOG_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_text(f"---\n{fm_text}---\n\n{body}", encoding="utf-8")
    logger.info("Wrote blog article: %s (frontmatter_valid=%s)", out_path, is_valid)
    return out_path


def build_pinterest_metadata(fm: Frontmatter, *, body_excerpt: str = "",
                              destination_url: str = "") -> dict[str, Any]:
    """Build a Pinterest-pin-ready JSON metadata dict.

    Pinterest rules (2024):
    - Title: max 100 chars (we target 60-90)
    - Description: max 500 chars (we target 200-400)
    - Image URL: required for actual pin (placeholder for now)
    - Destination URL: required (link to blog post)
    - Board: optional, falls back to "Red Sun Japan" if no match
    """
    title = fm.title[:100] if fm.title else "Red Sun Japan article"

    # Build a clean description from the FM description
    description = fm.raw.get("description", "")
    if body_excerpt and not description:
        # Take first 400 chars of body
        description = re.sub(r"\s+", " ", body_excerpt).strip()[:400]
    if not description:
        description = "An article from Red Sun Japan."

    description = description[:500]

    # Guess a board from tags / title
    board = "Red Sun Japan"
    text_to_check = (fm.title + " " + " ".join(fm.tags)).lower()
    for keyword, board_name in PINTEREST_BOARD_ALIASES.items():
        if keyword in text_to_check:
            board = board_name
            break

    return {
        "title": title,
        "description": description,
        "board": board,
        "destination_url": destination_url or f"https://redsunjapan.com/blog/{fm.slug}",
        "image_url": fm.raw.get("heroImage", ""),  # operator fills later
        "tags": fm.tags,
        "lang": fm.lang,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def write_pinterest_queue(fm: Frontmatter, body_excerpt: str = "",
                           destination_url: str = "") -> Path:
    """Write a Pinterest metadata JSON to ``pinterest_queue/<slug>.<lang>.json``.

    Returns the path. Operator reviews the JSON, fills in the image_url,
    then manually publishes via Pinterest UI/API.
    """
    metadata = build_pinterest_metadata(fm, body_excerpt=body_excerpt,
                                         destination_url=destination_url)
    out_path = PINTEREST_DIR / f"{fm.slug}.{fm.lang}.json"
    PINTEREST_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info("Wrote Pinterest queue: %s (board=%s)", out_path, metadata["board"])
    return out_path


# ─────────────────────────────────────────────────────────────────────────────
# Smoke test
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    import argparse

    parser = argparse.ArgumentParser(description="blog_export smoke test")
    parser.add_argument(
        "--fixture", type=str, default="kappa",
        help="Catalog slug to use as the sample blog fixture.",
    )
    args = parser.parse_args()

    # Sample blog markdown is generated from the catalog fixture
    # so the smoke test does not hardcode a specific theme.
    from redsun.themes import catalog

    entry = catalog.find(args.fixture) or {
        "name": args.fixture.replace("-", " ").title(),
        "slug": args.fixture,
        "category": "yokai",
        "description": f"Smoke test fixture for {args.fixture}.",
    }

    sample = f"""---
title: "{entry['name']}: A Smoke Test"
description: "{entry['description']}"
pubDate: "2025-04-10"
heroImage: ""
tags: ["{entry['slug']}", "japanese folklore", "{entry['category']}"]
slug: "{entry['slug']}-smoke-test"
lang: "en"
target_keywords: ["{entry['slug']}"]
estimated_read_time: "7 min"
---

# {entry['name']}: A Smoke Test

Smoke test fixture for the blog_export module.

## Sources & Further Reading

- Public domain
"""

    print("=== Test: parse_frontmatter ===")
    fm, body = parse_frontmatter(sample)
    print(f"  is_valid: {fm.is_valid}")
    print(f"  missing: {fm.missing}")
    print(f"  errors: {fm.errors}")
    print(f"  title: {fm.title!r}")
    print(f"  tags: {fm.tags}")

    print("\n=== Test: write_blog_article ===")
    path = write_blog_article(sample, slug=entry['slug'] + "-smoke-test",
                                lang="en", overwrite=True)
    print(f"  Wrote: {path}")
    print(f"  Size: {path.stat().st_size} bytes")

    print("\n=== Test: build_pinterest_metadata ===")
    excerpt = body[:400]
    pin = build_pinterest_metadata(fm, body_excerpt=excerpt,
                                     destination_url="https://redsunjapan.com/blog/" + entry['slug'])
    print(f"  title: {pin['title']!r} ({len(pin['title'])} chars)")
    print(f"  description: {pin['description']!r} ({len(pin['description'])} chars)")
    print(f"  board: {pin['board']!r}")

    print("\n=== Test: write_pinterest_queue ===")
    pin_path = write_pinterest_queue(fm, body_excerpt=excerpt)
    print(f"  Wrote: {pin_path}")
    print(f"  Size: {pin_path.stat().st_size} bytes")
