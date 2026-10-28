"""Render and Post-Processing Service for Red Sun Japan Pipeline.

Coordinates post-render steps including Step #14 (Blog Article Generation,
Editorial Quality Linting, Media Curation, and Astro Deployment).
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from database import SessionLocal, VideoProject, TranslationVariant, VideoStatus
from redsun.services.blog_article_generator import BlogArticleGenerator
from redsun.services.blog_quality_linter import BlogQualityLinter
from redsun.services.blog_media_curator import BlogMediaCurator
from redsun.services.remotion_runner import ACTIVE_RENDER_PROCESSES, cancel_remotion_render

logger = logging.getLogger("redsun.render_service")


def get_astro_blog_root() -> Path:
    """Find the redsunjapan-blog directory by ascending parent hierarchy."""
    curr = Path(__file__).resolve()
    for parent in curr.parents:
        cand = parent / "redsunjapan-blog"
        if cand.is_dir() and (cand / "src" / "content").exists():
            return cand
    return Path("/Users/isamumatsuyama/Documents/online-projects/red-sun-japan/red-sun-project-files/redsunjapan-blog")


DEFAULT_ASTRO_BLOG_COLLECTION = get_astro_blog_root() / "src" / "content" / "blog"


class RenderService:
    """Orchestrates video rendering and post-processing deliverables."""

    @classmethod
    def execute_step_14_blog(
        cls,
        slug: str,
        title: str,
        script: str,
        workspace_root: Path | None = None,
        force_regenerate: bool = False,
    ) -> Path:
        """Execute Step #14: Blog Generation, QA Linter, Media Curation, and Astro Deploy.

        1. Dispara geração/revisão via BlogArticleGenerator.
        2. Valida com BlogQualityLinter (com auto-correção se necessário).
        3. Processa assets com BlogMediaCurator.
        4. Salva Markdown final em redsunjapan-blog/src/content/blog/{slug}.md.
        5. Atualiza o status do passo #14 no DB e logs do pipeline.
        """
        logger.info("Executing Step #14 for '%s' (title: '%s')...", slug, title)

        existing_draft = None
        if not force_regenerate:
            db = SessionLocal()
            try:
                p = db.query(VideoProject).filter(VideoProject.slug == slug).first()
                if p:
                    v = db.query(TranslationVariant).filter(
                        TranslationVariant.project_id == p.id,
                        TranslationVariant.language_code == "en",
                    ).first()
                    if v and v.blog_article and len(v.blog_article) > 500:
                        existing_draft = v.blog_article
            finally:
                db.close()

            if not existing_draft and workspace_root:
                local_article = workspace_root / "text" / "blog_article.md"
                if local_article.exists():
                    existing_draft = local_article.read_text(encoding="utf-8")

        generator = BlogArticleGenerator()
        final_markdown = generator.generate_and_curate(
            slug=slug,
            title=title,
            script=script,
            existing_draft=existing_draft,
            force_regenerate=force_regenerate,
        )

        # Local workspace persistence
        if workspace_root:
            text_dir = workspace_root / "text"
            text_dir.mkdir(parents=True, exist_ok=True)
            (text_dir / "blog_article.md").write_text(final_markdown, encoding="utf-8")

            blog_dir = workspace_root / "blog"
            blog_dir.mkdir(parents=True, exist_ok=True)
            (blog_dir / f"{slug}.md").write_text(final_markdown, encoding="utf-8")

        # Deploy directly to Astro content collection
        astro_blog_dir = DEFAULT_ASTRO_BLOG_COLLECTION
        astro_blog_dir.mkdir(parents=True, exist_ok=True)
        dest_astro_file = astro_blog_dir / f"{slug}.md"
        dest_astro_file.write_text(final_markdown, encoding="utf-8")
        logger.info("Step #14: Saved final blog post to %s", dest_astro_file)

        # Update Database
        db = SessionLocal()
        try:
            p = db.query(VideoProject).filter(VideoProject.slug == slug).first()
            if p:
                v = db.query(TranslationVariant).filter(
                    TranslationVariant.project_id == p.id,
                    TranslationVariant.language_code == "en",
                ).first()
                if v:
                    v.blog_article = final_markdown
                    db.commit()
                    logger.info("Step #14: Updated TranslationVariant.blog_article in DB for project %s", p.id)
        except Exception as exc:
            logger.error("Step #14: Failed to update DB variant: %s", exc)
        finally:
            db.close()

        return dest_astro_file

    @classmethod
    def execute_step_16_distribution(
        cls,
        slug: str,
        workspace_root: Path | None = None,
        force_regenerate: bool = False,
    ) -> dict[str, Any]:
        """Execute Step #16: Universal Distribution Kit Generation.

        Generates YouTube kit, Pinterest pins & media, Shorts captions/hashtags,
        and X/Twitter thread.
        """
        logger.info("Executing Step #16 Universal Distribution Kit for '%s'...", slug)
        from redsun.services.distribution_kit_generator import generate_distribution_kit

        runtime_root = workspace_root.parent if workspace_root else None
        kit = generate_distribution_kit(
            project_id=slug,
            force_regenerate=force_regenerate,
            runtime_root=runtime_root,
        )

        # Update Project Status in DB
        db = SessionLocal()
        try:
            p = db.query(VideoProject).filter(VideoProject.slug == slug).first()
            if p:
                p.status = VideoStatus.RENDERED
                db.commit()
                logger.info("Step #16: Updated project %s status to %s", p.id, p.status)
        except Exception as exc:
            logger.error("Step #16: Failed to update DB status: %s", exc)
        finally:
            db.close()

        return kit

    @classmethod
    def cancel_render(cls, slug: str) -> dict[str, Any]:
        """Cancel running Remotion render and clean up temporary assets."""
        return cancel_remotion_render(slug)
