"""Official YouTube Thumbnail Generator for Red Sun Japan.

Renders 1280x720 high-impact YouTube thumbnails via Playwright using
the canonical template at `templates/thumbnail_template.html`.
Respects YouTube's bottom-right safe zone (no overlay blocking video timestamp).
Saves output to `projects/{slug}/distribution/thumbnail.png`.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import mimetypes
import re
from pathlib import Path
from typing import Any, Optional

from playwright.sync_api import sync_playwright

logger = logging.getLogger("redsun.thumbnail_generator")

TEMPLATE_PATH = Path(__file__).resolve().parent.parent.parent / "templates" / "thumbnail_template.html"


def _image_to_base64_src(image_path: Path) -> str:
    """Read a local image and convert to data URI for self-contained HTML rendering."""
    mime, _ = mimetypes.guess_type(str(image_path))
    if not mime:
        suffix = image_path.suffix.lower()
        if suffix in {".jpg", ".jpeg"}:
            mime = "image/jpeg"
        elif suffix == ".webp":
            mime = "image/webp"
        else:
            mime = "image/png"
    raw_data = image_path.read_bytes()
    b64 = base64.b64encode(raw_data).decode("utf-8")
    return f"data:{mime};base64,{b64}"


def derive_headline_copy(title: str) -> dict[str, str]:
    """Derive kicker, headline_line1, and headline_highlight from a project title."""
    clean_title = title.strip()

    # Default fallback kicker
    kicker = "ANCIENT KAIDAN"
    lower = clean_title.lower()
    if "kappa" in lower:
        kicker = "RIVER SPIRIT LORE"
    elif "yurei" in lower or "ghost" in lower or "phantom" in lower:
        kicker = "HAUNTING OF EDO"
    elif "samurai" in lower or "ronin" in lower:
        kicker = "SAMURAI CHRONICLES"
    elif "folklore" in lower or "legend" in lower:
        kicker = "JAPANESE FOLKLORE"

    # Separate title by dash, colon or em-dash
    parts = re.split(r"[\u2014\u2013\-:]", clean_title, maxsplit=1)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
        main_topic = parts[0].strip()
        subtitle = parts[1].strip()
        sub_lower = subtitle.lower()

        # Specific pattern for journey / travel chronicles (e.g. Kappa)
        if "journey through" in sub_lower or "journey to" in sub_lower:
            return {
                "kicker": "THE RIVER SPIRIT'S COUNTRY" if "river spirit" in sub_lower else "JAPANESE FOLKLORE",
                "headline_line1": "JOURNEY TO",
                "headline_highlight": main_topic.upper(),
            }

        # If subtitle is short (<= 3 words), use it cleanly
        sub_words = subtitle.split()
        if len(sub_words) <= 3:
            return {
                "kicker": subtitle.upper(),
                "headline_line1": "THE TALE OF",
                "headline_highlight": main_topic.upper(),
            }

        return {
            "kicker": kicker,
            "headline_line1": " ".join(sub_words[:2]).upper(),
            "headline_highlight": main_topic.upper(),
        }

    # Split words
    words = clean_title.split()
    if len(words) <= 2:
        return {
            "kicker": kicker,
            "headline_line1": "THE TALE OF",
            "headline_highlight": clean_title.upper(),
        }

    if words[0].lower() in {"the", "a", "an"}:
        line1 = f"{words[0]} {words[1]}".upper()
        highlight = " ".join(words[2:]).upper()
    else:
        half = max(1, len(words) // 2)
        line1 = " ".join(words[:half]).upper()
        highlight = " ".join(words[half:]).upper()

    return {
        "kicker": kicker,
        "headline_line1": line1,
        "headline_highlight": highlight,
    }


class ThumbnailGenerator:
    """Automates rendering of official 1280x720 YouTube thumbnails."""

    def __init__(self, template_path: Path | None = None) -> None:
        self.template_path = template_path or TEMPLATE_PATH
        if not self.template_path.exists():
            raise FileNotFoundError(f"Thumbnail template not found at {self.template_path}")

    def find_hero_image(
        self,
        project_dir: Path,
        slug: str = "",
        title: str = "",
        source_image_filename: Optional[str] = None,
    ) -> Optional[Path]:
        """Locate the best representative image for the thumbnail.

        1. Explicit Selection:
           If `source_image_filename` is provided, validate and return its physical path
           within the active project.

        2. Strict Deterministic Precedence:
           - Priority 1: projects/{slug}/gemini-assistant/references/character-protagonist.jpeg (or ref-002)
           - Priority 2: projects/{slug}/gemini-assistant/references/character-entity.jpeg (or ref-003)
           - Priority 3: First rendered scene in projects/{slug}/gemini-assistant/output/shot-001.jpeg
           - Priority 4: projects/{slug}/gemini-assistant/references/style-master.jpeg (or ref-001)

        3. Express Prohibition:
           Secondary characters (character-priest, character-supporting, character-witness,
           character-assistant) are strictly forbidden as default fallbacks when protagonist exists.
        """
        valid_exts = {".png", ".jpg", ".jpeg", ".webp"}

        # ── 1. Explicit Custom Image Selection ──────────────────────────────
        if source_image_filename:
            custom_target = source_image_filename.strip()

            # Check if absolute path was passed
            cand_p = Path(custom_target)
            if cand_p.is_absolute() and cand_p.exists() and cand_p.is_file():
                if cand_p.suffix.lower() in valid_exts:
                    logger.info("Using explicit absolute image for thumbnail: %s", cand_p)
                    return cand_p

            # Check direct relative path from project_dir
            rel_candidate = project_dir / custom_target
            if rel_candidate.exists() and rel_candidate.is_file() and rel_candidate.suffix.lower() in valid_exts:
                logger.info("Using explicit relative image for thumbnail: %s", rel_candidate)
                return rel_candidate

            # Search inside standard candidate folders
            search_dirs = [
                project_dir / "gemini-assistant" / "references",
                project_dir / "gemini-assistant" / "output",
                project_dir / "images" / "references",
                project_dir / "images" / "final",
                project_dir / "references",
                project_dir / "distribution" / "pins_media",
            ]
            base_name = Path(custom_target).name
            for sdir in search_dirs:
                direct_match = sdir / base_name
                if direct_match.exists() and direct_match.is_file() and direct_match.suffix.lower() in valid_exts:
                    logger.info("Found explicit image in %s: %s", sdir.name, direct_match)
                    return direct_match

            # Recursive search in project_dir
            for cand in project_dir.rglob(base_name):
                if cand.is_file() and cand.suffix.lower() in valid_exts:
                    logger.info("Found explicit image via rglob: %s", cand)
                    return cand

            logger.error("Requested thumbnail image '%s' not found in %s", custom_target, project_dir)
            raise FileNotFoundError(
                f"Requested thumbnail image '{source_image_filename}' not found in project workspace."
            )

        # ── 2. Automatic Deterministic Selection Hierarchy ──────────────────
        ref_dirs = [
            project_dir / "gemini-assistant" / "references",
            project_dir / "images" / "references",
            project_dir / "references",
            project_dir / "gemini-assistant" / "ref-job",
        ]
        output_dirs = [
            project_dir / "gemini-assistant" / "output",
            project_dir / "images" / "final",
            project_dir / "output",
            project_dir / "distribution" / "pins_media",
        ]

        def _scan_for_prefixes(prefixes: list[str], search_folders: list[Path]) -> Optional[Path]:
            for folder in search_folders:
                if not folder.exists():
                    continue
                # Sort files to guarantee deterministic order
                for f in sorted(folder.iterdir()):
                    if f.is_file() and f.suffix.lower() in valid_exts and not f.name.startswith("."):
                        stem_lower = f.stem.lower()
                        for prefix in prefixes:
                            if stem_lower == prefix or stem_lower.startswith(f"{prefix}.") or stem_lower.startswith(f"{prefix}_"):
                                return f
                            # Substring match if clean
                            if prefix in stem_lower:
                                return f
            return None

        # Check if project title/slug indicates a creature, spirit, or monster as primary subject
        creature_keywords = {
            "kappa", "yuki-onna", "yuki onna", "rokurokubi", "jorogumo", "toad", "serpent",
            "dragon", "tengu", "oni", "bake-danuki", "tanuki", "kitsune", "monster", "spirit",
            "goblin", "demon", "creature", "spider", "witch", "cat-demon", "bakeneko", "ghost"
        }
        title_slug_context = f"{slug} {title}".lower()
        prefer_entity = any(kw in title_slug_context for kw in creature_keywords)

        if prefer_entity:
            # Priority 1 (Creature Chronicles): Supernatural Entity / Creature (character-entity, ref-003)
            p_entity = _scan_for_prefixes(["character-entity", "ref-003", "ref_003", "entity"], ref_dirs)
            if p_entity:
                logger.info("Deterministic Thumbnail Selection [Priority 1 (Creature Chronicle) - Entity]: %s", p_entity.name)
                return p_entity

        # Priority 1: Main Protagonist (character-protagonist, ref-002)
        p1 = _scan_for_prefixes(["character-protagonist", "ref-002", "ref_002", "protagonist"], ref_dirs)
        if p1:
            logger.info("Deterministic Thumbnail Selection [Priority 1 - Protagonist]: %s", p1.name)
            return p1

        # Priority 2: Supernatural Entity / Antagonist (character-entity, ref-003)
        p2 = _scan_for_prefixes(["character-entity", "ref-003", "ref_003", "entity"], ref_dirs)
        if p2:
            logger.info("Deterministic Thumbnail Selection [Priority 2 - Entity]: %s", p2.name)
            return p2

        # Priority 3: First rendered scene in output (shot-001, shot_001)
        p3 = _scan_for_prefixes(["shot_001", "shot-001", "shot_01", "shot-01", "scene_001", "scene-001", "pin_01"], output_dirs)
        if p3:
            logger.info("Deterministic Thumbnail Selection [Priority 3 - Scene 001]: %s", p3.name)
            return p3

        # Priority 4: Style Master (style-master, ref-001)
        p4 = _scan_for_prefixes(["style-master", "ref-001", "ref_001", "style_anchor"], ref_dirs)
        if p4:
            logger.info("Deterministic Thumbnail Selection [Priority 4 - Style Master]: %s", p4.name)
            return p4

        # Strict prohibition: exclude secondary characters from default fallback
        secondary_forbidden = {"character-priest", "character-supporting", "character-witness", "character-assistant"}
        for folder in ref_dirs + output_dirs:
            if not folder.exists():
                continue
            for f in sorted(folder.iterdir()):
                if f.is_file() and f.suffix.lower() in valid_exts and not f.name.startswith("."):
                    if not any(sec in f.stem.lower() for sec in secondary_forbidden):
                        logger.warning("Deterministic fallback to neutral asset: %s", f.name)
                        return f

        # Extreme last resort only if literally nothing else exists
        for folder in ref_dirs + output_dirs:
            if not folder.exists():
                continue
            for f in sorted(folder.iterdir()):
                if f.is_file() and f.suffix.lower() in valid_exts and not f.name.startswith("."):
                    logger.warning("Exhausted fallbacks, using remaining asset: %s", f.name)
                    return f

        return None

    def get_brand_logo_base64(self) -> str:
        """Find the official Red Sun Japan brand logo and encode to base64 data URI."""
        candidate_paths = [
            Path(__file__).resolve().parent.parent.parent / "assets" / "brand" / "red-sun-logo-tate.png",
            Path(__file__).resolve().parent.parent.parent.parent / "render-remotion" / "public" / "red-sun-logo-tate.png",
            Path(__file__).resolve().parent.parent.parent.parent / "redsunjapan-blog" / "public" / "assets" / "red-sun-logo-tate.png",
        ]
        for cp in candidate_paths:
            if cp.exists():
                return _image_to_base64_src(cp)
        return "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"

    def render_thumbnail(
        self,
        project_dir: Path,
        slug: str,
        title: str,
        copy_data: Optional[dict[str, str]] = None,
        hero_image_path: Optional[Path] = None,
        output_path: Optional[Path] = None,
        source_image_filename: Optional[str] = None,
    ) -> dict[str, Any]:
        """Generate 1280x720 thumbnail image via Playwright."""
        logger.info("Generating YouTube Thumbnail for '%s'...", slug)

        target_out = output_path or (project_dir / "distribution" / "thumbnail.png")
        target_out.parent.mkdir(parents=True, exist_ok=True)

        # 1. Resolve Hero Image using strict deterministic rules
        hero_img = hero_image_path or self.find_hero_image(
            project_dir,
            slug=slug,
            title=title,
            source_image_filename=source_image_filename,
        )
        if hero_img and hero_img.exists():
            image_src = _image_to_base64_src(hero_img)
            hero_name = hero_img.name
        else:
            logger.warning("No hero image found for '%s', using atmospheric fallback gradient", slug)
            # 1x1 black pixel fallback
            image_src = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
            hero_name = "fallback_gradient"

        # 2. Resolve Text Elements & Brand Logo
        derived = derive_headline_copy(title)
        kicker = (copy_data and copy_data.get("kicker")) or derived["kicker"]
        line1 = (copy_data and copy_data.get("headline_line1")) or derived["headline_line1"]
        highlight = (copy_data and copy_data.get("headline_highlight")) or derived["headline_highlight"]
        logo_src = self.get_brand_logo_base64()

        # 3. Interpolate HTML Template
        template_html = self.template_path.read_text(encoding="utf-8")
        rendered_html = (
            template_html
            .replace("{{IMAGE_SRC}}", image_src)
            .replace("{{LOGO_SRC}}", logo_src)
            .replace("{{KICKER}}", kicker.upper())
            .replace("{{HEADLINE_LINE1}}", line1.upper())
            .replace("{{HEADLINE_HIGHLIGHT}}", highlight.upper())
        )

        # 4. Render with Playwright Chromium
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
            )
            page = browser.new_page(
                viewport={"width": 1280, "height": 720},
                device_scale_factor=1,
            )
            page.set_content(rendered_html, wait_until="networkidle")

            try:
                page.evaluate("document.fonts.ready")
            except Exception as exc:
                logger.debug("Font load wait warning: %s", exc)

            page.wait_for_timeout(600)

            page.screenshot(
                path=str(target_out),
                clip={"x": 0, "y": 0, "width": 1280, "height": 720},
            )
            browser.close()

        logger.info("YouTube thumbnail rendered successfully: %s (hero: %s)", target_out, hero_name)

        return {
            "status": "COMPLETED",
            "path": str(target_out),
            "relative_path": "distribution/thumbnail.png",
            "resolution": "1280x720",
            "hero_image": hero_name,
            "kicker": kicker,
            "headline_line1": line1,
            "headline_highlight": highlight,
        }


class ThumbnailService:
    """High-level service interface for project thumbnail rendering."""

    @classmethod
    async def render_for_project(
        cls,
        slug: str,
        custom_image: Optional[str] = None,
        custom_kicker: Optional[str] = None,
        custom_headline_1: Optional[str] = None,
        custom_headline_highlight: Optional[str] = None,
    ) -> Path:
        """Render YouTube thumbnail for a given project slug with optional customizations.

        Overwrites projects/{slug}/distribution/thumbnail.png and updates metadata.
        """
        from redsun.workspace import ProjectWorkspace

        ws = ProjectWorkspace.from_slug(slug)
        project_dir = ws.root
        if not project_dir.exists():
            raise FileNotFoundError(f"Project workspace not found at {project_dir}")

        # Load project title from project.json if available
        title = slug.replace("-", " ").title()
        project_json_file = project_dir / "project.json"
        if project_json_file.exists():
            try:
                pdata = json.loads(project_json_file.read_text(encoding="utf-8"))
                title = pdata.get("title") or title
            except Exception:
                pass

        # Prepare copy data overrides
        copy_data: dict[str, str] = {}
        if custom_kicker:
            copy_data["kicker"] = custom_kicker.strip()
        if custom_headline_1:
            copy_data["headline_line1"] = custom_headline_1.strip()
        if custom_headline_highlight:
            copy_data["headline_highlight"] = custom_headline_highlight.strip()

        # Read existing distribution kit copy if any
        dist_json = project_dir / "distribution" / "social_distribution_kit.json"
        if dist_json.exists():
            try:
                ddata = json.loads(dist_json.read_text(encoding="utf-8"))
                yt_thumb = ddata.get("youtube", {}).get("thumbnail") or ddata.get("thumbnail") or {}
                if not copy_data.get("kicker") and yt_thumb.get("kicker"):
                    copy_data["kicker"] = yt_thumb["kicker"]
                if not copy_data.get("headline_line1") and yt_thumb.get("headline_line1"):
                    copy_data["headline_line1"] = yt_thumb["headline_line1"]
                if not copy_data.get("headline_highlight") and yt_thumb.get("headline_highlight"):
                    copy_data["headline_highlight"] = yt_thumb["headline_highlight"]
            except Exception:
                pass

        tg = ThumbnailGenerator()

        def _do_render():
            return tg.render_thumbnail(
                project_dir=project_dir,
                slug=slug,
                title=title,
                copy_data=copy_data if copy_data else None,
                source_image_filename=custom_image,
            )

        # Run Playwright sync execution in worker thread
        result = await asyncio.to_thread(_do_render)

        # Sync updated thumbnail metadata to social_distribution_kit.json
        if dist_json.exists():
            try:
                ddata = json.loads(dist_json.read_text(encoding="utf-8"))
                if "youtube" not in ddata:
                    ddata["youtube"] = {}
                thumb_entry = {
                    "kicker": result.get("kicker"),
                    "headline_line1": result.get("headline_line1"),
                    "headline_highlight": result.get("headline_highlight"),
                    "hero_image": result.get("hero_image"),
                    "path": result.get("relative_path"),
                }
                ddata["youtube"]["thumbnail"] = thumb_entry
                ddata["thumbnail"] = thumb_entry
                dist_json.write_text(json.dumps(ddata, indent=2, ensure_ascii=False), encoding="utf-8")
            except Exception as e:
                logger.warning("Could not sync thumbnail metadata to social_distribution_kit.json: %s", e)

        return Path(result["path"])

    @classmethod
    def list_available_images(cls, slug: str) -> list[dict[str, Any]]:
        """List candidate images for the project's thumbnail with friendly labels and categories."""
        from redsun.workspace import ProjectWorkspace

        ws = ProjectWorkspace.from_slug(slug)
        project_dir = ws.root
        if not project_dir.exists():
            return []

        valid_exts = {".png", ".jpg", ".jpeg", ".webp"}
        candidates: list[dict[str, Any]] = []

        # 1. References
        ref_dir = project_dir / "gemini-assistant" / "references"
        if ref_dir.exists():
            for f in sorted(ref_dir.iterdir()):
                if f.is_file() and f.suffix.lower() in valid_exts and not f.name.startswith("."):
                    stem = f.stem.lower()
                    if "protagonist" in stem or "ref-002" in stem:
                        label = f"Protagonista Principal ({f.name})"
                        category = "protagonist"
                        prio = 1
                    elif "entity" in stem or "ref-003" in stem:
                        label = f"Entidade / Yōkai ({f.name})"
                        category = "entity"
                        prio = 2
                    elif "style-master" in stem or "ref-001" in stem:
                        label = f"Style Master ({f.name})"
                        category = "style"
                        prio = 4
                    elif "priest" in stem or "ref-007" in stem:
                        label = f"Monge / Abade ({f.name})"
                        category = "secondary"
                        prio = 10
                    else:
                        label = f"Referência: {f.name}"
                        category = "secondary"
                        prio = 12

                    candidates.append({
                        "id": f.name,
                        "name": f.name,
                        "relative_path": f"gemini-assistant/references/{f.name}",
                        "label": label,
                        "category": category,
                        "priority": prio,
                    })

        # 2. Output Scenes
        out_dir = project_dir / "gemini-assistant" / "output"
        if out_dir.exists():
            for f in sorted(out_dir.iterdir()):
                if f.is_file() and f.suffix.lower() in valid_exts and not f.name.startswith("."):
                    m = re.search(r"(\d+)", f.stem)
                    shot_num = int(m.group(1)) if m else 99
                    prio = 3 if shot_num == 1 else 20 + shot_num
                    candidates.append({
                        "id": f.name,
                        "name": f.name,
                        "relative_path": f"gemini-assistant/output/{f.name}",
                        "label": f"Cena Final {shot_num:02d} ({f.name})",
                        "category": "scene",
                        "priority": prio,
                    })

        # Sort by priority
        candidates.sort(key=lambda x: (x["priority"], x["name"]))
        return candidates
