"""Canonical brand social links & official shop configuration loader for Red Sun Japan.

Decouples all official brand links (Printify shop, blog, social media) from Python code.
Reads `config/social_links.json` with optional environment variable overrides.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("redsun.social_links")

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "social_links.json"


def get_social_links_config_path() -> Path:
    """Resolve the canonical path to config/social_links.json."""
    env_path = os.environ.get("SOCIAL_LINKS_CONFIG_PATH")
    if env_path:
        p = Path(env_path)
        if p.exists():
            return p
    if DEFAULT_CONFIG_PATH.exists():
        return DEFAULT_CONFIG_PATH
    # Fallback search from CWD
    cwd_candidate = Path.cwd() / "config" / "social_links.json"
    if cwd_candidate.exists():
        return cwd_candidate
    return DEFAULT_CONFIG_PATH


def load_social_links(config_path: Optional[Path | str] = None) -> dict[str, dict[str, str]]:
    """Load social and shop links from JSON configuration with optional environment variable overrides.
    
    Precedence:
    1. Environment variables (e.g. `SOCIAL_SHOP_PRINTIFY_URL`, `SHOP_PRINTIFY_URL`, etc.)
    2. JSON configuration (`config/social_links.json`)
    """
    resolved_path = Path(config_path) if config_path else get_social_links_config_path()
    links: dict[str, dict[str, str]] = {}

    if resolved_path.exists():
        try:
            raw = json.loads(resolved_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                links = raw
        except Exception as exc:
            logger.error("Failed to load social links from %s: %s", resolved_path, exc)
    else:
        logger.warning("Social links config file not found at %s. Using environment fallbacks.", resolved_path)

    # Apply environment variable overrides
    # e.g. SOCIAL_SHOP_PRINTIFY_URL or SHOP_PRINTIFY_URL for key 'shop_printify'
    for key, item in list(links.items()):
        if not isinstance(item, dict):
            continue
        key_upper = key.upper()
        # URL overrides
        env_url = (
            os.environ.get(f"SOCIAL_{key_upper}_URL")
            or os.environ.get(f"{key_upper}_URL")
        )
        if env_url and env_url.strip():
            item["url"] = env_url.strip()

        # Label overrides
        env_label = (
            os.environ.get(f"SOCIAL_{key_upper}_LABEL")
            or os.environ.get(f"{key_upper}_LABEL")
        )
        if env_label and env_label.strip():
            item["label"] = env_label.strip()

    # Check for keys defined exclusively in environment variables
    # e.g. SOCIAL_TIKTOK_URL
    for env_k, env_v in os.environ.items():
        if env_k.startswith("SOCIAL_") and env_k.endswith("_URL") and env_v.strip():
            key_name = env_k[len("SOCIAL_") : -len("_URL")].lower()
            if key_name not in links:
                links[key_name] = {
                    "label": key_name.replace("_", " ").title(),
                    "url": env_v.strip(),
                }

    return links


def format_social_links_block(
    slug: Optional[str] = None,
    links: Optional[dict[str, dict[str, str]]] = None,
    include_header: bool = True,
) -> str:
    """Format social and shop links into a clean, markdown bulleted block for video descriptions.
    
    If `slug` is provided, the website link points to the specific post canonical URL.
    """
    if links is None:
        links = load_social_links()

    lines: list[str] = []
    if include_header:
        lines.append("Official Links & Art Shop:")

    # Preferred presentation order
    ordered_keys = ["kofi", "shop_printify", "website", "instagram", "pinterest", "twitter_x"]
    remaining_keys = [k for k in links.keys() if k not in ordered_keys]
    all_keys = [k for k in ordered_keys if k in links] + remaining_keys

    for k in all_keys:
        info = links.get(k)
        if not info or not isinstance(info, dict):
            continue
        label = info.get("label", k.title())
        url = info.get("url", "").strip()
        if not url:
            continue

        # Specialize website URL if project slug is present
        if k == "website" and slug:
            clean_base = url.rstrip("/")
            if not clean_base.endswith("/posts") and not clean_base.endswith(f"/posts/{slug}"):
                url = f"{clean_base}/posts/{slug}"

        lines.append(f"- {label}: {url}")

    return "\n".join(lines)


def inject_social_links_into_description(
    description: str,
    slug: Optional[str] = None,
    links: Optional[dict[str, dict[str, str]]] = None,
) -> str:
    """Deterministically inject the official links block into a YouTube description if not present.
    
    Places the links block directly before the Soundtrack Attribution or before the hashtags block.
    """
    if links is None:
        links = load_social_links()

    shop_url = links.get("shop_printify", {}).get("url", "").strip()
    if (
        (shop_url and shop_url in description)
        or "printify.me" in description
        or "Official Links & Art Shop" in description
        or "Official Art Prints & Puzzles" in description
    ):
        # Already present, no duplication
        return description

    block = format_social_links_block(slug=slug, links=links)
    if not block:
        return description

    formatted_block = f"\n\n{block}"

    # Target 1: right above Soundtrack attribution if present
    if "Soundtrack & Audio Attribution" in description:
        idx = description.find("Soundtrack & Audio Attribution")
        return description[:idx].rstrip() + formatted_block + "\n\n" + description[idx:].lstrip()

    # Target 2: right above hashtag block if present
    if "\n#" in description:
        idx = description.rfind("\n#")
        return description[:idx].rstrip() + formatted_block + "\n\n" + description[idx:].lstrip()

    # Fallback: append at the end
    return description.rstrip() + formatted_block


def get_blog_post_url(slug: Optional[str] = None, links: Optional[dict[str, dict[str, str]]] = None) -> str:
    """Return canonical blog post URL derived from parameterized website link."""
    if links is None:
        links = load_social_links()
    base_url = links.get("website", {}).get("url", "https://redsunjapan.com").strip().rstrip("/")
    if slug:
        return f"{base_url}/posts/{slug}"
    return base_url
