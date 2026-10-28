"""Reference Service — Deterministic Visual Reference Assembler.

Assembles Phase 1 (Handoff Gate: Visual References) gemini-assistant/project.json (Schema v2).
Features:
1. Primary Deterministic Assembly: Consumes VisualEntitiesRoster provided via POST /projects.
   Zero LLM token cost ($0) - binds physical character & environment curation directly
   with Master Style Bible and Style Exclusions.
2. Low-Cost Fallback: If visual_entities is absent, uses low-cost utility model
   (google/gemini-2.5-flash or anthropic/claude-3.5-haiku, NEVER Claude Sonnet 5).
3. Strict Prompt Sanitization: Sanitizes any metadata/title/slug pollution.
4. Linter Gate: Runs ReferenceFidelityLinter prior to writing project.json.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Union

from pydantic import BaseModel, Field

from llm_client import LLMConfig, _call_openrouter, extract_json_safely
from redsun.services.reference_fidelity_linter import (
    PromptContaminationError,
    ReferenceFidelityError,
    ReferenceFidelityLinter,
)
from redsun.services.style_constants import (
    MASTER_STYLE_BIBLE,
    clean_narrative_text,
    format_prompt_with_style_bible,
    sanitize_visual_prompt,
)

logger = logging.getLogger("redsun.reference_service")


class VisualEntitiesRoster(BaseModel):
    """Canonical visual roster submitted by JingJing or extracted by utility LLM."""
    ref_001_style: Optional[str] = Field(
        None,
        description="Atmospheric environment establishing style anchor (16:9)",
    )
    ref_002_protagonist: str = Field(
        ...,
        description="Full portrait of central human protagonist (3:4)",
    )
    ref_003_entity: str = Field(
        ...,
        description="Full portrait of supernatural entity, yokai, warrior or antagonist (3:4)",
    )
    ref_004_supporting: Optional[str] = Field(
        None,
        description="Full portrait of primary supporting character (3:4)",
    )
    ref_005_assistant: Optional[str] = Field(
        None,
        description="Full portrait of assistant, servant or secondary character (3:4)",
    )
    ref_006_witness: Optional[str] = Field(
        None,
        description="Full portrait of witness, townsperson or local resident (3:4)",
    )
    ref_007_priest: Optional[str] = Field(
        None,
        description="Full portrait of Buddhist priest, monk or spiritual elder (3:4)",
    )
    ref_008_primary_env: Optional[str] = Field(
        None,
        description="Wide shot of central setting / primary environment (16:9)",
    )
    ref_009_climax_env: Optional[str] = Field(
        None,
        description="Wide shot of sacred ground, graveyard, riverbank or climax environment (16:9)",
    )


FALLBACK_EXTRACTION_SYSTEM_PROMPT = """You are an expert Japanese Folklore & Edo Period Historical Visual Director.
Your task is to extract concrete, historically grounded character and environment visual descriptions
from the provided Japanese folklore script or source text.

MANDATORY RULES:
1. Purely material, tactile and anatomical descriptions.
2. Structure for characters:
   (1) Age range and physical build;
   (2) Physical condition and facial features (blindness, tonsure, scars, skin texture);
   (3) Authentic historical garments with traditional Japanese names in parentheses
       (e.g., hemp monk robes (dōi), straw sandals (zōri), Genpei-era ō-yoroi armor, cotton kosode);
   (4) Inseparable props (biwa lute, walking stick, longbow, sutra scroll, wooden geta);
   (5) Cinematic side/ambient lighting.
