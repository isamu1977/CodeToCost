"""Blog Article Generator and Quality Orchestration Service.

Generates full-length companion articles from narration scripts, validates
them via BlogQualityLinter, auto-corrects any violations with Claude Sonnet 5,
and enriches the article with curated episode media via BlogMediaCurator.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from redsun.services.blog_quality_linter import BlogQualityLinter
from redsun.services.blog_media_curator import BlogMediaCurator
from llm_client import generate_blog_article_from_script

logger = logging.getLogger("redsun.blog_generator")


class BlogArticleGenerator:
    """Orchestrates generation, linting, media injection, and export of blog articles."""

    def __init__(self) -> None:
        self.linter = BlogQualityLinter()
        self.media_curator = BlogMediaCurator()

    def generate_and_curate(
        self,
        slug: str,
        title: str,
        script: str,
        existing_draft: str | None = None,
        force_regenerate: bool = False,
    ) -> str:
        """Produce a publication-ready blog article with validated frontmatter and media."""
        article_text = (existing_draft or "").strip()

        # Step 1: Generate via LLM if draft is empty, too short, or forced
        needs_generation = force_regenerate or not article_text or len(article_text) < 500
        if not needs_generation:
            # Check if existing draft is corrupt (e.g. truncated or lacking headings)
            audit_res = self.linter.audit(article_text)
            if not audit_res["valid"]:
                logger.warning(
                    "Existing blog article for '%s' failed initial QA (%d issues): %s. Re-generating/correcting...",
                    slug,
                    len(audit_res["issues"]),
                    audit_res["issues"],
                )
                needs_generation = True

        if needs_generation:
            logger.info("Generating full companion blog article for '%s' via LLM...", slug)
            res = generate_blog_article_from_script(
                english_script=script,
                topic=title,
                target_keywords=["kaidan", "japanese folklore", "edo period", slug.replace("-", " ")],
            )
            raw_gen = res.text.strip()
            if raw_gen.startswith("```markdown"):
                raw_gen = raw_gen[len("```markdown"):].strip()
            elif raw_gen.startswith("```"):
                raw_gen = raw_gen[3:].strip()
            if raw_gen.endswith("```"):
                raw_gen = raw_gen[:-3].strip()
            article_text = raw_gen

        # Step 2: Validate with BlogQualityLinter
        is_valid, issues = self.linter.validate(article_text)
        if not is_valid:
            logger.warning(
                "Blog article for '%s' failed validation with %d issues. Entering auto-correction loop...",
                slug,
                len(issues),
            )
            article_text = self.linter.run_correction_loop(
                markdown_text=article_text,
                script=script,
                topic=title,
                max_attempts=2,
            )

        # Step 3: Process assets and inject media
        logger.info("Curating and injecting media assets for '%s'...", slug)
        media_info = self.media_curator.curate_and_deploy_media(slug, num_section_images=4)
        curated_article = self.media_curator.inject_media_into_markdown(slug, article_text, media_info)

        return curated_article
