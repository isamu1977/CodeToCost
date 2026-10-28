#!/usr/bin/env python3
"""
compile_kdp_anthology.py — Flexible KDP & Ko-fi Book Compiler
Compiles selected project chapters into "The Feudal Archival Folio" (6x9" US Trade).
Outputs:
  - manuscript_clean.html (Self-contained HTML for Amazon Kindle Create)
  - book_collector_edition.pdf (High-res 6x9" PDF via Playwright for Ko-fi / print)
"""

import argparse
import base64
import logging
import mimetypes
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

from jinja2 import Environment, FileSystemLoader
from markdown_it import MarkdownIt

# Add parent directory to path to allow redsun imports
CURRENT_DIR = Path(__file__).resolve().parent
PARENT_DIR = CURRENT_DIR.parent
if str(PARENT_DIR) not in sys.path:
    sys.path.insert(0, str(PARENT_DIR))

try:
    from redsun.workspace import RUNTIME_ROOT, resolve_project_dir
except ImportError:
    RUNTIME_ROOT = Path("/Users/isamumatsuyama/Documents/red-sun-japan-runtime/projects")
    def resolve_project_dir(slug: str) -> Optional[Path]:
        direct = RUNTIME_ROOT / slug
        if direct.exists():
            return direct
        for p in RUNTIME_ROOT.iterdir():
            if p.is_dir() and (p.name == slug or p.name.endswith(f"_{slug}")):
                return p
        return None

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("compile_kdp_anthology")


