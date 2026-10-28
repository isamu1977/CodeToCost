"""Canonical Style Bible and prompt formatting utilities for Red Sun Japan.

Ensures every image prompt for Gemini Assistant deterministically contains the
official channel Style Bible, preventing visual degradation or anime/3D drift.
"""
from __future__ import annotations

import re

MASTER_STYLE_BIBLE = """Cinematic semi-realistic historical dark fantasy digital painting,
grounded painterly realism, realistic human anatomy and natural
facial proportions, believable skin and fabric textures, highly
detailed historically authentic Japanese environment.

Sophisticated hand-painted digital artwork with subtle visible
brushwork and organic texture, realistic dimensionality without
photorealism.

Moody cinematic lighting, deep natural shadows, atmospheric depth,
soft volumetric mist, restrained highlights, realistic reflections
and physically believable light sources.

Muted desaturated color palette dominated by charcoal black,
weathered brown, dark indigo, slate gray and aged earth tones,
with restrained warm amber light from candles and lanterns.

Dark, melancholic, mysterious and haunting atmosphere.
Elegant supernatural horror rather than grotesque horror.

Composition and lighting inspired by prestige historical cinema,
combined with high-end narrative book illustration and dark
historical concept art.

Characters must look like believable real human beings interpreted
through a sophisticated digital painting — NOT anime characters.

Traditional Japanese architecture, clothing, objects and landscapes
must appear historically grounded, aged, weathered and lived-in.

STYLE EXCLUSIONS:
no anime, no manga, no cel shading, no comic-book outlines,
no graphic novel linework, no cartoon proportions,
no exaggerated facial features, no glossy fantasy illustration,
no neon colors, no oversaturated colors, no cute aesthetic,
no modern objects, no 3D-render appearance,
no photographic or hyper-photorealistic look."""

STYLE_TERMINATOR = "Single unified composition."


