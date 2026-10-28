"""Pydantic schemas for the Red Sun Japan pipeline."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from pydantic import BaseModel, ConfigDict, Field, model_validator

from redsun.services.reference_service import VisualEntitiesRoster


class ProjectCreate(BaseModel):
    """Input shape for ``POST /projects``."""

    title: str | None = Field(None, min_length=1, max_length=200)
    title_en: str | None = Field(None, description="English title")
    title_ja: str | None = Field(
        None,
        description="Optional Japanese title (e.g. 雪女). Displayed on-screen.",
    )
    channel_name: str = "Red Sun Japan"
    source_text_ja: str | None = Field(
        "",
        description=(
            "Raw Japanese source text fetched by the agent from "
            "Nichibanken / NDL / Aozora Bunko."
        ),
    )
    script_en: str | None = Field(
        None,
        description="Optional pre-existing English script provided directly by curator.",
    )
    visual_entities: VisualEntitiesRoster | None = Field(
        None,
        description="Deterministic visual entities roster curated by JingJing.",
    )
    source_url: str | None = Field(
        None,
        description="Where the source_text was obtained.",
    )
    theme_source_url: str | None = Field(
        None,
        description="Synonym for source_url from Studio Pipeline frontend.",
    )
    target_keywords: str | None = Field(
        None,
        description="Comma-separated SEO keywords (for Phase 3).",
    )
    output_languages: str = Field(
        "en",
        description=(
            "Comma-separated language codes to produce. "
            'Defaults to "en". Forward-compatible with multi-language.'
        ),
    )
    source_type: str | None = Field(
        None,
        description=(
            "Source classification for the review agent. "
            "Known: classical_kaidan, showa_legend, documented_case, "
            "regional_yokai, unresolved_mystery. Optional — defaults to "
            "'classical_kaidan' if not set."
        ),
    )
    atmosphere: str | None = Field(
        None,
        description=(
            "Tone/atmosphere target for review and visual generation. "
            "Optional — defaults to 'dark and atmospheric' if not set."
        ),
    )
    image_generation_mode: str = Field(
        "assistant",
        description="Image generation mode: 'assistant' (Chrome extension) or 'api' (Autonomous Meta Muse Image 1.0).",
    )
    content_rating: str = Field(
        "pg13_prestige",
        description="Content rating standard (default: 'pg13_prestige').",
    )

    @model_validator(mode="before")
    @classmethod
    def reconcile_titles(cls, data: Any) -> Any:
        if isinstance(data, dict):
            t = data.get("title")
            te = data.get("title_en")
            if not t and te:
                data["title"] = te
            elif t and not te:
                data["title_en"] = t
        return data


class ProjectResponse(BaseModel):
    id: int
    title: str
    title_ja: str | None
    slug: str | None
    status: str
    source_language: str
    output_languages: str
    error_message: str | None
    source_type: str | None = None
    atmosphere: str | None = None
    image_generation_mode: str = "assistant"
    content_rating: str = "pg13_prestige"
    created_at: datetime | None = None
    updated_at: datetime | None = None
    variants: list[dict] | None = None

    model_config = ConfigDict(from_attributes=True)


class LLMScriptRequest(BaseModel):
    """Input shape for ``POST /projects/{id}/script``."""

    source_text_ja: str | None = Field(
        None,
        description="Japanese source text (overrides project.source_text if set)",
    )
    language_code: str = Field("en", min_length=2, max_length=10)
    content_rating: str | None = Field(
        None,
        description="Optional content rating override (e.g. 'pg13_prestige')",
    )


class LLMScriptResponse(BaseModel):
    project_id: int
    language_code: str
    script: str
    model: str
    elapsed_sec: float
