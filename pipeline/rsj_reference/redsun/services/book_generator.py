"""Book Chapter Generator Service for Red Sun Japan.

Translates and expands the unabridged Japanese historical source text
(source_text_ja) into an annotated, immersive literary chapter formatted for
print publication on Amazon KDP.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional, Tuple

from database import SessionLocal, VideoProject
from llm_client import LLMConfig, _call_openrouter, _fill_user_template
from redsun.workspace import ProjectWorkspace, RUNTIME_ROOT

logger = logging.getLogger("redsun.book_generator")

PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"


def _resolve_project(slug_input: str, db) -> Tuple[Optional[VideoProject], str]:
    """Resolve project and canonical slug with support for fuzzy slug aliases.
    
    Supports:
    - Exact slug match (e.g. hoichi-the-earless-the-blind-biwa-priest-of-akamagaseki)
    - ID match if numeric (e.g. 37)
    - Fuzzy alias match (e.g. miminashi-hoichi-the-earless-musician -> hoichi-...)
    """
    slug_str = str(slug_input).strip()
    
    # 1. Exact slug match
    proj = db.query(VideoProject).filter(VideoProject.slug == slug_str).first()
    if proj:
        return proj, proj.slug
    
    # 2. Integer ID match
    if slug_str.isdigit():
        proj = db.query(VideoProject).filter(VideoProject.id == int(slug_str)).first()
        if proj:
            return proj, proj.slug or f"project-{proj.id}"

    # 3. Fuzzy keyword search across all projects
    all_projects = db.query(VideoProject).all()
    keywords = [w.lower() for w in slug_str.replace("-", " ").replace("_", " ").split() if len(w) > 3]
    
    best_proj = None
    best_score = 0
    for p in all_projects:
        cand_slug = (p.slug or "").lower()
        cand_title = (p.title or "").lower()
        score = sum(1 for kw in keywords if kw in cand_slug or kw in cand_title)
        if score > best_score:
            best_score = score
            best_proj = p

    if best_proj and best_score > 0:
        logger.info("Resolved alias '%s' to canonical project '%s' (ID %d)", slug_input, best_proj.slug, best_proj.id)
        return best_proj, best_proj.slug

    # 4. Check if directory exists directly in runtime
    direct_dir = Path(RUNTIME_ROOT) / slug_str
    if direct_dir.exists() and direct_dir.is_dir():
        return None, slug_str

    return None, slug_str


def get_book_dir(slug: str, project_id: Optional[int] = None) -> Path:
    """Return the absolute path to the project's book directory."""
    ws = ProjectWorkspace.from_slug(slug, project_id=project_id)
    return ws.book_dir