ROMAN_NUMERALS = [
    (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
    (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
    (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")
]

def to_roman(num: int) -> str:
    """Converts integer to Roman numeral."""
    result = []
    for val, roman in ROMAN_NUMERALS:
        while num >= val:
            result.append(roman)
            num -= val
    return "".join(result) or "I"


def file_to_data_uri(file_path: Path) -> str:
    """Encodes an image file to a base64 data URI."""
    mime_type, _ = mimetypes.guess_type(file_path.name)
    if not mime_type:
        mime_type = "image/jpeg"
    encoded = base64.b64encode(file_path.read_bytes()).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"


def find_top_images(project_dir: Path, count: int = 2) -> list[tuple[Path, str]]:
    """
    Finds the 2 best high-resolution images for the chapter:
    Prioritizes images/final/shot_*.jpeg, hero.jpeg, distribution/pins_media/pin_*.jpeg, distribution/thumbnail.png.
    """
    candidates: list[Path] = []

    # 1. Look in images/final/
    final_dir = project_dir / "images" / "final"
    if final_dir.exists():
        shots = sorted(
            [f for f in final_dir.iterdir() if f.suffix.lower() in (".jpg", ".jpeg", ".png") and not f.name.startswith(".")],
            key=lambda x: x.name
        )
        if shots:
            # Pick first shot and a middle/later shot
            candidates.append(shots[0])
            if len(shots) > 1:
                # Pick one around index 2 or 3 if available
                pick_idx = min(len(shots) - 1, 2)
                candidates.append(shots[pick_idx])

    # 2. Look in distribution/pins_media/
    pins_dir = project_dir / "distribution" / "pins_media"
    if len(candidates) < count and pins_dir.exists():
        pins = sorted(
            [f for f in pins_dir.iterdir() if f.suffix.lower() in (".jpg", ".jpeg", ".png") and not f.name.startswith(".")],
            key=lambda x: x.name
        )
        for p in pins:
            if p not in candidates:
                candidates.append(p)
                if len(candidates) >= count:
                    break

    # 3. Look in distribution/thumbnail.png or hero.jpeg
    if len(candidates) < count:
        thumb = project_dir / "distribution" / "thumbnail.png"
        if thumb.exists() and thumb not in candidates:
            candidates.append(thumb)
        hero = project_dir / "hero.jpeg"
        if hero.exists() and hero not in candidates:
            candidates.append(hero)

    results: list[tuple[Path, str]] = []
    for idx, img_path in enumerate(candidates[:count], start=1):
        caption = f"Archival Plate {to_roman(idx)}: Historical documentary illustration preserved in the chronicle record."
        results.append((img_path, caption))

    return results


def parse_glossary(glossary_text: str) -> list[dict[str, str]]:
    """
    Parses cultural glossary bullet lines into structured entries.
    Matches lines like:
      - ***Term*** (*reading*): Definition
      - **Term**: Definition
    """
    items: list[dict[str, str]] = []
    lines = glossary_text.strip().splitlines()
    pattern = re.compile(r"^\s*-\s*\*{2,3}([^*]+)\*{2,3}(?:\s*\(([^)]+)\))?:\s*(.+)$")

    for line in lines:
        match = pattern.match(line)
        if match:
            term = match.group(1).strip()
            reading = (match.group(2) or "").strip()
            definition = match.group(3).strip()
            items.append({
                "term": term,
                "reading": reading,
                "definition": definition
            })
    return items


def inject_art_plates(body_html: str, plates: list[dict[str, str]]) -> str:
    """
    Injects art plates gracefully into the chapter body HTML.
    Plate 1 after paragraph 3 (or first subsection).
    Plate 2 around paragraph 8-10 (or second subsection).
    """
    if not plates:
        return body_html

    def make_plate_html(plate: dict[str, str]) -> str:
        return (
            f'\n<figure class="art-plate">\n'
            f'  <img src="{plate["src"]}" alt="{plate["caption"]}" />\n'
            f'  <figcaption><em>{plate["caption"]}</em></figcaption>\n'
            f'</figure>\n'
        )

    # Split by </p>
    p_tags = body_html.split("</p>")
    total_p = len(p_tags)

    if total_p <= 2:
        # Just append plates
        for pl in plates:
            body_html += make_plate_html(pl)
        return body_html

    # Plate 1 placement
    plate_1_idx = min(3, total_p // 3)
    p_tags.insert(plate_1_idx, make_plate_html(plates[0]))

    # Plate 2 placement if present
    if len(plates) > 1:
        plate_2_idx = min(len(p_tags) - 2, (total_p * 2) // 3 + 1)
        if plate_2_idx <= plate_1_idx:
            plate_2_idx = len(p_tags) - 1
        p_tags.insert(plate_2_idx, make_plate_html(plates[1]))

    return "</p>".join(p_tags)


def parse_chapter_markdown(md_text: str, chapter_idx: int, project_dir: Path) -> dict[str, Any]:
    """
    Parses a single book/chapter.md into clean HTML sections and metadata.
    """
    md_parser = MarkdownIt("commonmark")

    # 1. Parse Title & Subtitle
    lines = md_text.splitlines()
    raw_title_line = ""
    for l in lines:
        if l.strip().startswith("# "):
            raw_title_line = l.strip()[2:].strip()
            break

    # Strip Chapter prefix e.g. "Chapter VII: Hoichi the Earless (耳なし芳一)"
    title_no_prefix = re.sub(r"^Chapter\s+[IVXLCDM\d]+:\s*", "", raw_title_line, flags=re.IGNORECASE).strip()
    if not title_no_prefix:
        title_no_prefix = f"Chronicle {chapter_idx}"

    # Extract Japanese subtitle from parens if present e.g. (耳なし芳一)
    subtitle_ja = ""
    ja_match = re.search(r"\(([\u3000-\u303F\u3040-\u309F\u30A0-\u30FF\uFF00-\uFFEF\u4E00-\u9FAF]+.*?)\)", title_no_prefix)
    if ja_match:
        subtitle_ja = ja_match.group(1).strip()
        clean_title = re.sub(r"\([^)]*\)", "", title_no_prefix).strip()
    else:
        clean_title = title_no_prefix

    # Clean punctuation at edges
    clean_title = re.sub(r"[\s—:–-]+$", "", clean_title).strip()

    # 2. Extract Major Sections
    # Sections usually demarcated by ### Headers or ---
    sections = re.split(r"\n(?=###?\s+)", md_text)

    provenance_raw = ""
    chronicle_parts: list[str] = []
    footnotes_raw = ""
    glossary_raw = ""

    for sec in sections:
        sec_strip = sec.strip()
        if not sec_strip:
            continue
        first_line = sec_strip.splitlines()[0].strip()

        if "Archival Introduction" in first_line or "Archival Provenance" in first_line:
            # Remove header line and trailing divider
            content_lines = sec_strip.splitlines()[1:]
            content = "\n".join(content_lines).strip()
            content = re.sub(r"\n---+\s*$", "", content).strip()
            provenance_raw = content
        elif "Historical Footnotes" in first_line or "Archival Analysis" in first_line:
            content_lines = sec_strip.splitlines()[1:]
            content = "\n".join(content_lines).strip()
            content = re.sub(r"\n---+\s*$", "", content).strip()
            footnotes_raw = content
        elif "Cultural Glossary" in first_line:
            content_lines = sec_strip.splitlines()[1:]
            glossary_raw = "\n".join(content_lines).strip()
        elif first_line.startswith("# ") and "Chapter" in first_line:
            # Top title section, ignore
            continue
        elif "The Chronicle" in first_line:
            # Top chronicle divider, remove header
            content_lines = sec_strip.splitlines()[1:]
            sub_body = "\n".join(content_lines).strip()
            if sub_body:
                chronicle_parts.append(sub_body)
        else:
            # Chronicle subsection (e.g. ### I. The Haunted Strait)
            chronicle_parts.append(sec_strip)

    # 3. Process Chronicle Body
    full_chronicle_md = "\n\n".join(chronicle_parts)
    # Replace markdown scene breaks `---` with ornamental separator
    full_chronicle_md = re.sub(r"\n\s*---\s*\n", '\n\n<div class="scene-break">❖ &nbsp; ❖ &nbsp; ❖</div>\n\n', full_chronicle_md)

    body_html = md_parser.render(full_chronicle_md)

    # 4. Find Best Images & Inject
    raw_images = find_top_images(project_dir, count=2)
    plates_data = []
    for img_path, caption in raw_images:
        try:
            uri = file_to_data_uri(img_path)
            plates_data.append({"src": uri, "caption": caption})
        except Exception as e:
            logger.warning("Could not encode image %s: %s", img_path, e)

    body_html_with_plates = inject_art_plates(body_html, plates_data)

    # 5. Process Provenance & Footnotes
    provenance_html = md_parser.render(provenance_raw) if provenance_raw else ""
    footnotes_html = md_parser.render(footnotes_raw) if footnotes_raw else ""

    # 6. Process Cultural Glossary
    glossary_items = parse_glossary(glossary_raw) if glossary_raw else []

    return {
        "chapter_num_roman": f"Chapter {to_roman(chapter_idx)}",
        "clean_title": clean_title,
        "subtitle_ja": subtitle_ja,
        "provenance_html": provenance_html,
        "body_html": body_html_with_plates,
        "footnotes_html": footnotes_html,
        "glossary_items": glossary_items,
    }


async def compile_anthology(
    slugs: list[str],
    volume: int = 1,
    title: Optional[str] = None,
    foreword_path: Optional[str] = None,
    output_dir: Optional[str] = None,
) -> dict[str, Any]:
    """
    Compiles an anthology volume from a list of project slugs.
    Generates manuscript_clean.html and book_collector_edition.pdf.
    """
    book_title = title or f"Chronicles of the Red Sun, Vol. {volume}: Illustrated & Annotated Edo Kaidan"

    # Resolve output directory
    if output_dir:
        out_path = Path(output_dir)
        if not out_path.is_absolute():
            # If path starts with projects/, anchor to RUNTIME_ROOT
            if str(output_dir).startswith("projects/"):
                rel_part = str(output_dir).replace("projects/", "", 1)
                out_path = RUNTIME_ROOT / rel_part
            else:
                out_path = Path.cwd() / output_dir
    else:
        out_path = RUNTIME_ROOT / "kdp_books" / f"vol_{volume}"

    out_path.mkdir(parents=True, exist_ok=True)
    logger.info("Output directory resolved to: %s", out_path)

    # Parse Foreword if provided
    foreword_html = ""
    if foreword_path:
        fw_p = Path(foreword_path)
        if fw_p.exists():
            md_parser = MarkdownIt("commonmark")
            foreword_html = md_parser.render(fw_p.read_text(encoding="utf-8"))
            logger.info("Loaded foreword from %s", fw_p)
        else:
            logger.warning("Foreword file %s not found. Proceeding without foreword.", foreword_path)

    # Process each slug
    compiled_chapters: list[dict[str, Any]] = []
    chapter_counter = 1

    for slug in slugs:
        slug = slug.strip()
        if not slug:
            continue

        project_dir = resolve_project_dir(slug)
        if not project_dir:
            print(f"\n⚠️  [WARNING] Project '{slug}' not found in runtime directory ({RUNTIME_ROOT}). Skipping.")
            logger.warning("Project '%s' directory not found. Skipping.", slug)
            continue

        chapter_file = project_dir / "book" / "chapter.md"
        if not chapter_file.exists():
            print(f"\n⚠️  [WARNING] Project '{slug}' does not have 'book/chapter.md' at {chapter_file}. Skipping chapter compilation.")
            logger.warning("Project '%s' missing 'book/chapter.md'. Skipping.", slug)
            continue

        logger.info("Compiling Chapter %d: %s (from %s)", chapter_counter, slug, chapter_file)
        try:
            md_text = chapter_file.read_text(encoding="utf-8")
            ch_data = parse_chapter_markdown(md_text, chapter_counter, project_dir)
            compiled_chapters.append(ch_data)
            chapter_counter += 1
        except Exception as e:
            logger.error("Failed to parse chapter for slug %s: %s", slug, e, exc_info=True)
            print(f"\n❌ [ERROR] Failed to compile chapter for slug '{slug}': {e}")

    if not compiled_chapters:
        raise ValueError("No valid chapters could be compiled from the provided slugs.")

    # Load Jinja2 Template
    templates_dir = PARENT_DIR / "templates"
    env = Environment(loader=FileSystemLoader(str(templates_dir)))
    template = env.get_template("book_template.html")

    # Render Template
    html_output = template.render(
        book_title=book_title,
        subtitle="Illustrated & Annotated Edo Kaidan",
        volume=volume,
        publication_year="2026",
        foreword_html=foreword_html,
        chapters=compiled_chapters,
    )

    # Write manuscript_clean.html
    html_file = out_path / "manuscript_clean.html"
    html_file.write_text(html_output, encoding="utf-8")
    html_size_kb = html_file.stat().st_size / 1024
    logger.info("Saved clean KDP manuscript: %s (%.1f KB)", html_file, html_size_kb)

    # Generate PDF via Playwright
    pdf_file = out_path / "book_collector_edition.pdf"
    logger.info("Rendering collector's edition 6x9\" PDF via Playwright headless...")

    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            await page.set_content(html_output, wait_until="networkidle")
            await page.pdf(
                path=str(pdf_file),
                print_background=True,
                prefer_css_page_size=True,
            )
            await browser.close()

        pdf_size_mb = pdf_file.stat().st_size / (1024 * 1024)
        logger.info("Rendered high-resolution PDF: %s (%.2f MB)", pdf_file, pdf_size_mb)
    except Exception as exc:
        logger.error("Playwright PDF generation failed: %s", exc, exc_info=True)
        raise

    return {
        "status": "SUCCESS",
        "volume": volume,
        "book_title": book_title,
        "chapters_count": len(compiled_chapters),
        "output_dir": str(out_path),
        "manuscript_html": str(html_file),
        "manuscript_html_size_bytes": html_file.stat().st_size,
        "book_pdf": str(pdf_file),
        "book_pdf_size_bytes": pdf_file.stat().st_size,
    }


def compile_anthology_sync(
    slugs: list[str],
    volume: int = 1,
    title: Optional[str] = None,
    foreword_path: Optional[str] = None,
    output_dir: Optional[str] = None,
) -> dict[str, Any]:
    """Synchronous wrapper for compile_anthology."""
    import asyncio
    return asyncio.run(
        compile_anthology(
            slugs=slugs,
            volume=volume,
            title=title,
            foreword_path=foreword_path,
            output_dir=output_dir,
        )
    )


def main():
    parser = argparse.ArgumentParser(
        description="Compile selected Red Sun Japan project chapters into a 6x9\" KDP & Ko-fi Anthology."
    )
    parser.add_argument(
        "--slugs",
        type=str,
        required=True,
        help="Comma-separated list of project slugs in exact chapter order (e.g. slug1,slug2,slug3)",
    )
    parser.add_argument(
        "--volume",
        type=int,
        default=1,
        help="Volume number of the collection (default: 1)",
    )
    parser.add_argument(
        "--title",
        type=str,
        default=None,
        help="Book title (default: 'Chronicles of the Red Sun, Vol. {volume}: Illustrated & Annotated Edo Kaidan')",
    )
    parser.add_argument(
        "--foreword",
        type=str,
        default=None,
        help="Optional path to a Markdown file containing foreword/preface",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Output directory (default: projects/kdp_books/vol_{volume}/)",
    )

    args = parser.parse_args()
    slug_list = [s.strip() for s in args.slugs.split(",") if s.strip()]

    print("=" * 80)
    print(f"RED SUN JAPAN // KDP & KO-FI BOOK COMPILER")
    print(f"Volume: {args.volume} | Slugs: {', '.join(slug_list)}")
    print("=" * 80)

    try:
        result = compile_anthology_sync(
            slugs=slug_list,
            volume=args.volume,
            title=args.title,
            foreword_path=args.foreword,
            output_dir=args.output_dir,
        )
        print("\n" + "=" * 80)
        print("COMPILATION REPORT: SUCCESS")
        print("=" * 80)
        print(f"Book Title:       {result['book_title']}")
        print(f"Chapters Count:   {result['chapters_count']}")
        print(f"Output Directory: {result['output_dir']}")
        print(f"HTML Manuscript:  {result['manuscript_html']} ({result['manuscript_html_size_bytes'] / 1024:.1f} KB)")
        print(f"Collector PDF:    {result['book_pdf']} ({result['book_pdf_size_bytes'] / (1024 * 1024):.2f} MB)")
        print("=" * 80 + "\n")
    except Exception as exc:
        print(f"\n❌ Compilation aborted with error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