def sanitize_visual_prompt(raw_prompt: str, project_title: str = "", slug: str = "") -> str:
    """Remove project titles, episode names, and administrative/meta pollution from prompt text.

    Guarantees the prompt starts directly with the concrete subject or camera framing,
    preventing token bleed and unwanted typography generation while preserving legitimate creature names.
    """
    cleaned = str(raw_prompt or "").strip()
    if not cleaned:
        return ""

    # 1. Full title and dash-normalized variations
    if project_title:
        title_stripped = project_title.strip()
        # Escaped with flexible dash matching
        title_pattern = re.escape(title_stripped).replace(r"\—", "[-—–]").replace(r"\-", "[-—–]")
        cleaned = re.sub(rf"\s*(?:for|in|depicting)?\s*\(?{title_pattern}\)?", "", cleaned, flags=re.IGNORECASE)

        # Segments (e.g. 'A Journey Through the River Spirit\'s Country')
        for seg in re.split(r"[-—:–]+", title_stripped):
            seg_s = seg.strip()
            if len(seg_s) >= 12:
                seg_pattern = re.escape(seg_s)
                cleaned = re.sub(rf"\s*(?:for|in|depicting)?\s*\(?{seg_pattern}\)?", "", cleaned, flags=re.IGNORECASE)
            elif len(seg_s) >= 3:
                # For shorter tokens (e.g. 'Kappa'), only strip if preceded by 'for' or 'depicting'
                seg_pattern = re.escape(seg_s)
                cleaned = re.sub(rf"\s*(?:for|depicting)\s+\(?{seg_pattern}\)?", "", cleaned, flags=re.IGNORECASE)

    if slug:
        slug_pattern = re.escape(slug.strip())
        cleaned = re.sub(rf"\s*(?:for|in|depicting)?\s*\(?{slug_pattern}\)?", "", cleaned, flags=re.IGNORECASE)

    # Remove administrative reference tags
    cleaned = re.sub(r"—\s*Master References?", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"-\s*Master References?", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bMaster References?\b", "", cleaned, flags=re.IGNORECASE)

    # Clean double spaces, orphaned punctuation, and dangling prepositions
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+([,\.:;])", r"\1", cleaned)
    cleaned = re.sub(r",\s*,+", ",", cleaned)
    cleaned = re.sub(r"\.\s*\.+", ".", cleaned)
    cleaned = re.sub(r"\b(for|in|of|depicting)\s*([,\.])", r"\2", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^[,\.\s]+", "", cleaned)
    return cleaned.strip()


def format_prompt_with_style_bible(
    narrative_description: str,
    aspect_ratio: str = "16:9",
    project_title: str = "",
    slug: str = "",
) -> str:
    """Combines narrative description, aspect ratio, unity terminator, and the Master Style Bible."""
    cleaned = clean_narrative_text(narrative_description)
    if project_title or slug:
        cleaned = sanitize_visual_prompt(cleaned, project_title=project_title, slug=slug)
    cleaned = attenuate_content_for_safety(cleaned)
    ar = "3:4" if "3:4" in str(aspect_ratio) else "16:9"
    return f"{cleaned}\n\n{ar} aspect ratio. {STYLE_TERMINATOR}\n\n{MASTER_STYLE_BIBLE}"


def attenuate_content_for_safety(text: str) -> str:
    """Attenuates sensitive/violent terms to PG-13 Prestige documentary standards to prevent API safety policy triggers."""
    if not text:
        return ""

    # Contextual replacements to maintain eerie atmosphere while preventing content policy filters
    s = str(text)
    s = re.sub(r"\beyes\s+closed\s+in\s+death\b", "eyes serenely closed in deep sleep", s, flags=re.IGNORECASE)
    s = re.sub(r"\bno\s+wounds?\s+or\s+blood\s+visible\b", "peaceful and unmarked, completely intact", s, flags=re.IGNORECASE)
    s = re.sub(r"\bno\s+blood\s+or\s+wounds?\s+visible\b", "peaceful and unmarked, completely intact", s, flags=re.IGNORECASE)
    s = re.sub(r"\bno\s+visible\s+wounds?\s+or\s+blood\b", "peaceful and unmarked, completely intact", s, flags=re.IGNORECASE)
    s = re.sub(r"\bno\s+visible\s+blood\b", "peaceful and unmarked", s, flags=re.IGNORECASE)
    s = re.sub(r"\bno\s+blood\b", "no stains", s, flags=re.IGNORECASE)
    s = re.sub(r"\bdead\s+(man|woman|person|body|figure|villager|monk|priest|samurai|girl|boy|student)\b", r"motionless \1", s, flags=re.IGNORECASE)
    s = re.sub(r"\bdead\b", "lifeless", s, flags=re.IGNORECASE)
    s = re.sub(r"\bin\s+death\b", "in deep stillness", s, flags=re.IGNORECASE)
    s = re.sub(r"\bcorpse\b", "motionless body", s, flags=re.IGNORECASE)
    s = re.sub(r"\bcadaver\b", "motionless body", s, flags=re.IGNORECASE)
    s = re.sub(r"\bbloody\b", "dark-stained", s, flags=re.IGNORECASE)
    s = re.sub(r"\bblood\b", "crimson shadow", s, flags=re.IGNORECASE)
    s = re.sub(r"\bstrangled\b", "overcome", s, flags=re.IGNORECASE)
    s = re.sub(r"\bmurdered\b", "fallen in folklore", s, flags=re.IGNORECASE)
    s = re.sub(r"\bdecapitated\b", "shadowed", s, flags=re.IGNORECASE)
    return s


def clean_narrative_text(raw_prompt: str) -> str:
    """Strips legacy terminators, duplicate style tags, and style exclusions to isolate narrative core."""
    text = str(raw_prompt or "").strip()

    # Remove existing STYLE EXCLUSIONS block if present
    if "STYLE EXCLUSIONS:" in text:
        text = text.split("STYLE EXCLUSIONS:")[0].strip()

    # Remove the Master Style Bible if already embedded
    bible_lead = "Cinematic semi-realistic historical dark fantasy digital painting"
    if bible_lead in text:
        text = text.split(bible_lead)[0].strip()

    # Remove legacy short terminators (only when appearing on separate lines or at block boundaries)
    text = re.sub(
        r"(?:^|\n)\s*(?:3:4|16:9)\s+aspect\s+ratio[^\.\n]*\.?",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"cinematic\s+historical\s+dark\s+fantasy\s+digital\s+painting,\s*muted\s+painterly\s+realism\.?",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"Single\s+unified\s+composition\.?", "", text, flags=re.IGNORECASE)

    # Clean up whitespace and trailing punctuation
    text = text.strip().rstrip(". ")
    if not text:
        return "Detailed Edo Japanese scene"
    return f"{text}."

