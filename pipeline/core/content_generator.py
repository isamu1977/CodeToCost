"""Content generator service for Red Sun Japan.

Orchestrates generation of narration scripts and blog companion articles
using Claude Sonnet 5 via OpenRouter, strictly enforcing the mandatory
Content Attenuation & Dramatic Transposition (PG-13 Prestige Criteria).
"""
from __future__ import annotations

import logging
from typing import Iterable

from llm_client import (
    LLMConfig,
    LLMResult,
    generate_script_from_sources,
    generate_blog_article_from_script,
)

logger = logging.getLogger("redsun.content_generator")


def generate_attenuated_script(
    source_text_ja: str,
    *,
    topic: str = "Untitled topic",
    target_keywords: Iterable[str] | None = None,
    content_rating: str = "pg13_prestige",
    cfg: LLMConfig | None = None,
) -> LLMResult:
    """Generate spoken documentary script with mandatory content attenuation."""
    return generate_script_from_sources(
        source_text_ja,
        topic=topic,
        target_keywords=target_keywords,
        content_rating=content_rating,
        cfg=cfg,
    )


def generate_attenuated_article(
    english_script: str,
    *,
    topic: str = "Untitled topic",
    target_keywords: Iterable[str] | None = None,
    content_rating: str = "pg13_prestige",
    cfg: LLMConfig | None = None,
) -> LLMResult:
    """Generate Astro blog companion article with mandatory content attenuation."""
    return generate_blog_article_from_script(
        english_script,
        topic=topic,
        target_keywords=target_keywords,
        content_rating=content_rating,
        cfg=cfg,
    )
