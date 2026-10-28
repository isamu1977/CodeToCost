"""Script and narration service for Red Sun Japan.

Wraps script generation with Claude Sonnet 5 via OpenRouter, compiling
prestige documentary content attenuation guidelines (PG-13 prestige criteria).
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

logger = logging.getLogger("redsun.script_service")


class ScriptService:
    """Service for generating attenuated documentary scripts and companion articles."""

    @staticmethod
    def generate_script(
        source_text_ja: str,
        *,
        topic: str = "Untitled topic",
        target_keywords: Iterable[str] | None = None,
        content_rating: str = "pg13_prestige",
        cfg: LLMConfig | None = None,
    ) -> LLMResult:
        return generate_script_from_sources(
            source_text_ja,
            topic=topic,
            target_keywords=target_keywords,
            content_rating=content_rating,
            cfg=cfg,
        )

    @staticmethod
    def generate_article(
        english_script: str,
        *,
        topic: str = "Untitled topic",
        target_keywords: Iterable[str] | None = None,
        content_rating: str = "pg13_prestige",
        cfg: LLMConfig | None = None,
    ) -> LLMResult:
        return generate_blog_article_from_script(
            english_script,
            topic=topic,
            target_keywords=target_keywords,
            content_rating=content_rating,
            cfg=cfg,
        )