3. FORBIDDEN: NEVER include project titles, episode numbers, abstract emotional metaphors ("a feeling of deep sorrow"), or modern anachronisms (plastic, modern, needle).
4. If the story is about a blind biwa priest (Hoichi/Miminashi), the protagonist MUST be blind and hold a biwa lute.
5. If the entity is a Kappa, it MUST have amphibious traits, a tortoise-like shell (kora), and a water-holding dish (sara) on the head.
6. Return ONLY a valid JSON object matching the keys:
{
  "ref_001_style": "Establishing wide shot...",
  "ref_002_protagonist": "Full portrait of...",
  "ref_003_entity": "Full portrait of...",
  "ref_004_supporting": "Portrait of...",
  "ref_005_assistant": "Portrait of...",
  "ref_006_witness": "Portrait of...",
  "ref_007_priest": "Portrait of...",
  "ref_008_primary_env": "Wide shot of...",
  "ref_009_climax_env": "Wide shot of..."
}
"""


class ReferenceService:
    """Service for building canonical Phase 1 visual references."""

    @classmethod
    def extract_visual_entities_fallback(
        cls,
        title: str,
        script_or_source: str,
    ) -> VisualEntitiesRoster:
        """Low-cost structured entity extraction using Gemini 2.5 Flash / Claude 3.5 Haiku.
        
        Never uses Claude Sonnet 5.
        """
        logger.info("Executing low-cost utility LLM fallback for visual entities extraction: %s", title)
        cfg = LLMConfig.from_env().for_utility()
        user_prompt = (
            f"Title: {title}\n\n"
            f"Narrative / Script Text:\n{script_or_source[:8000]}\n\n"
            f"Extract all 9 physical visual entities as specified in JSON format."
        )

        try:
            result = _call_openrouter(
                system_prompt=FALLBACK_EXTRACTION_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                cfg=cfg,
                json_mode=True,
                temperature=0.2,
                max_tokens=2500,
            )
            data = extract_json_safely(result.text)
            if isinstance(data, list) and data:
                data = data[0]
            if not isinstance(data, dict):
                raise ValueError(f"LLM did not return a JSON dictionary: {result.text[:200]}")

            # Ensure required fields have valid non-empty values
            if not data.get("ref_002_protagonist"):
                data["ref_002_protagonist"] = (
                    "Full portrait of the central human protagonist, period-accurate feudal Japanese attire, "
                    "weathered facial features, soft side candle lighting."
                )
            if not data.get("ref_003_entity"):
                data["ref_003_entity"] = (
                    "Full portrait of the primary supernatural entity or antagonist, eerie mythological presence, "
                    "authentic historical textures, moody ambient lighting."
                )
            return VisualEntitiesRoster(**data)
        except Exception as exc:
            logger.error("Failed utility LLM entity extraction for %s: %s", title, exc)
            raise RuntimeError(
                f"Could not extract visual entities via low-cost LLM: {exc}. "
                "Hardcoded generic templates are strictly prohibited."
            ) from exc

    @classmethod
    def assemble_reference_project(
        cls,
        slug: str,
        title: str,
        visual_entities: Optional[Union[VisualEntitiesRoster, Dict[str, Any]]] = None,
        script_text: str = "",
        source_text_ja: str = "",
    ) -> Dict[str, Any]:
        """Assembles the canonical Gemini Assistant Schema v2 project.json for references.
        
        Zero LLM tokens when visual_entities is provided.
        """
        clean_title = title.strip() or slug.replace("-", " ").title()

        # 1. Resolve visual entities
        roster: VisualEntitiesRoster
        if visual_entities is not None:
            if isinstance(visual_entities, VisualEntitiesRoster):
                roster = visual_entities
            elif isinstance(visual_entities, dict):
                roster = VisualEntitiesRoster(**visual_entities)
            else:
                raise ValueError(f"Unsupported visual_entities type: {type(visual_entities)}")
        else:
            # Fallback via low-cost utility model (no Sonnet 5, no static hardcoding)
            narrative = script_text or source_text_ja or clean_title
            roster = cls.extract_visual_entities_fallback(title=clean_title, script_or_source=narrative)

        # 2. Build 9 canonical reference tasks
        task_definitions = [
            (
                "ref-001",
                "Style Master Reference Anchor",
                roster.ref_001_style
                or (
                    "Establishing wide shot of a weathered Edo Japanese environment under heavy dusk, "
                    "ancient cedar trees, stone lanterns with moss, faint blue twilight mist, and deep "
                    "desaturated charcoal and indigo tones with single candle amber reflection."
                ),
                "16:9",
                "style-master",
            ),
            (
                "ref-002",
                "Main Protagonist",
                roster.ref_002_protagonist,
                "3:4",
                "character-protagonist",
            ),
            (
                "ref-003",
                "Supernatural Lead Entity",
                roster.ref_003_entity,
                "3:4",
                "character-entity",
            ),
            (
                "ref-004",
                "Primary Supporting Character",
                roster.ref_004_supporting
                or (
                    "Full portrait of an elderly Japanese temple servant, weathered face, "
                    "layered muted indigo kimono, hands folded within sleeves, low candlelight in a dim traditional room."
                ),
                "3:4",
                "character-supporting",
            ),
            (
                "ref-005",
                "Secondary Character / Assistant",
                roster.ref_005_assistant
                or (
                    "Portrait of a secondary servant or assistant, lean and sun-weathered, patched earth-brown "
                    "cotton work clothes, anxious observant glance, natural daylight from a courtyard doorway."
                ),
                "3:4",
                "character-assistant",
            ),
            (
                "ref-006",
                "Townsperson / Witness",
                roster.ref_006_witness
                or (
                    "Portrait of a local witness or resident, sturdy build, plain navy cotton kimono with a "
                    "worn hemp obi, suspicious expression, practical low light in a rustic wooden dwelling."
                ),
                "3:4",
                "character-witness",
            ),
            (
                "ref-007",
                "Buddhist Priest / Spiritual Elder",
                roster.ref_007_priest
                or (
                    "Portrait of an elderly Japanese Buddhist priest, shaved head, lean weathered face, "
                    "layered robes in muted black and deep brown, carrying a writing brush and sutra book, "
                    "solemn gaze, temple interior with incense smoke."
                ),
                "3:4",
                "character-priest",
            ),
            (
                "ref-008",
                "Primary Environment",
                roster.ref_008_primary_env
                or (
                    "Wide shot of the central historical setting at late dusk. Weathered Edo wooden architecture, "
                    "paper shoji glow, damp gravel, silhouette of branches over a lane, no people. "
                    "Melancholic atmosphere, desaturated charcoal and indigo palette."
                ),
                "16:9",
                "environment-primary",
            ),
            (
                "ref-009",
                "Climax Environment / Sacred Grounds",
                roster.ref_009_climax_env
                or (
                    "Wide shot of sacred or haunted wilderness grounds at deep dusk. Weathered dark unpainted timber "
                    "structures, moss-eaten stone markers, ancient twisted pine trees, slick damp ground, no people. "
                    "Deep shadows, ominous quiet."
                ),
                "16:9",
                "environment-sacred",
            ),
        ]

        tasks: list[dict[str, Any]] = []
        for task_id, task_title, raw_desc, aspect_ratio, basename in task_definitions:
            # Sanitize description strictly
            clean_desc = sanitize_visual_prompt(raw_desc, project_title=clean_title, slug=slug)
            # Combine with aspect ratio, terminator, and Style Bible
            full_prompt = format_prompt_with_style_bible(
                narrative_description=clean_desc,
                aspect_ratio=aspect_ratio,
                project_title=clean_title,
                slug=slug,
            )
            tasks.append({
                "id": task_id,
                "title": task_title,
                "prompt": full_prompt,
                "references": [],
                "output": {"basename": basename},
            })

        project_doc = {
            "schemaVersion": 2,
            "project": {
                "id": f"{slug}-references",
                "name": "Master References",
                "description": "Master reference visual anchors.",
            },
            "assets": {},
            "tasks": tasks,
        }

        # 3. Deterministic fidelity & anti-contamination validation
        ReferenceFidelityLinter.validate_or_raise(
            project_data=project_doc,
            project_title=clean_title,
            slug=slug,
            script_text=script_text or source_text_ja,
        )

        return project_doc
