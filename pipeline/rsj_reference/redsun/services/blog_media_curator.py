"""Curator and Injector for Blog Media Assets.

Selects rendered high-impact scene shots, copies them to Astro public assets,
updates frontmatter hero/og images, and anchors section images with atmospheric captions
beneath Markdown H2 headers.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any

logger = logging.getLogger("redsun.blog_media_curator")

DEFAULT_RUNTIME_ROOT = Path("/Users/isamumatsuyama/Documents/red-sun-japan-runtime/projects")


def get_astro_blog_root() -> Path:
    """Find the redsunjapan-blog directory by ascending parent hierarchy."""
    curr = Path(__file__).resolve()
    for parent in curr.parents:
        cand = parent / "redsunjapan-blog"
        if cand.is_dir() and (cand / "src" / "content").exists():
            return cand
    return Path("/Users/isamumatsuyama/Documents/online-projects/red-sun-japan/red-sun-project-files/redsunjapan-blog")


DEFAULT_ASTRO_PUBLIC_ROOT = get_astro_blog_root() / "public"


def format_editorial_caption(task_title: str, scene_description: str = "") -> str:
    """
    Gera uma legenda limpa, editorial e concisa para o Markdown do blog.
    Nunca usa prompts técnicos brutos ou frases cortadas com reticências.
    """
    # 1. Se o título da task existir e for descritivo, priorize-o
    caption = (task_title or "").strip()

    # Remove identificadores como "Shot 01: ", "scene-012 — ", "Ref-001: "
    caption = re.sub(r'^(shot|scene|ref)[-_ \t]*\d+\s*[:—\-]\s*', '', caption, flags=re.IGNORECASE)

    # Remove colchetes de enquadramento técnicos [Close-up], [Wide shot], etc.
    caption = re.sub(r'\[.*?\]\s*', '', caption).strip()

    # Remove jargões de enquadramento se sobrarem
    caption = re.sub(r'^\[?(wide shot|medium shot|close-up|detail shot|establishing shot)\]?\s*(of\s*)?', '', caption, flags=re.IGNORECASE).strip()

    # 2. Se a legenda for curta ou genérica demais, refine a primeira oração da cena
    if len(caption) < 15 and scene_description:
        # Pega apenas a primeira frase limpa (até o primeiro ponto final)
        first_sentence = scene_description.split('.')[0].strip()
        # Remove tags entre colchetes
        first_sentence = re.sub(r'\[.*?\]\s*', '', first_sentence).strip()
        # Remove jargões de enquadramento
        first_sentence = re.sub(r'^\[?(wide shot|medium shot|close-up|detail shot|establishing shot)\]?\s*(of\s*)?', '', first_sentence, flags=re.IGNORECASE).strip()
        first_sentence = re.sub(r'^(wide shot|medium shot|close-up|detail shot|establishing shot)\s*(of\s*)?', '', first_sentence, flags=re.IGNORECASE).strip()
        first_sentence = re.sub(r'^focusing on (the )?', '', first_sentence, flags=re.IGNORECASE).strip()
        # Evita frases excessivamente longas cortando na última palavra sem quebrar
        if len(first_sentence) > 120:
            first_sentence = first_sentence[:120].rsplit(' ', 1)[0]
        if first_sentence:
            caption = first_sentence

    # 3. Capitalização e pontuação final rigorosa
    caption = caption.strip()
    if caption:
        caption = caption[0].upper() + caption[1:]

    # Remove reticências soltas ou pontuação repetida no final
    caption = re.sub(r'[\.\s,;—\-]+$', '', caption)
    if not caption:
        return ""
    return f"{caption}."


def format_editorial_alt(task_title: str, caption: str = "", heading_title: str = "") -> str:
    """
    Gera um texto alternativo (alt) limpo, descritivo e conciso para acessibilidade web.
    """
    if caption:
        alt = re.sub(r'[\.\s,;—\-]+$', '', caption).strip()
        if len(alt) >= 5:
            return alt

    if task_title:
        alt = re.sub(r'^(shot|scene|ref)[-_ \t]*\d+\s*[:—\-]\s*', '', task_title, flags=re.IGNORECASE).strip()
        alt = re.sub(r'\[.*?\]\s*', '', alt).strip()
        if alt:
            alt = alt[0].upper() + alt[1:]
            return alt

    return f"Scene illustrating {heading_title}" if heading_title else "Scene from Red Sun Japan chronicle"


class BlogMediaCurator:
    """Manages physical asset deployment and markdown image anchoring for blog posts."""

    def __init__(
        self,
        runtime_root: Path | None = None,
        astro_public_root: Path | None = None,
    ) -> None:
        self.runtime_root = runtime_root or DEFAULT_RUNTIME_ROOT
        self.astro_public_root = astro_public_root or DEFAULT_ASTRO_PUBLIC_ROOT

    def get_project_dir(self, slug: str) -> Path:
        from redsun.workspace import ProjectWorkspace
        return ProjectWorkspace.from_slug(slug, projects_root=self.runtime_root).root

    def find_rendered_shots(self, slug: str) -> list[Path]:
        """Find and numerically sort all rendered shot images for a project."""
        project_dir = self.get_project_dir(slug)
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

        return sorted(shots, key=_sort_key)

    def load_shot_metadata(self, slug: str) -> dict[str, dict[str, Any]]:
        """Load shot prompt and scene action descriptions from gemini-assistant project.json."""
        project_dir = self.get_project_dir(slug)
        project_json_path = project_dir / "gemini-assistant" / "project.json"
        meta: dict[str, dict[str, Any]] = {}
        if not project_json_path.exists():
            return meta

        try:
            data = json.loads(project_json_path.read_text(encoding="utf-8"))
            tasks = data.get("tasks", [])
            for t in tasks:
                tid = t.get("id", "")
                if tid:
                    meta[tid] = t
                    # Also map shot_001 -> shot-001
                    normalized_id = tid.replace("-", "_")
                    meta[normalized_id] = t
        except Exception as exc:
            logger.warning("Failed to load shot metadata from %s: %s", project_json_path, exc)

        return meta

    def derive_caption(
        self,
        shot_path: Path,
        shot_meta: dict[str, Any] | None,
        heading_title: str = "",
    ) -> tuple[str, str]:
        """Derive an evocative and clean (alt_text, caption) from metadata and context."""
        task_title = ""
        scene_description = ""
        if shot_meta:
            task_title = shot_meta.get("title", "") or ""
            scene_description = (
                shot_meta.get("prompt", "")
                or shot_meta.get("description", "")
                or shot_meta.get("action", "")
                or ""
            )

        caption = format_editorial_caption(task_title, scene_description)
        if not caption:
            caption = f"Atmospheric chronicle of {heading_title or 'feudal Japanese folklore'}."

        alt_text = format_editorial_alt(task_title, caption, heading_title)
        return alt_text, caption

    def curate_and_deploy_media(
        self,
        slug: str,
        num_section_images: int = 4,
    ) -> dict[str, Any]:
        """Copy hero and distributed section images to Astro public posts folder.

        Returns metadata on deployed files.
        """
        dest_dir = self.astro_public_root / "images" / "posts" / slug
        dest_dir.mkdir(parents=True, exist_ok=True)

        shots = self.find_rendered_shots(slug)
        if not shots:
            logger.warning("No rendered shots found for slug '%s' in %s", slug, self.runtime_root / slug)
            return {"hero": None, "sections": []}

        # 1. Hero Image: shot_001 or first shot
        hero_src = shots[0]
        hero_dest = dest_dir / "hero.jpeg"
        shutil.copy2(hero_src, hero_dest)
        logger.info("Deployed hero image for '%s': %s -> %s", slug, hero_src.name, hero_dest)

        # 2. Section Images: 3 to 4 distributed across timeline
        remaining_shots = shots[1:] if len(shots) > 1 else shots
        n_rem = len(remaining_shots)

        section_shots: list[Path] = []
        if n_rem <= num_section_images:
            section_shots = remaining_shots
        else:
            # Distribute evenly across timeline (e.g. N/4, 2N/4, 3N/4, etc.)
            indices: list[int] = []
            for i in range(1, num_section_images + 1):
                idx = min(int((i * n_rem) / (num_section_images + 1)), n_rem - 1)
                if idx not in indices:
                    indices.append(idx)
            # If we need more, append from evenly spaced or end
            for idx in indices:
                section_shots.append(remaining_shots[idx])

        deployed_sections: list[dict[str, Any]] = []
        for s in section_shots:
            dest_file = dest_dir / s.name
            shutil.copy2(s, dest_file)
            deployed_sections.append({
                "source_name": s.name,
                "dest_path": f"/images/posts/{slug}/{s.name}",
                "local_file": dest_file,
            })
            logger.info("Deployed section image for '%s': %s", slug, s.name)

        return {
            "hero_path": f"/images/posts/{slug}/hero.jpeg",
            "sections": deployed_sections,
            "dest_dir": dest_dir,
        }

    def inject_media_into_markdown(
        self,
        slug: str,
        markdown_text: str,
        media_info: dict[str, Any] | None = None,
    ) -> str:
        """Inject heroImage/ogImage into frontmatter and anchor images beneath H2 headings."""
        if not media_info:
            media_info = self.curate_and_deploy_media(slug)

        hero_path = media_info.get("hero_path") or f"/images/posts/{slug}/hero.jpeg"
        sections = media_info.get("sections", [])
        shot_metadata = self.load_shot_metadata(slug)

        # 1. Update Frontmatter
        lines = markdown_text.splitlines()
        in_fm = False
        fm_end_idx = -1
        updated_lines: list[str] = []

        has_hero = False
        has_og = False

        for i, line in enumerate(lines):
            if line.strip() == "---":
                if not in_fm:
                    in_fm = True
                    updated_lines.append(line)
                    continue
                else:
                    in_fm = False
                    fm_end_idx = i
                    if not has_hero:
                        updated_lines.append(f'heroImage: "{hero_path}"')
                    if not has_og:
                        updated_lines.append(f'ogImage: "{hero_path}"')
                    updated_lines.append(line)
                    continue

            if in_fm:
                if line.startswith("heroImage:"):
                    updated_lines.append(f'heroImage: "{hero_path}"')
                    has_hero = True
                    continue
                if line.startswith("ogImage:"):
                    updated_lines.append(f'ogImage: "{hero_path}"')
                    has_og = True
                    continue
                # Also handle {HERO_IMAGE_PATH} placeholder
                if "{HERO_IMAGE_PATH}" in line:
                    line = line.replace("{HERO_IMAGE_PATH}", hero_path)
                    if "heroImage:" in line:
                        has_hero = True
                    if "ogImage:" in line:
                        has_og = True

            updated_lines.append(line)

        content = "\n".join(updated_lines)

        # 2. Inject images beneath H2 headings in body
        if not sections:
            return content

        # Split frontmatter from body
        parts = content.split("---", 2)
        if len(parts) < 3:
            return content

        fm_part = parts[1]
        body_part = parts[2]

        # Clean up any previously injected post images for this slug to allow idempotent re-injection
        body_part = re.sub(
            rf"\n*!\[.*?\]\(/images/posts/{re.escape(slug)}/[^\)]+\)\n+\*[^\*\n]+\*\n*",
            "\n\n",
            body_part,
        )

        # Find H2 headings
        h2_pattern = re.compile(r"^(##\s+([^\n]+))", flags=re.MULTILINE)
        matches = list(h2_pattern.finditer(body_part))

        # Do not put images under final bibliography or sources section
        valid_matches = []
        for m in matches:
            heading_title = m.group(2).strip()
            if any(term in heading_title.lower() for term in ["sources", "further reading", "references", "bibliography"]):
                continue
            valid_matches.append(m)

        # We inject one section image per valid H2, up to len(sections)
        new_body = body_part
        # Process in reverse order so character offsets in matches don't get displaced
        for idx in range(min(len(valid_matches), len(sections)) - 1, -1, -1):
            m = valid_matches[idx]
            heading_title = m.group(2).strip()
            sec = sections[idx]
            img_url = sec["dest_path"]
            src_name = sec["source_name"]

            # Check if this heading already has an image tag right after it
            end_pos = m.end()
            peek = new_body[end_pos:end_pos + 200]
            if "![" in peek:
                continue  # already has an image

            # Look up metadata
            shot_id = Path(src_name).stem.replace("_", "-")
            meta = shot_metadata.get(shot_id)
            alt_text, caption = self.derive_caption(Path(src_name), meta, heading_title)

            injection = f"\n\n![{alt_text}]({img_url})\n*{caption}*\n"
            new_body = new_body[:end_pos] + injection + new_body[end_pos:]

        return f"---{fm_part}---{new_body}"