def generate_book_chapter(slug: str) -> str:
    """Generate an unabridged, annotated book chapter from source_text_ja.
    
    Args:
        slug: Project slug or alias.
        
    Returns:
        Absolute path to the saved chapter.md.
        
    Raises:
        ValueError: If project or source_text_ja is empty/missing.
        FileNotFoundError: If prompt templates cannot be found.
    """
    logger.info("Starting book chapter generation for project '%s'...", slug)
    db = SessionLocal()
    canonical_slug = slug
    project: Optional[VideoProject] = None

    try:
        project, canonical_slug = _resolve_project(slug, db)
        
        # Resolve source text
        source_text_ja = ""
        title_en = ""
        title_ja = ""
        theme_source_url = ""

        if project:
            source_text_ja = (project.source_text or "").strip()
            title_en = project.title or canonical_slug
            title_ja = project.title_ja or ""
            theme_source_url = project.theme_source_url or "Archival collection / Aozora Bunko"
        
        # Probe on disk for source.ja.txt if DB source text is empty
        ws = ProjectWorkspace.from_slug(canonical_slug, project_id=project.id if project else None)
        runtime_project_dir = ws.root
        source_disk_file = ws.text_dir / "source.ja.txt"
        if not source_text_ja and source_disk_file.exists():
            try:
                source_text_ja = source_disk_file.read_text(encoding="utf-8").strip()
                logger.info("Loaded source_text_ja from disk: %s (%d chars)", source_disk_file, len(source_text_ja))
            except Exception as exc:
                logger.warning("Failed to read source.ja.txt: %s", exc)

        if not source_text_ja:
            raise ValueError(
                f"Source Japanese text (source_text_ja) is empty for project '{slug}'. "
                "Ensure source text is populated in database or text/source.ja.txt before generating book chapter."
            )

        if not title_en:
            title_en = canonical_slug.replace("-", " ").title()

        # Load system and user prompts
        sys_prompt_path = PROMPTS_DIR / "book_chapter_system.md"
        user_prompt_path = PROMPTS_DIR / "book_chapter_user.md"

        if not sys_prompt_path.exists():
            raise FileNotFoundError(f"Missing prompt template: {sys_prompt_path}")
        if not user_prompt_path.exists():
            raise FileNotFoundError(f"Missing prompt template: {user_prompt_path}")

        system_prompt = sys_prompt_path.read_text(encoding="utf-8")
        user_template = user_prompt_path.read_text(encoding="utf-8")

        user_prompt = _fill_user_template(
            user_template,
            title_en=title_en,
            title_ja=title_ja,
            theme_source_url=theme_source_url,
            source_text_ja=source_text_ja,
        )

        cfg = LLMConfig.from_env()
        # Direct to Claude Sonnet 5
        cfg.model = cfg.model_literary
        logger.info(
            "Calling OpenRouter (%s) for literary book chapter generation (source_len=%d chars)...",
            cfg.model,
            len(source_text_ja),
        )

        llm_result = _call_openrouter(
            system_prompt,
            user_prompt,
            cfg=cfg,
            json_mode=False,
            temperature=0.6,
            max_tokens=8192,
        )

        chapter_content = llm_result.text.strip()
        if not chapter_content or len(chapter_content) < 500:
            raise RuntimeError(
                f"LLM returned insufficient book chapter content ({len(chapter_content)} chars)."
            )

        # Ensure target book directory exists
        book_dir = ws.book_dir
        book_dir.mkdir(parents=True, exist_ok=True)

        chapter_path = ws.book_chapter_path
        chapter_path.write_text(chapter_content, encoding="utf-8")
        logger.info(
            "Saved book chapter to %s (%d chars, %d words)",
            chapter_path,
            len(chapter_content),
            len(chapter_content.split()),
        )

        # Update database project flags
        if project:
            try:
                project.has_book_chapter = True
                db.commit()
                logger.info("Updated video_projects.has_book_chapter = True for project ID %d", project.id)
            except Exception as db_exc:
                logger.warning("Could not update has_book_chapter flag in DB: %s", db_exc)
                db.rollback()

        # Update project_info.json if present
        proj_info_path = runtime_project_dir / "project_info.json"
        if proj_info_path.exists():
            try:
                p_data = json.loads(proj_info_path.read_text(encoding="utf-8"))
                p_data["has_book_chapter"] = True
                p_data["book_chapter_updated_at"] = chapter_path.stat().st_mtime
                proj_info_path.write_text(json.dumps(p_data, indent=2, ensure_ascii=False), encoding="utf-8")
            except Exception as p_exc:
                logger.warning("Could not update project_info.json: %s", p_exc)

        # Also support alias symlink / secondary folder if slug differs
        if slug != canonical_slug:
            alias_dir = Path(RUNTIME_ROOT) / slug / "book"
            if alias_dir.exists() and alias_dir.is_dir() and not (alias_dir / "chapter.md").exists():
                try:
                    (alias_dir / "chapter.md").write_text(chapter_content, encoding="utf-8")
                except Exception:
                    pass

        return str(chapter_path.resolve())

    finally:
        db.close()
