"""Phase 1 FastAPI server — backend scaffolding only.

What works in this commit:

- ``POST /projects`` — create a new ``VideoProject`` row from a topic + source
  text. Returns 201 with the project id.
- ``GET /projects`` — list every project (most-recent first).
- ``GET /projects/{id}`` — single-project detail.
- ``GET /healthz`` — operator smoke probe (no DB hit; useful for Coolify).
- ``POST /assets/fetch`` — smoke-call into the scraper; returns the list of
  persisted ``AssetImage`` rows. Real call against Wikimedia, real DB write.

What does NOT work yet (deliberately):

- Remotion render orchestration. Lands in Phase 5.

Running locally:

    source motor-python/.venv/bin/activate
    pip install -r motor-python/requirements.txt
    cp motor-python/.env.example motor-python/.env   # then fill in values
    uvicorn motor_python.main:app --reload --port 8000
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# Force project venv to take precedence over Hermes venv
_THIS_DIR = Path(__file__).resolve().parent
_VENV_SITE = _THIS_DIR / ".venv" / "lib" / "python3.11" / "site-packages"
if _VENV_SITE.exists() and str(_VENV_SITE) not in sys.path:
    sys.path.insert(0, str(_VENV_SITE))

import logging
import json
import re
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from contextlib import asynccontextmanager
from dataclasses import field
from typing import Any, Dict, List, Optional

from redsun.workspace import ProjectWorkspace

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, UploadFile, File, Query, Body, BackgroundTasks
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, model_validator, ConfigDict
from sqlalchemy.orm import Session

from redsun.services.reference_fidelity_linter import (
    PromptContaminationError,
    ReferenceFidelityError,
    ReferenceFidelityLinter,
)
from redsun.services.reference_service import (
    ReferenceService,
    VisualEntitiesRoster,
)

from database import (
    AssetImage,
    SessionLocal,
    TranslationVariant,
    VideoProject,
    VideoStatus,
    get_db,
    init_schema,
)
from scraper import fetch_assets as scraper_fetch_assets
from tts_engine import (
    generate_tts as tts_generate,
    list_speakers as tts_list_speakers,
)
from whisper_engine import transcribe_audio as whisper_transcribe
from llm_client import (
    LLMConfig,
    generate_blog_article_from_script,
    generate_script_from_sources,
    identify_short_clips,
)
from redsun.schemas.gemini import (
    GeminiProjectMeta,
    GeminiAsset,
    GeminiTaskOutput,
    GeminiReferenceTask,
    GeminiReferenceProject,
    GeminiSceneTask,
    GeminiSceneProject,
    STYLE_CUE_TERMINATOR,
)
from redsun.services import (
    MASTER_STYLE_BIBLE,
    clean_narrative_text,
    format_prompt_with_style_bible,
    sanitize_visual_prompt,
    VisualPromptLinter,
    calculate_dynamic_scene_count,
    slice_narrative_into_beats,
    slice_narrative_into_20_beats,
    generate_narrative_scene_prompts,
    infer_asset_type,
    bind_entities_to_scene,
    inject_physical_anchors,
    MissingCharacterReferenceError,
    cleanup_and_unify_project_storage,
    run_autonomous_generation,
    get_generation_status,
    cancel_generation,
    regenerate_single_frame,
    reset_generation_status,
)

# Load .env once at import. Safe even if the file is missing.
load_dotenv()

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("redsun.main")

# Real-time job tracking across pipeline nodes (Node 1, Node 2)
ACTIVE_VOICE_JOBS: dict[str, dict[str, Any]] = {}
ACTIVE_SCRIPT_JOBS: dict[str, dict[str, Any]] = {}
_JOB_LOCK = threading.Lock()

RUNTIME_ROOT = Path(
    os.environ.get(
        "REDSUN_RUNTIME_ROOT",
        "/Users/isamumatsuyama/Documents/red-sun-japan-runtime/projects",
    )
)


# ─────────────────────────────────────────────────────────────────────────────
# Lifespan
# ─────────────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing database schema…")
    init_schema()
    logger.info("Red Sun Japan backend ready.")
    yield
    logger.info("Shutting down Red Sun Japan backend.")


app = FastAPI(
    title="Red Sun Japan — Video Automation Engine",
    version="0.1.0-phase1",
    description=(
        "Phase 1: schema + project CRUD + asset index. "
        "TTS pipeline lands in Phase 2."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:5175",
        "http://localhost:4173",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
        "http://127.0.0.1:5175",
        "http://127.0.0.1:4173",
    ],
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic schemas
# ─────────────────────────────────────────────────────────────────────────────
_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def _to_slug(title: str) -> str:
    """Lightweight slug for blog/YAML paths. Phase 6 will tighten this."""
    s = _SLUG_STRIP.sub("-", title.lower()).strip("-")
    return s[:80] or "untitled"


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


class ImageModeRequest(BaseModel):
    mode: str = Field(..., pattern="^(assistant|api)$")


class SingleFrameRegenerateRequest(BaseModel):
    identifier: str = Field(..., description="Filename or task ID to regenerate")


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

    class Config:
        from_attributes = True


class PromptRevisionPayload(BaseModel):
    instruction: str
    target_task_id: str | None = None
    scope: str | None = None  # "references" | "scenes" | None


PromptRevisionPayload.model_rebuild()


class ThumbnailRegenerateRequest(BaseModel):
    source_image_path: str | None = None
    kicker: str | None = None
    headline_line1: str | None = None
    headline_highlight: str | None = None


ThumbnailRegenerateRequest.model_rebuild()


class ProjectDetailResponse(ProjectResponse):
    files: list[str] = []
    error_logs: list[str] = []
    pipeline_summary: dict | None = None


class AssetSummary(BaseModel):
    id: int
    theme: str
    relative_path: str
    source_name: str
    source_url: str
    license: str
    description: str | None
    width: int | None
    height: int | None

    class Config:
        from_attributes = True


class AssetFetchRequest(BaseModel):
    theme: str = Field(..., min_length=1, max_length=80)
    query: str = Field(..., min_length=1)
    limit: int = Field(5, ge=1, le=20)


class AudioGenerateRequest(BaseModel):
    """Input shape for ``POST /projects/{id}/audio``."""

    script: str | None = Field(None, min_length=1, max_length=20000)
    language_code: str = Field("en", min_length=2, max_length=10)
    speaker: str | None = Field(None, description="CustomVoice/TTS speaker or voice_id")
    instruct: str | None = Field(None, description="Style instruction text")
    provider: str | None = Field(None, description="TTS provider override: 'kokoro' or 'minimax'")
    speed: float | None = Field(None, description="Speech speed multiplier (e.g. 0.95)")


class AudioJobResponse(BaseModel):
    project_id: int
    language_code: str
    audio_path: str
    sample_rate: int
    duration_sec: float
    chunk_count: int
    speaker: str
    language: str
    model_id: str


class AudioRegenerateResponse(BaseModel):
    status: str
    slug: str
    message: str
    provider: str
    speaker: str
    speed: float
    duration_seconds: float
    duration_formatted: str
    wav_path: str
    wav_size_bytes: int
    wav_size_formatted: str
    subtitles_count: int
    subtitles_path: str
    elapsed_seconds: float


class LLMScriptRequest(BaseModel):
    """Input shape for ``POST /projects/{id}/script``."""

    source_text_ja: str | None = Field(
        None,
        description="Japanese source text (overrides project.source_text if set)",
    )
    language_code: str = Field("en", min_length=2, max_length=10)


class LLMScriptResponse(BaseModel):
    project_id: int
    language_code: str
    script: str
    model: str
    elapsed_sec: float


class LLMReviewRequest(BaseModel):
    language_code: str = Field("en", min_length=2, max_length=10)
    source_type: str | None = Field(
        None,
        description=(
            "Override source classification for the review agent. "
            "If omitted, uses the project's stored value or a sensible default. "
            "Known values: classical_kaidan, showa_legend, documented_case, "
            "regional_yokai, unresolved_mystery."
        ),
    )
    atmosphere: str | None = Field(
        None,
        description=(
            "Override tone/atmosphere target for the review agent. "
            "If omitted, uses the project's stored value or a sensible default."
        ),
    )


class LLMReviewResponse(BaseModel):
    project_id: int
    language_code: str
    approved: bool
    score: int
    issues: list[dict]
    suggested_edits: list[dict] | None = None
    revised_script: str | None = None
    model: str
    elapsed_sec: float


class LLMBlogRequest(BaseModel):
    """Input shape for ``POST /projects/{id}/blog``."""

    language_code: str = Field("en", min_length=2, max_length=10)


class LLMBlogResponse(BaseModel):
    project_id: int
    language_code: str
    blog_article: str
    model: str
    elapsed_sec: float


class LLMShortsRequest(BaseModel):
    """Input shape for ``POST /projects/{id}/shorts``."""

    language_code: str = Field("en", min_length=2, max_length=10)
    max_clips: int = Field(5, ge=1, le=10)


class LLMShortsResponse(BaseModel):
    project_id: int
    language_code: str
    clips: list[dict]
    pacing_notes: str | None = None
    model: str
    elapsed_sec: float


class TranscribeRequest(BaseModel):
    """Input shape for ``POST /projects/{id}/transcribe``."""

    language_code: str = Field("en", min_length=2, max_length=10)
    # Optional: override language auto-detect. Faster-whisper supports
    # ISO codes ("en", "ja", "pt-BR", etc.).
    language: str | None = Field(
        None,
        description="Optional ISO code; defaults to env WHISPER_LANGUAGE or auto-detect.",
    )


class TranscribeResponse(BaseModel):
    project_id: int
    language_code: str
    timestamps_path: str
    word_count: int
    segment_count: int
    duration_sec: float
    elapsed_sec: float
    model_size: str
    language: str


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints — health
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/healthz")
def healthz() -> dict:
    """Liveness probe. No DB hit, no side effects. For Coolify / k8s."""
    return {
        "status": "ok",
        "version": "0.1.0-phase1",
        "tts_configured": bool(os.environ.get("QWEN_TTS_MODEL")),
        "llm_configured": bool(os.environ.get("OPENROUTER_API_KEY")),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints — projects
# ─────────────────────────────────────────────────────────────────────────────
@app.post("/projects", response_model=ProjectResponse, status_code=201)
def create_project(
    payload: ProjectCreate,
    db: Session = Depends(get_db),
) -> ProjectResponse:
    """Create a new video project in PENDING state.

    On success, materialize the canonical `projects/<slug>/` workspace
    via `ProjectWorkspace.open_or_create(slug)` and write a
    `project.json` file with the new row id so that the wired
    production pipeline can find the DB row from the workspace.
    """
    proj_title = payload.title or payload.title_en or "Untitled"
    visual_entities_dict = payload.visual_entities.model_dump() if payload.visual_entities else None
    visual_entities_raw = json.dumps(visual_entities_dict, indent=2, ensure_ascii=False) if visual_entities_dict else None

    project = VideoProject(
        channel_name=payload.channel_name or os.environ.get("BRAND_NAME", "Red Sun Japan"),
        title=proj_title,
        title_ja=payload.title_ja,
        slug=_to_slug(proj_title),
        status=VideoStatus.PENDING,
        source_language="ja",
        source_text=payload.source_text_ja or None,
        theme_source_url=payload.theme_source_url or payload.source_url,
        target_keywords=payload.target_keywords,
        output_languages=payload.output_languages,
        source_type=payload.source_type,
        atmosphere=payload.atmosphere,
        selected_bgm=None,
        image_generation_mode=payload.image_generation_mode or "assistant",
        content_rating=payload.content_rating or "pg13_prestige",
        error_message=None,
        visual_entities_json=visual_entities_raw,
    )
    db.add(project)
    db.commit()
    db.refresh(project)

    # Auto-create a translation variant row with explicit nulls for each output language
    for lang in [s.strip() for s in payload.output_languages.split(",") if s.strip()]:
        variant = TranslationVariant(
            project_id=project.id,
            language_code=lang,
            script=payload.script_en if lang == "en" and payload.script_en else None,
            blog_article=None,
            short_clips_json=None,
            audio_path=None,
            timestamps_path=None,
            output_video_path=None,
        )
        db.add(variant)
    db.commit()

    # Phase 1: Materialize fresh isolated workspace for the new project
    try:
        from redsun.workspace import ProjectWorkspace, WorkspaceError, RUNTIME_ROOT, get_project_folder_name

        # Ensure clean pristine folder structure using canonical numbered format (e.g. 001_slug)
        folder_name = get_project_folder_name(project.id, project.slug)
        project_root = Path(RUNTIME_ROOT) / folder_name
        for sub in (
            "gemini-assistant/references",
            "gemini-assistant/output",
            "gemini-assistant/ref-job",
            "distribution/pins_media",
            "shorts",
            "video",
            "audio",
            "checkpoints",
            "text",
            "blog",
            "images/prompts",
            "images/references",
            "images/final",
            "post_kit",
            "book",
        ):
            os.makedirs(project_root / sub, exist_ok=True)

        ws, mode = ProjectWorkspace.open_or_create(project.slug, project_id=project.id)
        ws.project_json_path.write_text(
            json.dumps(
                {
                    "technical_slug": project.slug,
                    "title": project.title,
                    "title_ja": project.title_ja,
                    "project_id": project.id,
                    "output_languages": payload.output_languages,
                    "source_type": payload.source_type,
                    "atmosphere": payload.atmosphere,
                    "content_rating": payload.content_rating or "pg13_prestige",
                    "materialized_at": datetime.now(timezone.utc).isoformat(),
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        # Canonical manifest: projects/{folder_name}/visual_entities.json
        if visual_entities_raw:
            (project_root / "visual_entities.json").write_text(visual_entities_raw, encoding="utf-8")
            (ws.root / "visual_entities.json").write_text(visual_entities_raw, encoding="utf-8")
            # Build and persist reference project deterministically ($0 LLM token cost)
            ref_data = ReferenceService.assemble_reference_project(
                slug=project.slug,
                title=project.title,
                visual_entities=payload.visual_entities,
                script_text=payload.script_en or "",
                source_text_ja=payload.source_text_ja or "",
            )
            _save_reference_project(ws, ref_data)
            logger.info("Deterministically assembled Phase 1 references for %s ($0 LLM tokens)", project.slug)

        logger.info(
            "Materialized canonical pristine workspace for %s (mode=%s) at %s",
            project.slug, mode, ws.root,
        )
    except WorkspaceError as exc:
        logger.warning("workspace materialization skipped: %s", exc)

    logger.info(
        "Created project #%s '%s' (slug=%s, langs=%s)",
        project.id, project.title, project.slug, payload.output_languages,
    )
    return ProjectResponse.model_validate(project)


def ensure_project_tree(slug: str, project_id: Optional[int] = None) -> "ProjectWorkspace":
    """Ensure directory tree exists under RUNTIME_ROOT / {folder_name}:
    text/, audio/, images/prompts/, images/references/, images/final/, post_kit/, video/, book/
    and sync any existing assets from legacy subdirectories.
    """
    import shutil
    from redsun.workspace import ProjectWorkspace, RUNTIME_ROOT

    ws = ProjectWorkspace.from_slug(slug, project_id=project_id)
    ws.create()

    # Ensure all required runtime subdirectories are created under ws.root
    project_root = ws.root
    for sub in (
        "text",
        "audio",
        "images/prompts",
        "gemini-assistant/references",
        "gemini-assistant/output",
        "gemini-assistant/ref-job",
        "post_kit",
        "video",
        "book",
    ):
        os.makedirs(project_root / sub, exist_ok=True)

    # Migrate legacy directories (01-references, 02-scenes, reference-generation) to unified tree
    try:
        legacy_dirs = [
            ws.gemini_assistant_dir / "01-references",
            ws.gemini_assistant_dir / "02-scenes",
            ws.gemini_assistant_dir / "reference-generation",
        ]
        valid_img_exts = (".png", ".jpg", ".jpeg", ".webp")
        for ldir in legacy_dirs:
            if ldir.exists() and ldir.is_dir():
                for root, _, files in os.walk(ldir):
                    for f in files:
                        if f.lower().endswith(valid_img_exts):
                            src_img = Path(root) / f
                            if "output" in str(src_img) and "scenes" in str(src_img):
                                dst = ws.gemini_output_dir / f
                            else:
                                dst = ws.gemini_references_dir / f
                            if not dst.exists():
                                shutil.copy2(src_img, dst)
                l_pjson = ldir / "project.json"
                if l_pjson.exists():
                    if "02-scenes" in ldir.name:
                        if not ws.gemini_project_json.exists():
                            shutil.copy2(l_pjson, ws.gemini_project_json)
                    else:
                        if not ws.gemini_ref_job_project_json.exists():
                            shutil.copy2(l_pjson, ws.gemini_ref_job_project_json)
                shutil.rmtree(ldir, ignore_errors=True)
    except Exception as exc:
        logger.debug("ensure_project_tree legacy cleanup note for %s: %s", slug, exc)

    # Sync project.json between images/prompts and unified gemini-assistant
    try:
        ref_source = None
        for p in (ws.gemini_ref_job_project_json, ws.references_json_path):
            if p.exists():
                ref_source = p
                break
        if ref_source:
            content = ref_source.read_text(encoding="utf-8")
            for p in (ws.gemini_ref_job_project_json, ws.references_json_path):
                if not p.exists():
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(content, encoding="utf-8")

        scene_source = None
        for p in (ws.gemini_project_json, ws.final_scenes_json_path):
            if p.exists():
                scene_source = p
                break
        if scene_source:
            content = scene_source.read_text(encoding="utf-8")
            for p in (ws.gemini_project_json, ws.final_scenes_json_path):
                if not p.exists():
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(content, encoding="utf-8")
    except Exception as exc:
        logger.debug("ensure_project_tree gemini json sync note for %s: %s", slug, exc)

    # Initialize job.json lifecycle files if not present
    if not ws.gemini_ref_job_job_json.exists():
        _write_job_json(ws.gemini_ref_job_job_json, "references", "ready")
    if not ws.gemini_job_json.exists():
        _write_job_json(ws.gemini_job_json, "scenes", "preparing")

    return ws


VALID_ASSET_TYPES = {"character", "environment", "style", "object", "other"}


def _read_job_json(path: Path, default_stage: str, default_status: str) -> dict:
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "stage" in data and "status" in data:
                return data
        except Exception:
            pass
    return {"stage": default_stage, "status": default_status}


def _write_job_json(path: Path, stage: str, status: str) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"stage": stage, "status": status}
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return data



def _clean_and_format_prompt(
    raw_prompt: str,
    aspect_ratio: str = "16:9",
    project_title: str = "",
    slug: str = "",
) -> str:
    """Deterministically formats prompt with narrative core and official Master Style Bible."""
    sanitized = raw_prompt
    if project_title or slug:
        sanitized = sanitize_visual_prompt(raw_prompt, project_title=project_title, slug=slug)
    return format_prompt_with_style_bible(
        sanitized,
        aspect_ratio=aspect_ratio,
        project_title=project_title,
        slug=slug,
    )


def _normalize_reference_project(data: dict, slug: str, title: str = "") -> dict:
    """Ensure data strictly conforms to GeminiReferenceProject, stripping invalid keys."""
    clean_title = title or slug.replace("-", " ").title()
    proj_id = f"{slug}-references"
    proj_name = f"{clean_title} — Master References"
    proj_desc = f"Master reference visual anchors for {clean_title}."

    if isinstance(data.get("project"), dict):
        proj_dict = data["project"]
        p_id = proj_dict.get("id")
        if p_id:
            proj_id = p_id if p_id.endswith("-references") else f"{p_id}-references"
        proj_name = proj_dict.get("name") or proj_name
        proj_desc = proj_dict.get("description") or proj_desc

    clean_tasks = []
    for i, t in enumerate(data.get("tasks", []), 1):
        task_id = t.get("id") or f"ref-{i:03d}"
        if not task_id.startswith("ref-"):
            task_id = f"ref-{i:03d}"
        t_title = t.get("title") or f"Reference Anchor {i:03d}"
        t_title = sanitize_visual_prompt(t_title, project_title=clean_title, slug=slug)
        base = t.get("output", {}).get("basename") or task_id

        # Determine aspect ratio based on whether task is a character portrait or environment/style
        is_portrait = any(
            k in task_id.lower() or k in t_title.lower() or k in str(base).lower()
            for k in (
                "character", "portrait", "person", "protagonist", "lead", "figure",
                "woman", "man", "priest", "monk", "samurai", "ghost", "yokai",
                "creature", "spirit", "entity", "merchant", "servant", "witness",
                "villager", "fisherman", "shinzaburo", "otsuyu", "yone", "tomozo", "omine",
                "kappa"
            )
        ) and not any(
            k in task_id.lower() or k in t_title.lower() or k in str(base).lower()
            for k in ("style", "environment", "lane", "temple", "landscape", "establishing", "background", "setting", "shrine", "river", "marsh")
        )
        task_ar = "3:4" if is_portrait else "16:9"
        prompt = _clean_and_format_prompt(
            t.get("prompt", ""),
            aspect_ratio=task_ar,
            project_title=clean_title,
            slug=slug,
        )

        clean_tasks.append(
            GeminiReferenceTask(
                id=task_id,
                title=t_title,
                prompt=prompt,
                references=[],  # ALWAYS empty in Phase A!
                output=GeminiTaskOutput(basename=base),
            )
        )

    doc = GeminiReferenceProject(
        schemaVersion=2,
        project=GeminiProjectMeta(id=proj_id, name=proj_name, description=proj_desc),
        assets={},
        tasks=clean_tasks,
    )
    return doc.model_dump()


def _normalize_scene_project(data: dict, slug: str, title: str = "") -> dict:
    """Ensure data strictly conforms to GeminiSceneProject, stripping invalid keys."""
    clean_title = title or slug.replace("-", " ").title()
    proj_id = slug
    proj_name = clean_title
    proj_desc = f"Long-form visual narrative shots for {clean_title}."

    if isinstance(data.get("project"), dict):
        proj_dict = data["project"]
        proj_id = proj_dict.get("id") or proj_id
        proj_name = proj_dict.get("name") or proj_name
        proj_desc = proj_dict.get("description") or proj_desc

    assets_raw = data.get("assets", {})
    clean_assets = {}
    for aid, ainfo in assets_raw.items():
        if isinstance(ainfo, dict):
            atype = infer_asset_type(aid, ainfo.get("type"))
            afile = ainfo.get("file", f"references/{aid}.jpeg")
            lbl = ainfo.get("label", aid)
            lbl = sanitize_visual_prompt(lbl, project_title=clean_title, slug=slug)
            clean_assets[aid] = GeminiAsset(
                label=lbl or aid,
                type=atype,
                file=afile,
                prompt=ainfo.get("prompt"),
            )

    valid_asset_keys = set(clean_assets.keys())
    clean_tasks = []
    for i, t in enumerate(data.get("tasks", []), 1):
        task_id = t.get("id") or f"shot-{i:03d}"
        t_title = t.get("title") or f"Shot {i:03d}"
        t_title = sanitize_visual_prompt(t_title, project_title=clean_title, slug=slug)
        raw_prompt = t.get("prompt", "")

        raw_refs = t.get("references", [])
        bound_refs = bind_entities_to_scene(
            prompt=raw_prompt,
            title=t_title,
            available_assets=clean_assets,
            current_refs=raw_refs,
            max_refs=3,
        )

        parts = raw_prompt.split("\n\n")
        anchored_core = inject_physical_anchors(parts[0], bound_refs)
        anchored_core = sanitize_visual_prompt(anchored_core, project_title=clean_title, slug=slug)
        prompt = format_prompt_with_style_bible(
            anchored_core,
            aspect_ratio="16:9",
            project_title=clean_title,
            slug=slug,
        )

        base = t.get("output", {}).get("basename") or f"shot_{i:03d}"
        clean_tasks.append(
            GeminiSceneTask(
                id=task_id,
                title=t_title,
                prompt=prompt,
                references=bound_refs,
                output=GeminiTaskOutput(basename=base),
            )
        )

    doc = GeminiSceneProject(
        schemaVersion=2,
        project=GeminiProjectMeta(id=proj_id, name=proj_name, description=proj_desc),
        assets=clean_assets,
        tasks=clean_tasks,
    )
    return doc.model_dump()


def _save_reference_project(ws: "ProjectWorkspace", data: dict) -> None:
    norm_data = _normalize_reference_project(data, slug=ws.technical_slug)
    content = json.dumps(norm_data, indent=2, ensure_ascii=False)
    for p in (
        ws.gemini_ref_job_project_json,
        ws.references_json_path,
    ):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


def _save_scene_project(ws: "ProjectWorkspace", data: dict) -> None:
    norm_data = _normalize_scene_project(data, slug=ws.technical_slug)
    content = json.dumps(norm_data, indent=2, ensure_ascii=False)
    for p in (ws.gemini_project_json, ws.final_scenes_json_path):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


def _build_default_references(
    slug: str,
    title: str = "",
    visual_entities: Optional[VisualEntitiesRoster | dict] = None,
    script_text: str = "",
    source_text_ja: str = "",
) -> dict:
    """Builds canonical references via ReferenceService.
    Eliminates all hardcoded templates. Enforces 0-token deterministic assembly
    when visual_entities is provided, or low-cost utility LLM fallback when absent.
    """
    from redsun.workspace import ProjectWorkspace
    if visual_entities is None:
        try:
            ws = ProjectWorkspace.from_slug(slug)
            ve_file = ws.root / "visual_entities.json"
            if ve_file.exists():
                visual_entities = json.loads(ve_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    return ReferenceService.assemble_reference_project(
        slug=slug,
        title=title,
        visual_entities=visual_entities,
        script_text=script_text,
        source_text_ja=source_text_ja,
    )



def _build_final_scenes(ws: "ProjectWorkspace", row: Optional[VideoProject] = None) -> dict:
    slug = ws.technical_slug
    title = row.title if row else slug.replace("-", " ").title()

    # Pre-build asset catalog from available reference project files
    ref_tasks = []
    for p in (ws.gemini_ref_job_project_json, ws.references_json_path):
        if p.exists():
            try:
                rdata = json.loads(p.read_text(encoding="utf-8"))
                ref_tasks = rdata.get("tasks", [])
                if ref_tasks:
                    break
            except Exception:
                pass
    if not ref_tasks:
        ref_tasks = _build_default_references(slug, title).get("tasks", [])

    assets: dict[str, GeminiAsset] = {}
    for rt in ref_tasks:
        rbase = rt.get("output", {}).get("basename") or rt.get("id")
        asset_type = infer_asset_type(rbase, rt.get("type"))

        # Check existing files on disk, prefer .jpeg / .png / etc.
        ext = ".jpeg"
        for candidate_ext in (".jpeg", ".jpg", ".png", ".webp"):
            if (ws.gemini_references_dir / f"{rbase}{candidate_ext}").exists():
                ext = candidate_ext
                break

        lbl = rt.get("title", rbase)
        lbl = sanitize_visual_prompt(lbl, project_title=title, slug=slug)
        assets[rbase] = GeminiAsset(
            label=lbl or rbase,
            type=asset_type,
            file=f"references/{rbase}{ext}",
            prompt=None,
        )

    valid_asset_keys = set(assets.keys())

    legacy_proj = ws.root / "sonnet5-v1" / "project.json"
    tasks: list[GeminiSceneTask] = []
    if legacy_proj.exists():
        try:
            legacy_data = json.loads(legacy_proj.read_text(encoding="utf-8"))
            candidate_tasks: list[GeminiSceneTask] = []
            for i, t in enumerate(legacy_data.get("tasks", []), 1):
                basename = t.get("output", {}).get("basename", f"shot_{i:03d}")
                prompt = _clean_and_format_prompt(t.get("prompt", ""), aspect_ratio="16:9")

                raw_refs = t.get("references", ["style-master"])
                filtered_refs = [r for r in raw_refs if r in valid_asset_keys]
                candidate_tasks.append(
                    GeminiSceneTask(
                        id=f"shot-{i:03d}",
                        title=t.get("title", f"Shot {i:03d}"),
                        prompt=prompt,
                        references=filtered_refs,
                        output=GeminiTaskOutput(basename=basename),
                    )
                )
            # Calculate required scenes based on audio length
            audio_candidate = ws.audio_dir / "audio_main.wav"
            if not audio_candidate.exists():
                audio_candidate = ws.audio_main_path
            audio_dur = 0.0
            if audio_candidate.exists():
                try:
                    import wave
                    with wave.open(str(audio_candidate), "rb") as w:
                        r = w.getframerate()
                        f = w.getnframes()
                        if r and f:
                            audio_dur = f / r
                except Exception:
                    pass
            required_scenes = calculate_dynamic_scene_count(audio_dur) if audio_dur > 0 else 25

            # Verify candidate tasks with VisualPromptLinter to reject duplicates/generic phrases
            if len(candidate_tasks) >= required_scenes:
                linter = VisualPromptLinter(similarity_threshold=0.50, min_detail_ratio=0.30)
                is_valid, _ = linter.validate([
                    {"scene_index": i + 1, "description": ct.prompt}
                    for i, ct in enumerate(candidate_tasks)
                ])
                if is_valid:
                    tasks = candidate_tasks
        except Exception as exc:
            logger.warning("Failed parsing legacy project.json: %s", exc)

    if not tasks:
        # Extract narrative script text from database or workspace files
        script_text = ""
        if row:
            try:
                from database import SessionLocal, TranslationVariant
                with SessionLocal() as db_sess:
                    variant = (
                        db_sess.query(TranslationVariant)
                        .filter(
                            TranslationVariant.project_id == row.id,
                            TranslationVariant.language_code.in_(["en", "ja"]),
                        )
                        .first()
                    )
                    if variant and variant.script:
                        script_text = str(variant.script)
            except Exception as exc:
                logger.debug("Could not extract script from DB for %s: %s", slug, exc)

        if not script_text:
            for p in (
                ws.root / "text" / "script_en.md",
                ws.script_path,
                ws.root / "text" / "script.md",
                ws.script_reviewed_path,
            ):
                if p.exists():
                    try:
                        content = p.read_text(encoding="utf-8")
                        if content.strip():
                            script_text = content
                            break
                    except Exception:
                        pass

        # Locate word-level transcript or subtitles file
        transcript_path = None
        for tp in (
            ws.root / "text" / "transcript.json",
            ws.transcript_path,
            ws.root / "transcript.json",
        ):
            if tp.exists():
                transcript_path = tp
                break

        subtitles_path = None
        for sp in (
            ws.root / "text" / "subtitles_en.json",
            ws.root / "audio" / "subtitles_en.json",
            ws.root / "subtitles_en.json",
        ):
            if sp.exists():
                subtitles_path = sp
                break

        audio_candidate = ws.audio_dir / "audio_main.wav"
        if not audio_candidate.exists():
            audio_candidate = ws.audio_main_path

        beats = slice_narrative_into_beats(
            script_text=script_text,
            transcript_path=transcript_path,
            subtitles_path=subtitles_path,
            audio_path=audio_candidate if audio_candidate.exists() else None,
        )

        try:
            tasks = generate_narrative_scene_prompts(
                slug=slug,
                title=title,
                narrative_beats=beats,
                reference_assets=assets,
            )
        except Exception as exc:
            logger.error("Failed generating narrative scene prompts with LLM: %s. Using heuristic generator.", exc)
            from redsun.services.image_prompt_generator import (
                _heuristic_narrative_scene_generator,
                _apply_programmatic_diversity_fixes,
            )
            h_scenes = _heuristic_narrative_scene_generator(title, beats, assets)
            linter = VisualPromptLinter()
            valid, errs = linter.validate(h_scenes)
            if not valid:
                h_scenes = _apply_programmatic_diversity_fixes(h_scenes, errs, beats, title)

            tasks = []
            for i, sc in enumerate(h_scenes, 1):
                raw_desc = sc.get("description", "")
                scale = sc.get("scale", "")
                scale_prefix = f"[{scale}] " if scale and not raw_desc.startswith("[") else ""
                prompt = format_prompt_with_style_bible(f"{scale_prefix}{raw_desc}", aspect_ratio="16:9")
                raw_refs = sc.get("references", ["style-master"])
                filtered_refs = [r for r in raw_refs if r in valid_asset_keys]
                if "style-master" in valid_asset_keys and "style-master" not in filtered_refs:
                    filtered_refs.insert(0, "style-master")
                tasks.append(
                    GeminiSceneTask(
                        id=f"shot-{i:03d}",
                        title=sc.get("title", f"Shot {i:02d}"),
                        prompt=prompt,
                        references=filtered_refs,
                        output=GeminiTaskOutput(basename=f"shot_{i:03d}"),
                    )
                )

    # Ensure entity binding, sanitization, and physical anchors are deterministically applied to all final tasks
    for i, t in enumerate(tasks, 1):
        t.title = sanitize_visual_prompt(t.title, project_title=title, slug=slug)
        t.prompt = sanitize_visual_prompt(t.prompt, project_title=title, slug=slug)
        b_narr = beats[i - 1].get("narration", "") if beats and i - 1 < len(beats) else ""
        bound_refs = bind_entities_to_scene(
            prompt=t.prompt,
            title=t.title,
            narration=b_narr,
            available_assets=assets,
            current_refs=t.references,
            max_refs=3,
        )
        t.references = bound_refs

        parts = t.prompt.split("\n\n")
        anchored_core = inject_physical_anchors(parts[0], bound_refs)
        anchored_core = sanitize_visual_prompt(anchored_core, project_title=title, slug=slug)
        t.prompt = format_prompt_with_style_bible(
            anchored_core, aspect_ratio="16:9", project_title=title, slug=slug
        )

    # Audit final tasks with VisualPromptLinter and character guardrail
    linter = VisualPromptLinter(similarity_threshold=0.50, min_detail_ratio=0.30)
    is_valid, linter_errs = linter.validate(
        [
            {"scene_index": i + 1, "prompt": t.prompt, "title": t.title, "references": t.references}
            for i, t in enumerate(tasks)
        ],
        asset_catalog=assets,
        project_title=title,
    )
    if not is_valid:
        logger.warning("Final scene validation warning(s): %s", linter_errs)

    doc = GeminiSceneProject(
        schemaVersion=2,
        project=GeminiProjectMeta(
            id=slug,
            name=title,
            description=f"Long-form visual narrative shots for {title}.",
        ),
        assets=assets,
        tasks=tasks,
    )
    return doc.model_dump()


def _find_project(id_or_slug: str | int, db: Session) -> VideoProject | None:
    s = str(id_or_slug).strip()
    if s.isdigit():
        row = db.query(VideoProject).filter(VideoProject.id == int(s)).first()
        if row:
            return row

    # Check if format is 037_slug
    m = re.match(r"^(\d{3})_(.+)$", s)
    if m:
        p_id = int(m.group(1))
        row = db.query(VideoProject).filter(VideoProject.id == p_id).first()
        if row:
            return row
        p_slug = m.group(2)
        row = db.query(VideoProject).filter(VideoProject.slug == p_slug).first()
        if row:
            return row

    # Exact slug match
    row = db.query(VideoProject).filter(VideoProject.slug == s).first()
    if row:
        return row

    # Fuzzy alias match across all projects
    all_projects = db.query(VideoProject).all()
    keywords = [w.lower() for w in s.replace("-", " ").replace("_", " ").split() if len(w) > 3]
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
        return best_proj

    return None


@app.get("/projects", response_model=List[ProjectResponse])
def list_projects(db: Session = Depends(get_db)) -> list[ProjectResponse]:
    """List every project, newest first."""
    rows = (
        db.query(VideoProject)
        .order_by(VideoProject.created_at.desc())
        .all()
    )
    return [ProjectResponse.model_validate(r) for r in rows]


@app.get("/projects/{id_or_slug}", response_model=ProjectDetailResponse)
def get_project(id_or_slug: str, db: Session = Depends(get_db)) -> ProjectDetailResponse:
    row = _find_project(id_or_slug, db)
    if not row:
        raise HTTPException(status_code=404, detail=f"Project '{id_or_slug}' not found")

    files: list[str] = []
    error_logs: list[str] = []
    pipeline_summary = None

    try:
        from redsun.workspace import _REPO_ROOT

        ws = ensure_project_tree(row.slug)
        if ws.root.exists():
            for p in sorted(ws.root.rglob("*")):
                if p.is_file():
                    try:
                        files.append(str(p.relative_to(_REPO_ROOT)))
                    except ValueError:
                        files.append(str(p))
            if ws.pipeline_log_path.exists():
                lines = ws.pipeline_log_path.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()
                error_logs = [
                    l
                    for l in lines
                    if any(k in l for k in ("ERROR", "CRITICAL", "FAILED"))
                ][-20:]
            if ws.pipeline_summary_path.exists():
                try:
                    pipeline_summary = json.loads(
                        ws.pipeline_summary_path.read_text(encoding="utf-8")
                    )
                except Exception:
                    pass
    except Exception as exc:
        logger.debug("workspace lookup note for %s: %s", row.slug, exc)

    if row.error_message and row.error_message not in error_logs:
        error_logs.append(row.error_message)

    var_rows = db.query(TranslationVariant).filter(TranslationVariant.project_id == row.id).all()

    resp = ProjectDetailResponse.model_validate(row)
    resp.files = files
    resp.error_logs = error_logs
    resp.pipeline_summary = pipeline_summary
    if var_rows:
        resp.variants = [
            {
                "id": v.id,
                "project_id": v.project_id,
                "language_code": v.language_code,
                "script": v.script,
                "blog_article": v.blog_article,
                "short_clips_json": v.short_clips_json,
                "audio_path": v.audio_path or (str(ws.root / "audio" / "audio_main.wav") if (ws.root / "audio" / "audio_main.wav").exists() else None),
                "timestamps_path": v.timestamps_path or (str(ws.root / "audio" / "subtitles_en.json") if (ws.root / "audio" / "subtitles_en.json").exists() else (str(ws.root / "text" / "subtitles_en.json") if (ws.root / "text" / "subtitles_en.json").exists() else None)),
                "output_video_path": v.output_video_path,
            }
            for v in var_rows
        ]
    return resp


@app.get("/projects/{id_or_slug}/status")
def get_project_status(id_or_slug: str, db: Session = Depends(get_db)) -> dict:
    """Return pipeline stages status and deliverables status for Nodes 1-4 and Step 16."""
    row = _find_project(id_or_slug, db)
    if not row:
        raise HTTPException(status_code=404, detail=f"Project '{id_or_slug}' not found")

    real_slug = row.slug or f"project-{row.id}"
    ws = ensure_project_tree(real_slug)

    # Node 1: Script status
    variant_en = db.query(TranslationVariant).filter(
        TranslationVariant.project_id == row.id,
        TranslationVariant.language_code == "en"
    ).first()
    has_script = bool(variant_en and variant_en.script and len(variant_en.script.strip()) > 100)
    script_words = len(variant_en.script.split()) if has_script else 0
    source_chars = len(row.source_text or "")

    # Node 2: Audio & subtitles status
    audio_main = ws.root / "audio" / "audio_main.wav"
    audio_dur = 0.0
    if audio_main.exists():
        try:
            import wave
            with wave.open(str(audio_main), "rb") as w:
                r = w.getframerate()
                f = w.getnframes()
                if r and f:
                    audio_dur = f / r
        except Exception:
            pass
    has_audio = audio_main.exists() and audio_dur > 0

    subtitles_file = ws.root / "subtitles_en.json"
    if not subtitles_file.exists():
        subtitles_file = ws.root / "text" / "subtitles_en.json"
    subtitles_count = 0
    if subtitles_file.exists():
        try:
            subs = json.loads(subtitles_file.read_text(encoding="utf-8"))
            if isinstance(subs, list):
                subtitles_count = len(subs)
        except Exception:
            pass

    valid_img_exts = {".png", ".jpg", ".jpeg", ".webp"}
    refs_count = len([f for f in ws.gemini_references_dir.glob("*.*") if f.suffix.lower() in valid_img_exts]) if ws.gemini_references_dir.exists() else 0
    scenes_count = len([f for f in ws.gemini_output_dir.glob("*.*") if f.suffix.lower() in valid_img_exts]) if ws.gemini_output_dir.exists() else 0

    master_video = ws.root / "video" / f"{real_slug}_master.mp4"
    blog_file = ws.root / "blog" / f"{real_slug}.md"
    if not blog_file.exists():
        from redsun.services.blog_media_curator import get_astro_blog_root
        blog_file = get_astro_blog_root() / "src" / "content" / "blog" / f"{real_slug}.md"

    shorts_dir = ws.shorts_dir
    shorts_files = list(shorts_dir.glob("*short_*.mp4")) if shorts_dir.exists() else []
    if not shorts_files:
        legacy_shorts_dir = ws.root / "video" / "shorts"
        if legacy_shorts_dir.exists():
            shorts_files = list(legacy_shorts_dir.glob("*short_*.mp4"))

    dist_dir = ws.root / "distribution"
    dist_json = dist_dir / "social_distribution_kit.json"
    dist_twitter = dist_dir / "twitter_thread.md"
    dist_yt = dist_dir / "youtube_kit.txt"
    pins_dir = dist_dir / "pins_media"
    pins_files = list(pins_dir.glob("pin_*.*")) if pins_dir.exists() else []
    thumb_file = dist_dir / "thumbnail.png"
    has_thumbnail = thumb_file.exists()

    step_13_completed = master_video.exists() and master_video.stat().st_size > 1024 and scenes_count > 0
    step_14_completed = blog_file.exists() and blog_file.stat().st_size > 500
    step_15_completed = len(shorts_files) >= 3 and scenes_count > 0
    step_16_completed = dist_json.exists() and dist_twitter.exists() and dist_yt.exists() and step_13_completed

    with _JOB_LOCK:
        script_job = dict(ACTIVE_SCRIPT_JOBS.get(real_slug, {})) if real_slug in ACTIVE_SCRIPT_JOBS else None
        voice_job = dict(ACTIVE_VOICE_JOBS.get(real_slug, {})) if real_slug in ACTIVE_VOICE_JOBS else None

    try:
        from redsun.services.remotion_runner import ACTIVE_RENDER_JOBS, _LOCK as _REMOTION_LOCK
        with _REMOTION_LOCK:
            render_job = dict(ACTIVE_RENDER_JOBS.get(real_slug, {})) if real_slug in ACTIVE_RENDER_JOBS else None
    except Exception:
        render_job = None

    is_script_running = bool(script_job and script_job.get("status") == "running")
    if is_script_running:
        script_status = "RUNNING"
    elif has_script:
        script_status = "COMPLETED"
    elif row.status == VideoStatus.FAILED and (row.error_message or "").startswith("script:"):
        script_status = "FAILED"
    elif source_chars > 0:
        script_status = "PENDING"
    else:
        script_status = "PENDING"

    is_voice_running = bool(voice_job and voice_job.get("status") == "running")
    audio_sub_step = voice_job.get("step") if (voice_job and is_voice_running) else None
    if is_voice_running:
        audio_status = "RUNNING"
    elif has_audio:
        audio_status = "COMPLETED"
    elif row.status == VideoStatus.FAILED and (row.error_message or "").startswith("voice:"):
        audio_status = "FAILED"
    else:
        audio_status = "PENDING"

    is_render_running = bool(render_job and render_job.get("status") == "running")
    if is_render_running:
        render_status = "RUNNING"
    elif step_13_completed:
        render_status = "COMPLETED"
    elif row.status == VideoStatus.FAILED and (row.error_message or "").startswith("remotion:"):
        render_status = "FAILED"
    else:
        render_status = "PENDING"

    return {
        "id": row.id,
        "slug": real_slug,
        "title": row.title,
        "status": row.status.value,
        "script": {
            "has_script": has_script,
            "word_count": script_words,
            "source_chars": source_chars,
            "status": script_status,
            "error": script_job.get("error") if script_job else None,
        },
        "audio": {
            "has_audio": has_audio,
            "duration_seconds": round(audio_dur, 2),
            "duration_formatted": f"{int(audio_dur // 60)}m {int(audio_dur % 60):02d}s" if has_audio else None,
            "subtitles_count": subtitles_count,
            "status": audio_status,
            "sub_step": audio_sub_step,
            "error": voice_job.get("error") if voice_job else None,
        },
        "render": {
            "status": render_status,
            "error": render_job.get("error") if render_job else None,
        },
        "image_generation_mode": getattr(row, "image_generation_mode", "assistant") or "assistant",
        "images": {
            "refs_count": refs_count,
            "scenes_count": scenes_count,
            "status": "Awaiting Images" if scenes_count == 0 else f"{scenes_count} scenes generated",
            "mode": getattr(row, "image_generation_mode", "assistant") or "assistant",
            "api_status": get_generation_status(real_slug),
            "coverage_percentage": 0 if scenes_count == 0 else None,
        },
        "step_13_master": step_13_completed,
        "step_14_blog": step_14_completed,
        "step_15_shorts": step_15_completed,
        "step_16_distribution": step_16_completed,
        "distribution": {
            "has_kit": step_16_completed,
            "has_thumbnail": has_thumbnail,
            "thumbnail_path": str(thumb_file) if has_thumbnail else None,
            "json_path": str(dist_json) if dist_json.exists() and step_16_completed else None,
            "twitter_thread_path": str(dist_twitter) if dist_twitter.exists() and step_16_completed else None,
            "youtube_kit_path": str(dist_yt) if dist_yt.exists() and step_16_completed else None,
            "pins_count": len(pins_files) if step_16_completed else 0,
            "pins_media_dir": str(pins_dir) if pins_dir.exists() and step_16_completed else None,
            "youtube_video_id": row.youtube_video_id,
            "youtube_url": row.youtube_url,
            "youtube_status": row.youtube_status,
        }
    }


@app.get("/projects/{id_or_slug}/distribution-kit")
def get_project_distribution_kit(id_or_slug: str, db: Session = Depends(get_db)) -> dict:
    """Return the generated Universal Distribution Kit for the project."""
    row = _find_project(id_or_slug, db)
    if not row:
        raise HTTPException(status_code=404, detail=f"Project '{id_or_slug}' not found")

    real_slug = row.slug or f"project-{row.id}"
    ws = ensure_project_tree(real_slug)
    dist_json = ws.root / "distribution" / "social_distribution_kit.json"

    if not dist_json.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Distribution kit not yet generated for '{real_slug}'. Run generation explicitly via POST /projects/{real_slug}/distribution-kit/generate."
        )

    try:
        kit = json.loads(dist_json.read_text(encoding="utf-8"))
        thumb_path = ws.root / "distribution" / "thumbnail.png"
        if ("thumbnail" not in kit or not kit.get("thumbnail")) and thumb_path.exists():
            from redsun.services.thumbnail_generator import derive_headline_copy
            d = derive_headline_copy(row.title)
            kit["thumbnail"] = {
                "status": "COMPLETED",
                "path": str(thumb_path),
                "relative_path": "distribution/thumbnail.png",
                "resolution": "1280x720",
                **d,
            }
        return kit
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read distribution kit: {exc}")


@app.get("/projects/{id_or_slug}/thumbnail")
def get_project_thumbnail(id_or_slug: str, db: Session = Depends(get_db)):
    """Return the official 1280x720 YouTube thumbnail as an image."""
    from fastapi.responses import FileResponse

    row = _find_project(id_or_slug, db)
    if not row:
        raise HTTPException(status_code=404, detail=f"Project '{id_or_slug}' not found")
    real_slug = row.slug or f"project-{row.id}"
    ws = ensure_project_tree(real_slug)
    thumb_path = ws.root / "distribution" / "thumbnail.png"

    # If missing, attempt on-demand render
    if not thumb_path.exists():
        try:
            from redsun.services.thumbnail_generator import ThumbnailGenerator
            tg = ThumbnailGenerator()
            tg.render_thumbnail(ws.root, slug=real_slug, title=row.title)
        except Exception as exc:
            logger.warning("Could not render thumbnail on-the-fly for %s: %s", real_slug, exc)

    if not thumb_path.exists():
        raise HTTPException(status_code=404, detail=f"Thumbnail not found for '{real_slug}'")

    return FileResponse(
        path=str(thumb_path),
        media_type="image/png",
        filename=f"{real_slug}_thumbnail.png",
    )




@app.post("/projects/{slug}/distribution/thumbnail/regenerate")
async def regenerate_thumbnail_endpoint(slug: str, req: ThumbnailRegenerateRequest):
    """Regenerate project thumbnail via ThumbnailService with optional custom image and copy."""
    try:
        from redsun.services.thumbnail_generator import ThumbnailService
        output_path = await ThumbnailService.render_for_project(
            slug=slug,
            custom_image=req.source_image_path,
            custom_kicker=req.kicker,
            custom_headline_1=req.headline_line1,
            custom_headline_highlight=req.headline_highlight,
        )
        return {"success": True, "thumbnail_path": str(output_path)}
    except Exception as e:
        logger.exception("Failed in regenerate_thumbnail_endpoint for %s: %s", slug, e)
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/projects/{slug}/distribution/thumbnail/available-images")
def list_available_thumbnail_images_endpoint(slug: str, db: Session = Depends(get_db)) -> dict:
    """List all candidate images in the project that can be selected for YouTube thumbnail."""
    row = _find_project(slug, db)
    real_slug = row.slug if row else slug
    from redsun.services.thumbnail_generator import ThumbnailService
    images = ThumbnailService.list_available_images(real_slug)
    return {"images": images}


@app.post("/projects/{id_or_slug}/thumbnail/generate")
async def generate_project_thumbnail_endpoint(
    id_or_slug: str,
    req: Optional[ThumbnailRegenerateRequest] = None,
    db: Session = Depends(get_db),
) -> dict:
    """Explicitly render or re-render the official YouTube thumbnail."""
    row = _find_project(id_or_slug, db)
    if not row:
        raise HTTPException(status_code=404, detail=f"Project '{id_or_slug}' not found")
    real_slug = row.slug or f"project-{row.id}"
    ws = ensure_project_tree(real_slug)

    from redsun.services.thumbnail_generator import ThumbnailService, ThumbnailGenerator
    if req and (req.source_image_path or req.kicker or req.headline_line1 or req.headline_highlight):
        try:
            output_path = await ThumbnailService.render_for_project(
                slug=real_slug,
                custom_image=req.source_image_path,
                custom_kicker=req.kicker,
                custom_headline_1=req.headline_line1,
                custom_headline_highlight=req.headline_highlight,
            )
            return {"status": "COMPLETED", "path": str(output_path), "relative_path": "distribution/thumbnail.png"}
        except Exception as exc:
            logger.exception("Failed to render custom thumbnail for %s: %s", real_slug, exc)
            raise HTTPException(status_code=400, detail=str(exc))

    # Default automatic rendering
    dist_json = ws.root / "distribution" / "social_distribution_kit.json"
    thumb_copy = None
    if dist_json.exists():
        try:
            data = json.loads(dist_json.read_text(encoding="utf-8"))
            thumb_copy = data.get("youtube", {}).get("thumbnail") or data.get("thumbnail")
        except Exception:
            pass

    try:
        tg = ThumbnailGenerator()
        res = tg.render_thumbnail(
            project_dir=ws.root,
            slug=real_slug,
            title=row.title,
            copy_data=thumb_copy,
        )
        return res
    except Exception as exc:
        logger.exception("Failed to render thumbnail for %s: %s", real_slug, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/projects/{id_or_slug}/distribution-kit/generate")
def generate_project_distribution_kit_endpoint(id_or_slug: str, db: Session = Depends(get_db)) -> dict:
    """Explicitly generate or re-generate the Universal Distribution Kit."""
    row = _find_project(id_or_slug, db)
    if not row:
        raise HTTPException(status_code=404, detail=f"Project '{id_or_slug}' not found")

    real_slug = row.slug or f"project-{row.id}"
    from redsun.services.distribution_kit_generator import generate_distribution_kit
    try:
        kit = generate_distribution_kit(real_slug, force_regenerate=True)
        return kit
    except Exception as exc:
        logger.exception("Failed to generate distribution kit for %s: %s", real_slug, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.delete("/projects/{id_or_slug}")
def delete_project(id_or_slug: str, db: Session = Depends(get_db)) -> dict:
    """Delete a project and its translation variants."""
    row = _find_project(id_or_slug, db)
    if not row:
        raise HTTPException(status_code=404, detail=f"Project '{id_or_slug}' not found")

    project_id = row.id
    project_slug = row.slug

    # Remove translation variants first
    db.query(TranslationVariant).filter(TranslationVariant.project_id == project_id).delete()

    # Remove project
    db.delete(row)
    db.commit()

    logger.info("Deleted project #%s slug=%s", project_id, project_slug)
    return {
        "status": "deleted",
        "id": project_id,
        "slug": project_slug,
        "message": f"Projeto '{project_slug}' (ID {project_id}) excluído com sucesso."
    }


@app.post("/projects/{id_or_slug}/resume")
def resume_project(id_or_slug: str, db: Session = Depends(get_db)) -> dict:
    row = _find_project(id_or_slug, db)
    if not row:
        raise HTTPException(status_code=404, detail=f"Project '{id_or_slug}' not found")

    logger.info("Resuming pipeline for project #%s slug=%s", row.id, row.slug)

    # Transition status from waiting/pending/failed to next active phase
    if row.status in (VideoStatus.PENDING, VideoStatus.FAILED):
        row.status = VideoStatus.SCRIPTED
        row.error_message = None
        db.commit()
        db.refresh(row)

    # Materialize approved gate checkpoint and canonical image tree in workspace
    try:
        from redsun.workspace import ProjectWorkspace

        ws = ProjectWorkspace.from_slug(row.slug)
        if ws.root.exists():
            ws.materialize_canonical_image_tree()
            ws.checkpoints_dir.mkdir(parents=True, exist_ok=True)
            resume_marker = ws.checkpoints_dir / "gate_image_handoff_approved.json"
            resume_marker.write_text(
                json.dumps(
                    {
                        "approved_at": datetime.now(timezone.utc).isoformat(),
                        "status": "APPROVED",
                        "project_id": row.id,
                        "slug": row.slug,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
    except Exception as exc:
        logger.warning("Could not write resume checkpoint for %s: %s", row.slug, exc)

    # Automatically prepare Remotion manifest and trigger Remotion rendering in background
    render_job_info = None
    try:
        from redsun.services.remotion_runner import start_remotion_render
        render_job_info = start_remotion_render(row.slug, concurrency=2)
        logger.info("Dispatched Remotion render background task for %s: %s", row.slug, render_job_info)
    except Exception as exc:
        logger.exception("Failed to start Remotion render job for %s: %s", row.slug, exc)
        render_job_info = {"status": "error", "error": str(exc)}

    return {
        "status": "resumed",
        "project_id": row.id,
        "slug": row.slug,
        "message": f"Pipeline resumed for '{row.slug}'. #10 Remotion Manifest gerado e compilação do Nó 4 iniciada.",
        "resumed_at": datetime.now(timezone.utc).isoformat(),
        "render_job": render_job_info,
    }


@app.get("/projects/{id_or_slug}/logs")
def get_project_logs(id_or_slug: str, lines: int = Query(100, ge=1, le=500), db: Session = Depends(get_db)) -> dict:
    """Returns the latest execution logs from pipeline.log for the project."""
    row = _find_project(id_or_slug, db)
    if not row:
        raise HTTPException(status_code=404, detail=f"Project '{id_or_slug}' not found")

    from redsun.services.remotion_runner import get_pipeline_logs
    log_lines = get_pipeline_logs(row.slug, max_lines=lines)
    return {
        "slug": row.slug,
        "project_id": row.id,
        "total_lines": len(log_lines),
        "logs": log_lines,
    }



@app.get("/projects/{id_or_slug}/pipeline-result")
def get_pipeline_result(id_or_slug: str, db: Session = Depends(get_db)) -> dict:
    """Return the latest structured `PipelineResult` for the project."""
    row = _find_project(id_or_slug, db)
    if not row:
        raise HTTPException(status_code=404, detail=f"Project '{id_or_slug}' not found")

    try:
        from redsun.workspace import ProjectWorkspace
        from pathlib import Path as _P

        ws = ProjectWorkspace.from_slug(row.slug)
        candidates = [
            ws.pipeline_summary_path,
            _P(os.environ.get("REDSUN_PROJECTS_DIR", "")) / row.slug / "pipeline_summary.json"
            if os.environ.get("REDSUN_PROJECTS_DIR")
            else None,
        ]
        for path in candidates:
            if path is None:
                continue
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("pipeline-result lookup failed for #%s: %s", row.id, exc)

    return {
        "status": "UNKNOWN",
        "slug": row.slug,
        "project_id": row.id,
        "message": "pipeline_summary.json not found for this project",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints — Handoff Gate de Imagens (Fase 1: Referências & Fase 2: Cenas)
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/projects/{slug}/image-prompts/references")
def get_image_prompts_references(slug: str, db: Session = Depends(get_db)) -> dict:
    """Retorna o JSON canônico de Fase A (gemini-assistant/ref-job/project.json e images/prompts/01_references.json)."""
    row = _find_project(slug, db)
    real_slug = row.slug if row else slug
    ws = ensure_project_tree(real_slug)
    proj_title = row.title if row else ""
    script_text = row.source_text if row else ""

    # Look for existing files
    for p in (ws.gemini_ref_job_project_json, ws.references_json_path, ws.gemini_ref_gen_project_json, ws.gemini_references_project_json):
        if p.exists():
            try:
                raw_data = json.loads(p.read_text(encoding="utf-8"))
                clean_data = _normalize_reference_project(
                    raw_data, slug=real_slug, title=proj_title
                )
                # Enforce fidelity linter before accepting
                ReferenceFidelityLinter.validate_or_raise(
                    clean_data,
                    project_title=proj_title,
                    slug=real_slug,
                    script_text=script_text,
                )
                _save_reference_project(ws, clean_data)
                return clean_data
            except (PromptContaminationError, ReferenceFidelityError) as err:
                logger.warning("Existing references at %s failed fidelity linter: %s. Re-assembling.", p, err)
            except Exception as exc:
                logger.warning("Error reading/normalizing %s for %s: %s", p, real_slug, exc)

    # Check for visual_entities in workspace or DB
    ve_data = None
    ve_file = ws.root / "visual_entities.json"
    if ve_file.exists():
        try:
            ve_data = json.loads(ve_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    if not ve_data and row and getattr(row, "visual_entities_json", None):
        try:
            ve_data = json.loads(row.visual_entities_json)
        except Exception:
            pass

    clean_data = _build_default_references(
        slug=real_slug,
        title=proj_title,
        visual_entities=ve_data,
        source_text_ja=script_text,
    )
    _save_reference_project(ws, clean_data)
    return clean_data


@app.post("/projects/{slug}/image-prompts/generate-scenes")
def generate_image_prompts_scenes(slug: str, db: Session = Depends(get_db)) -> dict:
    """Consome o script, visual plan e referências para gravar e retornar project.json e 02_final_scenes.json."""
    row = _find_project(slug, db)
    real_slug = row.slug if row else slug
    ws = ensure_project_tree(real_slug)

    # Ensure reference project exists
    has_refs = any(
        p.exists()
        for p in (ws.gemini_ref_job_project_json, ws.references_json_path, ws.gemini_ref_gen_project_json, ws.gemini_references_project_json)
    )
    if not has_refs:
        ref_data = get_image_prompts_references(real_slug, db=db)
        _save_reference_project(ws, ref_data)

    data = _build_final_scenes(ws, row)
    _save_scene_project(ws, data)
    logger.info(
        "Generated %d final scenes in project.json for %s",
        len(data.get("tasks", [])),
        real_slug,
    )

    # Trigger Hook Handoff Gate: notify macOS that contracts are ready for human generation
    try:
        from services.agent_dispatcher import notify_macos
        notify_macos(
            title="Red Sun Japan // Handoff Gate",
            message=f"Prompts gerados para '{real_slug}'. Pronto para geração de imagens no Gemini Assistant!",
            sound="Glass",
        )
    except Exception as exc:
        logger.warning("Could not emit Handoff Gate notification: %s", exc)

    return data


@app.get("/projects/{slug}/image-prompts/scenes")
def get_image_prompts_scenes(slug: str, db: Session = Depends(get_db)) -> dict:
    """Retorna o JSON das cenas finais (gemini-assistant/project.json), garantindo densidade dinâmica."""
    row = _find_project(slug, db)
    real_slug = row.slug if row else slug
    ws = ensure_project_tree(real_slug)

    # Check required dynamic scene count for this project's audio (10-12s pacing)
    audio_dur = 0.0
    audio_cand = ws.audio_dir / "audio_main.wav"
    if not audio_cand.exists():
        audio_cand = ws.audio_main_path
    if audio_cand.exists():
        try:
            import wave
            with wave.open(str(audio_cand), "rb") as w:
                r = w.getframerate()
                f = w.getnframes()
                if r and f:
                    audio_dur = f / r
        except Exception:
            pass
    if audio_dur <= 0:
        for sp in (
            ws.root / "text" / "subtitles_en.json",
            ws.root / "subtitles" / "subtitles_en.json",
            ws.subtitles_path,
        ):
            if sp.exists():
                try:
                    s_data = json.loads(sp.read_text(encoding="utf-8"))
                    if isinstance(s_data, list) and s_data:
                        audio_dur = float(s_data[-1].get("end") or 0.0)
                        if audio_dur > 0:
                            break
                except Exception:
                    pass
    required_scenes = calculate_dynamic_scene_count(audio_dur) if audio_dur > 0 else 25

    for p in (ws.gemini_project_json, ws.final_scenes_json_path, ws.gemini_scenes_project_json):
        if p.exists():
            try:
                raw_data = json.loads(p.read_text(encoding="utf-8"))
                tasks_in_json = len(raw_data.get("tasks", []))
                if tasks_in_json >= required_scenes:
                    clean_data = _normalize_scene_project(
                        raw_data, slug=real_slug, title=row.title if row else ""
                    )
                    _save_scene_project(ws, clean_data)
                    return clean_data
                else:
                    logger.info(
                        "Cached scenes for %s has %d tasks, but required is %d. Regenerating...",
                        real_slug,
                        tasks_in_json,
                        required_scenes,
                    )
            except Exception as exc:
                logger.warning("Error reading/normalizing %s for %s: %s", p, real_slug, exc)

    # Generate on demand if missing or insufficient
    return generate_image_prompts_scenes(slug=real_slug, db=db)



@app.post("/projects/{slug}/image-prompts/revise")
def revise_image_prompts(
    slug: str,
    payload: PromptRevisionPayload,
    db: Session = Depends(get_db),
) -> dict:
    """Surgically revise one or more visual prompts (references or scenes) using Human-in-the-Loop AI."""
    row = _find_project(slug, db)
    real_slug = row.slug if row else slug
    project_title = row.title if row else ""

    from redsun.services.prompt_revision_service import PromptRevisionService

    try:
        result = PromptRevisionService.revise_prompts(
            slug=real_slug,
            instruction=payload.instruction,
            target_task_id=payload.target_task_id,
            scope=payload.scope,
            project_title=project_title,
        )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error("Error revising prompts for %s: %s", real_slug, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Erro na revisão com IA: {exc}")


def _ingest_from_downloads(slug: str, ws: "ProjectWorkspace") -> dict:
    """Detect and move generated images from ~/Downloads/Gemini Assistant/<slug> directly
    into gemini-assistant/output/ (and references into gemini-assistant/references/), avoiding disk duplication.
    """
    downloads_root = Path.home() / "Downloads"
    ga_downloads_dir = downloads_root / "Gemini Assistant"

    valid_exts = {".png", ".jpg", ".jpeg", ".webp"}
    moved_scenes: list[str] = []
    moved_refs: list[str] = []
    cleaned_dirs: list[str] = []

    target_output_dir = ws.gemini_output_dir
    target_output_dir.mkdir(parents=True, exist_ok=True)
    target_refs_dir = ws.gemini_references_dir
    target_refs_dir.mkdir(parents=True, exist_ok=True)

    # 1. Rigorous scan of ~/Downloads/Gemini Assistant/<slug> for scenes
    scenes_dl_dir = ga_downloads_dir / slug
    if scenes_dl_dir.exists() and scenes_dl_dir.is_dir():
        for f in list(scenes_dl_dir.iterdir()):
            if f.name == ".DS_Store":
                try:
                    f.unlink()
                except OSError:
                    pass
                continue

            if f.is_file() and f.suffix.lower() in valid_exts:
                dst = target_output_dir / f.name
                try:
                    if dst.exists() and dst.stat().st_size == f.stat().st_size:
                        f.unlink()
                    else:
                        shutil.move(str(f), str(dst))
                    moved_scenes.append(f.name)
                except Exception as exc:
                    logger.warning("Failed moving %s -> %s: %s", f, dst, exc)

        # Remove if directory is now empty
        try:
            for sub in list(scenes_dl_dir.iterdir()):
                if sub.name.startswith("."):
                    try:
                        sub.unlink()
                    except OSError:
                        pass
            if not any(scenes_dl_dir.iterdir()):
                os.rmdir(str(scenes_dl_dir))
                cleaned_dirs.append(str(scenes_dl_dir))
        except Exception as exc:
            logger.debug("Could not remove %s: %s", scenes_dl_dir, exc)

    # 2. Check candidate reference directories ~/Downloads/Gemini Assistant/<slug>-references
    candidate_ref_dirs = [
        ga_downloads_dir / f"{slug}-references",
        ga_downloads_dir / f"{slug}_references",
        ga_downloads_dir / slug / "references",
    ]
    for crd in candidate_ref_dirs:
        if crd.exists() and crd.is_dir():
            for f in list(crd.iterdir()):
                if f.name == ".DS_Store":
                    try:
                        f.unlink()
                    except OSError:
                        pass
                    continue
                if f.is_file() and f.suffix.lower() in valid_exts:
                    dst = target_refs_dir / f.name
                    try:
                        if dst.exists() and dst.stat().st_size == f.stat().st_size:
                            f.unlink()
                        else:
                            shutil.move(str(f), str(dst))
                        moved_refs.append(f.name)
                    except Exception as exc:
                        logger.warning("Failed moving %s -> %s: %s", f, dst, exc)
            try:
                for sub in list(crd.iterdir()):
                    if sub.name.startswith("."):
                        try:
                            sub.unlink()
                        except OSError:
                            pass
                if not any(crd.iterdir()):
                    os.rmdir(str(crd))
                    cleaned_dirs.append(str(crd))
            except Exception:
                pass

    # 3. Check any loose files matching slug in Downloads root
    for f in downloads_root.glob(f"*{slug}*"):
        if f.is_file() and f.suffix.lower() in valid_exts:
            dst = target_output_dir / f.name
            try:
                shutil.move(str(f), str(dst))
                moved_scenes.append(f.name)
            except Exception:
                pass

    all_moved = sorted(list(set(moved_scenes + moved_refs)))
    return {
        "moved_count": len(all_moved),
        "files": all_moved,
        "ingested_scenes": sorted(list(set(moved_scenes))),
        "ingested_references": sorted(list(set(moved_refs))),
        "cleaned_dirs": cleaned_dirs,
    }


@app.post("/projects/{slug}/images/ingest-downloads")
def ingest_project_downloads(slug: str, db: Session = Depends(get_db)) -> dict:
    """Varre e move rigorosamente imagens de ~/Downloads/Gemini Assistant/{slug}
    para gemini-assistant/output/ e atualiza o status.
    Materializa deterministicamente a árvore canônica de imagens (symlinks ou espelhamento).
    """
    row = _find_project(slug, db)
    real_slug = row.slug if row else slug
    ws = ensure_project_tree(real_slug)
    ingestion_result = _ingest_from_downloads(real_slug, ws)
    tree_result = ws.materialize_canonical_image_tree()
    status_data = get_images_status(slug=real_slug, db=db)
    return {
        "success": True,
        "moved_count": ingestion_result.get("moved_count", 0),
        "files": ingestion_result.get("files", []),
        "ingested": ingestion_result,
        "canonical_tree": tree_result,
        "status": status_data,
    }


@app.post("/projects/{slug}/images/promote-references")
def promote_references(slug: str, db: Session = Depends(get_db)) -> dict:
    """Aprova as referências geradas em gemini-assistant/references/.
    Atualiza gemini-assistant/project.json para vincular exatamente os arquivos físicos existentes.
    Atualiza job.json para marcar a Fase 2 pronta para execução.
    Materializa deterministicamente a árvore canônica de imagens.
    """
    row = _find_project(slug, db)
    real_slug = row.slug if row else slug
    ws = ensure_project_tree(real_slug)

    # Ingest any pending downloads
    _ingest_from_downloads(real_slug, ws)
    tree_result = ws.materialize_canonical_image_tree()

    valid_exts = (".png", ".jpg", ".jpeg", ".webp")
    ref_files = [
        f.name for f in ws.gemini_references_dir.iterdir()
        if f.is_file() and f.suffix.lower() in valid_exts
    ]

    # Re-build / update gemini-assistant/project.json so assets resolve to newly approved references
    scenes_data = _build_final_scenes(ws, row)
    _save_scene_project(ws, scenes_data)

    # Update job.json lifecycle according to GEMINI_ASSISTANT_GUIDE.md
    _write_job_json(ws.gemini_ref_job_job_json, "references", "completed")
    _write_job_json(ws.gemini_job_json, "scenes", "ready")

    logger.info("Promoted %d references for %s", len(ref_files), real_slug)
    status_data = get_images_status(slug=real_slug, db=db)
    return {
        "success": True,
        "promoted_count": len(ref_files),
        "files": sorted(ref_files),
        "references_dir": str(ws.gemini_references_dir.resolve()),
        "canonical_tree": tree_result,
        "status": status_data,
    }


@app.post("/projects/{slug}/images/approve")
def approve_project_images(slug: str, db: Session = Depends(get_db)) -> dict:
    """Aprova e conclui o Step 9 (Handoff Gate de Imagens / Ingestão).
    Materializa deterministicamente a árvore canônica de imagens (symlinks ou espelhamento):
      * projects/{slug}/images/final -> ../gemini-assistant/output
      * projects/{slug}/images/references -> ../gemini-assistant/references
    """
    row = _find_project(slug, db)
    real_slug = row.slug if row else slug
    ws = ensure_project_tree(real_slug)
    _ingest_from_downloads(real_slug, ws)
    tree_result = ws.materialize_canonical_image_tree()
    status_data = get_images_status(slug=real_slug, db=db)
    return {
        "success": True,
        "slug": real_slug,
        "canonical_tree": tree_result,
        "status": status_data,
    }


@app.post("/projects/{slug}/hygiene")
def run_project_hygiene(slug: str, db: Session = Depends(get_db)) -> dict:
    """Executa a limpeza determinística e higienização de pastas legadas do projeto."""
    row = _find_project(slug, db)
    real_slug = row.slug if row else slug
    result = cleanup_and_unify_project_storage(real_slug)
    return {"success": True, **result}


@app.get("/projects/{slug}/images/status")
def get_images_status(slug: str, db: Session = Depends(get_db)) -> dict:
    """Retorna a contagem e lista de imagens existentes em references/ e em output/.
    Executa auto-ingestão de ~/Downloads/Gemini Assistant automaticamente.
    """
    row = _find_project(slug, db)
    real_slug = row.slug if row else slug
    ws = ensure_project_tree(real_slug)

    # Ingest any pending downloads from Chrome
    _ingest_from_downloads(real_slug, ws)

    ref_dir = ws.gemini_references_dir
    final_dir = ws.gemini_output_dir
    os.makedirs(ref_dir, exist_ok=True)
    os.makedirs(final_dir, exist_ok=True)

    valid_exts = (".png", ".jpg", ".jpeg", ".webp")

    ref_files = sorted(
        [
            f.name
            for f in ref_dir.iterdir()
            if f.is_file() and f.suffix.lower() in valid_exts
        ]
    )
    final_files = sorted(
        [
            f.name
            for f in final_dir.iterdir()
            if f.is_file() and f.suffix.lower() in valid_exts
        ]
    )

    total_refs = 9
    for p in (ws.gemini_ref_job_project_json, ws.references_json_path):
        if p.exists():
            try:
                ref_json = json.loads(p.read_text(encoding="utf-8"))
                total_refs = len(ref_json.get("tasks", [])) or total_refs
                break
            except Exception:
                pass

    # Determine audio duration to enforce 10-12s pacing dynamically
    audio_dur = 0.0
    audio_cand = ws.audio_dir / "audio_main.wav"
    if not audio_cand.exists():
        audio_cand = ws.audio_main_path
    if audio_cand.exists():
        try:
            import wave
            with wave.open(str(audio_cand), "rb") as w:
                r = w.getframerate()
                f = w.getnframes()
                if r and f:
                    audio_dur = f / r
        except Exception:
            pass

    if audio_dur <= 0:
        for sp in (
            ws.root / "text" / "subtitles_en.json",
            ws.root / "subtitles" / "subtitles_en.json",
            ws.root / "subtitles_en.json",
        ):
            if sp.exists():
                try:
                    s_data = json.loads(sp.read_text(encoding="utf-8"))
                    if isinstance(s_data, list) and s_data:
                        audio_dur = float(s_data[-1].get("end") or 0.0)
                        if audio_dur > 0:
                            break
                except Exception:
                    pass

    required_scenes = calculate_dynamic_scene_count(audio_dur) if audio_dur > 0 else 25

    total_scenes = required_scenes
    for p in (ws.gemini_project_json, ws.final_scenes_json_path):
        if p.exists():
            try:
                scenes_json = json.loads(p.read_text(encoding="utf-8"))
                tasks_in_json = len(scenes_json.get("tasks", []))
                if tasks_in_json >= required_scenes:
                    total_scenes = tasks_in_json
                    break
            except Exception:
                pass

    scenes_count = len(final_files)
    refs_count = len(ref_files)
    ref_complete = refs_count >= total_refs and refs_count > 0
    final_complete = scenes_count >= total_scenes and scenes_count > 0
    coverage_pct = 0 if scenes_count == 0 else min(100, round((scenes_count / max(1, total_scenes)) * 100))
    coverage_str = "Aguardando Imagens (0% de cobertura)" if scenes_count == 0 else f"{scenes_count}/{total_scenes} cenas carregadas"
    gate_ready = final_complete and ref_complete
    actual_pacing = round(audio_dur / max(1, scenes_count), 1) if (audio_dur > 0 and scenes_count > 0) else 12.0
    target_pacing = round(audio_dur / max(1, total_scenes), 1) if audio_dur > 0 else 12.0

    # Read job.json lifecycle states
    ref_job = _read_job_json(ws.gemini_ref_job_job_json, "references", "ready")
    scenes_job = _read_job_json(ws.gemini_job_json, "scenes", "preparing")

    if ref_complete and ref_job.get("status") in ("ready", "in-progress"):
        ref_job["status"] = "completed"

    # Count resolved references in gemini-assistant
    resolved_refs_count = 0
    scenes_data = {}
    if ws.gemini_project_json.exists():
        try:
            scenes_data = json.loads(ws.gemini_project_json.read_text(encoding="utf-8"))
        except Exception:
            pass
    scenes_assets = scenes_data.get("assets", {})
    total_refs_required = len(scenes_assets) if scenes_assets else total_refs
    if scenes_assets:
        for asset_key, asset_val in scenes_assets.items():
            rel_file = asset_val.get("file", "")
            if rel_file and (ws.gemini_assistant_dir / rel_file).exists():
                resolved_refs_count += 1
    else:
        resolved_refs_count = len(list(ws.gemini_references_dir.glob("*.*")))

    references_data = {
        "expected": total_refs,
        "current_count": len(ref_files),
        "count": len(ref_files),
        "total_required": total_refs,
        "files": ref_files,
        "is_complete": ref_complete,
        "directory_path": str(ref_dir.resolve()),
        "gemini_folder": str(ws.gemini_ref_job_dir.resolve()),
        "job": ref_job,
    }

    final_scenes_data = {
        "expected": total_scenes,
        "current_count": len(final_files),
        "count": len(final_files),
        "total_required": total_scenes,
        "files": final_files,
        "is_complete": final_complete,
        "directory_path": str(final_dir.resolve()),
        "gemini_folder": str(ws.gemini_assistant_dir.resolve()),
        "resolved_references": resolved_refs_count,
        "total_references": total_refs_required,
        "job": scenes_job,
        "required_scenes": required_scenes,
        "actual_pacing_seconds": actual_pacing,
        "target_pacing_seconds": target_pacing,
    }

    return {
        "slug": real_slug,
        "schema_version": 2,
        "references": references_data,
        "final_scenes": final_scenes_data,
        "final": final_scenes_data,
        "job_status": {
            "references": ref_job,
            "scenes": scenes_job,
        },
        "coverage": coverage_str,
        "coverage_percentage": coverage_pct,
        "gate_ready": gate_ready,
        "image_generation_mode": getattr(row, "image_generation_mode", "assistant") if row else "assistant",
        "api_status": get_generation_status(real_slug),
        "audio_duration_seconds": round(audio_dur, 2),
        "required_scenes": required_scenes,
        "actual_pacing_seconds": actual_pacing,
        "target_pacing_seconds": target_pacing,
        "gemini_references_dir": str(ws.gemini_ref_job_dir.resolve()),
        "gemini_reference_generation_dir": str(ws.gemini_ref_job_dir.resolve()),
        "gemini_scenes_dir": str(ws.gemini_assistant_dir.resolve()),
    }


@app.post("/projects/{slug}/images/upload")
async def upload_project_images(
    slug: str,
    type: str = Query("references", description="Target folder: 'references' or 'final'"),
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db),
) -> dict:
    """Faz o upload direto de múltiplos arquivos de imagem para as pastas references ou final."""
    target_type = type.lower().strip()
    if target_type not in ("references", "final", "final_scenes"):
        raise HTTPException(
            status_code=400,
            detail="Parâmetro 'type' deve ser 'references' ou 'final'",
        )

    row = _find_project(slug, db)
    real_slug = row.slug if row else slug
    ws = ensure_project_tree(real_slug)

    primary_dir = ws.gemini_references_dir if target_type == "references" else ws.gemini_output_dir
    os.makedirs(primary_dir, exist_ok=True)

    saved_files: list[str] = []
    valid_exts = (".png", ".jpg", ".jpeg", ".webp")

    for upload_file in files:
        if not upload_file.filename:
            continue
        ext = Path(upload_file.filename).suffix.lower()
        if ext not in valid_exts:
            logger.warning("Skipping non-image upload file: %s", upload_file.filename)
            continue

        safe_filename = Path(upload_file.filename).name
        dest_path = primary_dir / safe_filename

        content = await upload_file.read()
        dest_path.write_bytes(content)
        saved_files.append(safe_filename)
        logger.info(
            "Saved image %s to %s (%d bytes)",
            safe_filename,
            primary_dir,
            len(content),
        )

    status_response = get_images_status(slug=real_slug, db=db)
    return {
        "success": True,
        "type": "references" if target_type == "references" else "final",
        "saved_count": len(saved_files),
        "saved_files": saved_files,
        "status": status_response,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints — Hybrid Image Generation (Assistant vs Autonomous API)
# ─────────────────────────────────────────────────────────────────────────────
@app.post("/projects/{slug}/image-mode")
def set_image_generation_mode(
    slug: str,
    payload: ImageModeRequest,
    db: Session = Depends(get_db),
) -> dict:
    """Toggle between manual Chrome extension ('assistant') and autonomous Meta Muse Image ('api')."""
    row = _find_project(slug, db)
    if not row:
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")
    row.image_generation_mode = payload.mode
    db.commit()
    db.refresh(row)
    if payload.mode == "assistant":
        reset_generation_status(row.slug or slug)
    logger.info("Project '%s' image_generation_mode updated to '%s'", row.slug, payload.mode)
    return {
        "success": True,
        "slug": row.slug,
        "mode": row.image_generation_mode,
        "image_generation_mode": row.image_generation_mode,
    }


@app.post("/projects/{slug}/generate-images-api")
def trigger_api_image_generation(
    slug: str,
    background_tasks: BackgroundTasks,
    force_all: bool = Query(False, description="Regenerate all images even if already present"),
    db: Session = Depends(get_db),
) -> dict:
    """Dispara a geração 100% autônoma via API Meta Muse Image em background task."""
    row = _find_project(slug, db)
    if not row:
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")
    real_slug = row.slug or slug

    current_status = get_generation_status(real_slug)
    if current_status.get("status") == "running":
        return {
            "success": True,
            "message": "Generation already running",
            "status": current_status,
        }

    background_tasks.add_task(
        run_autonomous_generation,
        slug=real_slug,
        project_title=row.title,
        force_all=force_all,
    )

    return {
        "success": True,
        "message": "Autonomous image generation started in background",
        "slug": real_slug,
    }


@app.get("/projects/{slug}/generate-images-api/status")
def get_api_image_generation_status(slug: str, db: Session = Depends(get_db)) -> dict:
    """Retorna o progresso atual da geração autônoma de imagens via API."""
    row = _find_project(slug, db)
    real_slug = row.slug if row else slug
    return get_generation_status(real_slug)


@app.post("/projects/{slug}/generate-images-api/cancel")
def cancel_api_image_generation_endpoint(slug: str, db: Session = Depends(get_db)) -> dict:
    """Cancela ou pausa a geração autônoma de imagens em andamento."""
    row = _find_project(slug, db)
    real_slug = row.slug if row else slug
    canceled = cancel_generation(real_slug)
    return {"success": canceled, "slug": real_slug}


@app.post("/projects/{slug}/generate-images-api/single")
def regenerate_single_api_image(
    slug: str,
    payload: SingleFrameRegenerateRequest,
    db: Session = Depends(get_db),
) -> dict:
    """Regera pontualmente apenas um frame específico (cena ou referência) via API."""
    row = _find_project(slug, db)
    if not row:
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")
    real_slug = row.slug or slug
    try:
        res = regenerate_single_frame(
            slug=real_slug,
            target_identifier=payload.identifier,
            project_title=row.title,
        )
        return {"success": True, **res}
    except Exception as exc:
        logger.error("Failed to regenerate single image %s for %s: %s", payload.identifier, real_slug, exc)
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/projects/{slug}/gallery")
def get_project_gallery(slug: str, db: Session = Depends(get_db)) -> dict:
    """Escaneia gemini-assistant/references/ e gemini-assistant/output/.
    Retorna a lista completa com: filename, stage ('reference' | 'scene'), url de preview, e timestamp.
    """
    row = _find_project(slug, db)
    real_slug = row.slug if row else slug
    ws = ensure_project_tree(real_slug)

    valid_exts = (".png", ".jpg", ".jpeg", ".webp")
    items = []

    # Map tasks metadata
    ref_task_map = {}
    if ws.gemini_ref_job_project_json.exists():
        try:
            pdata = json.loads(ws.gemini_ref_job_project_json.read_text(encoding="utf-8"))
            for t in pdata.get("tasks", []):
                b = t.get("output", {}).get("basename") or t.get("id")
                if b:
                    ref_task_map[str(b).lower()] = t
                    ref_task_map[str(t.get("id", "")).lower()] = t
        except Exception:
            pass

    scene_task_map = {}
    if ws.gemini_project_json.exists():
        try:
            pdata = json.loads(ws.gemini_project_json.read_text(encoding="utf-8"))
            for t in pdata.get("tasks", []):
                b = t.get("output", {}).get("basename") or t.get("id")
                if b:
                    scene_task_map[str(b).lower()] = t
                    scene_task_map[str(t.get("id", "")).lower()] = t
        except Exception:
            pass

    # 1. References
    if ws.gemini_references_dir.exists():
        for f in sorted(ws.gemini_references_dir.iterdir()):
            if f.is_file() and f.suffix.lower() in valid_exts:
                stat = f.stat()
                stem = f.stem.lower()
                task_info = ref_task_map.get(stem, {})
                items.append({
                    "filename": f.name,
                    "stage": "reference",
                    "task_id": task_info.get("id", f.stem),
                    "title": task_info.get("title", f.stem.replace("-", " ").replace("_", " ").title()),
                    "prompt": task_info.get("prompt"),
                    "url": f"/media/{real_slug}/references/{f.name}",
                    "size_bytes": stat.st_size,
                    "created_at": stat.st_mtime,
                    "approved": True,
                })

    # 2. Scenes
    if ws.gemini_output_dir.exists():
        for f in sorted(ws.gemini_output_dir.iterdir()):
            if f.is_file() and f.suffix.lower() in valid_exts:
                stat = f.stat()
                stem = f.stem.lower()
                task_info = scene_task_map.get(stem, {})
                items.append({
                    "filename": f.name,
                    "stage": "scene",
                    "task_id": task_info.get("id", f.stem),
                    "title": task_info.get("title", f.stem.replace("-", " ").replace("_", " ").title()),
                    "prompt": task_info.get("prompt"),
                    "url": f"/media/{real_slug}/output/{f.name}",
                    "size_bytes": stat.st_size,
                    "created_at": stat.st_mtime,
                    "approved": True,
                })

    ref_count = sum(1 for it in items if it["stage"] == "reference")
    scene_count = sum(1 for it in items if it["stage"] == "scene")
    return {
        "slug": real_slug,
        "total": len(items),
        "references_count": ref_count,
        "scenes_count": scene_count,
        "items": items,
    }


@app.get("/media/{slug}/{stage}/{filename}")
def get_media_preview(slug: str, stage: str, filename: str, db: Session = Depends(get_db)):
    """Serve static image preview for references and final scene images."""
    clean_filename = os.path.basename(filename)
    stage_lower = stage.lower().strip()
    row = _find_project(slug, db)
    real_slug = row.slug if row else slug
    ws = ensure_project_tree(real_slug)

    if stage_lower in ("reference", "references"):
        file_path = ws.gemini_references_dir / clean_filename
    elif stage_lower in ("scene", "scenes", "output", "final"):
        file_path = ws.gemini_output_dir / clean_filename
    else:
        raise HTTPException(status_code=400, detail=f"Invalid stage '{stage}'. Must be 'references' or 'output'.")

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail=f"Image '{clean_filename}' not found in {stage}.")

    mime = "image/jpeg"
    if clean_filename.lower().endswith(".png"):
        mime = "image/png"
    elif clean_filename.lower().endswith(".webp"):
        mime = "image/webp"

    return FileResponse(path=str(file_path), media_type=mime, filename=clean_filename)



# ─────────────────────────────────────────────────────────────────────────────
# Endpoints — assets (Phase 1: scraper smoke entrypoint)
# ─────────────────────────────────────────────────────────────────────────────
@app.post("/assets/fetch", response_model=List[AssetSummary])
def assets_fetch(
    payload: AssetFetchRequest,
    db: Session = Depends(get_db),
) -> list[AssetSummary]:
    """Fetch + persist public-domain images via the Wikimedia scraper.

    This is a real network call against Commons; it is intentionally
    exposed as an endpoint so Isamu can trigger it from the future
    operator UI without booting the Python REPL.
    """
    try:
        results = scraper_fetch_assets(
            query=payload.query,
            theme=payload.theme,
            limit=payload.limit,
            db=db,
        )
    except Exception as exc:
        logger.exception("Asset fetch failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    logger.info(
        "Asset fetch ok: theme=%s query=%r returned %s rows",
        payload.theme, payload.query, len(results),
    )
    return [AssetSummary.model_validate(r) for r in results]


@app.get("/assets", response_model=List[AssetSummary])
def list_assets_by_theme(
    theme: str,
    db: Session = Depends(get_db),
) -> list[AssetSummary]:
    """List every persisted asset under a theme (for the future operator UI)."""
    rows = (
        db.query(AssetImage)
        .filter(AssetImage.theme == theme)
        .order_by(AssetImage.downloaded_at.desc())
        .all()
    )
    return [AssetSummary.model_validate(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints — TTS (Phase 2)
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/tts/speakers")
def tts_speakers(provider: str | None = None) -> dict:
    """List available TTS speakers/voices for the active or requested provider."""
    active = (provider or os.environ.get("TTS_PROVIDER") or os.environ.get("TTS_ENGINE", "kokoro")).lower().strip()
    return {
        "provider": active,
        "speakers": tts_list_speakers(engine=active),
    }


@app.get("/tts/config")
@app.get("/config/tts")
def get_tts_config() -> dict:
    """Return active TTS provider and configuration."""
    active_provider = (os.environ.get("TTS_PROVIDER") or os.environ.get("TTS_ENGINE", "stepfun")).lower().strip()
    stepfun_key = os.environ.get("STEPFUN_API_KEY", "").strip()
    has_stepfun_key = bool(stepfun_key and stepfun_key != "sua_chave_stepfun_aqui")
    minimax_key = os.environ.get("MINIMAX_API_KEY", "").strip()
    has_minimax_key = bool(minimax_key and minimax_key != "sua_chave_aqui")
    return {
        "active_provider": active_provider,
        "available_providers": ["stepfun", "minimax", "kokoro", "qwen"],
        "stepfun": {
            "model": os.environ.get("STEPFUN_MODEL", "stepaudio-2.5-tts"),
            "voice": os.environ.get("STEPFUN_VOICE", "magnetic-voiced-male"),
            "speed": float(os.environ.get("STEPFUN_SPEED", "0.95")),
            "has_api_key": has_stepfun_key,
            "speakers": tts_list_speakers("stepfun"),
        },
        "minimax": {
            "model": os.environ.get("MINIMAX_MODEL", "speech-01-turbo"),
            "voice_id": os.environ.get("MINIMAX_VOICE_ID", "English_expressive_narrator"),
            "speed": float(os.environ.get("MINIMAX_SPEED", "0.95")),
            "group_id": os.environ.get("MINIMAX_GROUP_ID", ""),
            "has_api_key": has_minimax_key,
            "speakers": tts_list_speakers("minimax"),
        },
        "kokoro": {
            "repo_id": os.environ.get("KOKORO_REPO_ID", "hexgrad/Kokoro-82M"),
            "voice": os.environ.get("KOKORO_VOICE", "am_michael"),
            "lang": os.environ.get("KOKORO_LANG", "a"),
            "speakers": tts_list_speakers("kokoro"),
        },
    }


def _fallback_script(variant: TranslationVariant) -> str:
    """Best-effort script text for TTS when the request omits ``script``."""
    text = getattr(variant, "script", None)
    return (text or "").strip()


def _voice_and_subtitles_worker(
    slug: str,
    language_code: str = "en",
    speaker: str | None = None,
    provider: str | None = None,
    speed: float | None = None,
):
    """Background worker that runs Kokoro or MiniMax TTS and Faster-Whisper alignment end-to-end."""
    db = SessionLocal()
    try:
        project = _find_project(slug, db)
        if not project:
            logger.error("Voice worker: project %s not found", slug)
            return

        variant = _get_or_create_variant(db, project.id, language_code)
        ws = ensure_project_tree(project.slug)

        script_text = (variant.script or "").strip()
        if not script_text and (ws.root / "script.md").exists():
            script_text = (ws.root / "script.md").read_text(encoding="utf-8").strip()
            variant.script = script_text
            db.commit()

        if not script_text:
            logger.error("Voice worker: no script for %s", slug)
            project.status = VideoStatus.FAILED
            project.error_message = "No script found for voice generation."
            db.commit()
            return

        def _log(level: str, msg: str):
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            line = f"[{ts}] [{level}] {msg}\n"
            try:
                with open(ws.pipeline_log_path, "a", encoding="utf-8") as f:
                    f.write(line)
            except Exception:
                pass
            if level == "ERROR":
                logger.error(msg)
            else:
                logger.info(msg)

        active_provider = (
            provider
            or os.environ.get("TTS_PROVIDER")
            or os.environ.get("TTS_ENGINE", "stepfun")
        ).lower().strip()
        if active_provider == "stepfun":
            provider_display = "StepFun StepAudio"
        elif active_provider == "minimax":
            provider_display = "MiniMax"
        else:
            provider_display = "Kokoro"

        words_count = len(script_text.split())
        _log("INFO", f"Nó 02: Iniciando síntese neural {provider_display} TTS ({words_count} palavras)...")
        with _JOB_LOCK:
            ACTIVE_VOICE_JOBS[slug] = {
                "status": "running",
                "step": "tts",
                "provider": active_provider,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "error": None
            }
        project.status = VideoStatus.READY_FOR_AUDIO
        db.commit()

        storage_root = os.environ.get(
            "REDSUN_STORAGE_DIR",
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "storage"),
        )
        project_dir = os.path.join(storage_root, str(project.id))
        os.makedirs(project_dir, exist_ok=True)
        storage_audio_path = os.path.join(project_dir, f"audio_{language_code}.wav")
        ws_audio_path = ws.root / "audio" / "audio_main.wav"
        ws_audio_path.parent.mkdir(parents=True, exist_ok=True)

        t0 = time.time()
        result = tts_generate(
            script_text,
            storage_audio_path,
            speaker=speaker,
            provider=active_provider,
            speed=speed,
            slug=slug,
        )
        elapsed_tts = time.time() - t0

        shutil.copy2(result.wav_path, str(ws_audio_path))

        dur = result.duration_sec
        variant.audio_path = str(ws_audio_path)
        db.commit()
        _log("INFO", f"{provider_display} TTS: audio_main.wav gerado em {elapsed_tts:.1f}s — Duração: {int(dur // 60)}m {int(dur % 60)}s ({dur:.1f}s)")

        # Step 7: Faster-Whisper alignment
        _log("INFO", "Nó 02: Iniciando alinhamento de legendas e timestamps Faster-Whisper...")
        with _JOB_LOCK:
            if slug in ACTIVE_VOICE_JOBS:
                ACTIVE_VOICE_JOBS[slug]["step"] = "whisper"

        storage_timestamps_path = os.path.join(project_dir, f"timestamps_{language_code}.json")
        ws_subtitles_path = ws.root / "text" / "subtitles_en.json"
        ws_subtitles_path.parent.mkdir(parents=True, exist_ok=True)

        t1 = time.time()
        whisper_res = whisper_transcribe(
            str(ws_audio_path),
            storage_timestamps_path,
            language=language_code,
            reference_text=script_text,
        )
        elapsed_whisper = time.time() - t1

        shutil.copy2(whisper_res.timestamps_path, str(ws_subtitles_path))

        variant.timestamps_path = str(ws_subtitles_path)
        project.status = VideoStatus.ASSETS_READY
        project.error_message = None
        db.commit()

        with _JOB_LOCK:
            ACTIVE_VOICE_JOBS[slug] = {
                "status": "completed",
                "step": "completed",
                "provider": active_provider,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "error": None
            }

        _log("SUCCESS", f"Nó 02 Concluído ({provider_display} + Whisper): {whisper_res.word_count} palavras alinhadas em {whisper_res.segment_count} segmentos ({elapsed_whisper:.1f}s). Áudio e legendas sincronizados!")
    except Exception as exc:
        logger.exception("Voice worker failed for %s", slug)
        with _JOB_LOCK:
            ACTIVE_VOICE_JOBS[slug] = {
                "status": "failed",
                "step": "failed",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "error": str(exc)
            }
        try:
            p = _find_project(slug, db)
            if p:
                p.status = VideoStatus.FAILED
                p.error_message = f"voice: {exc}"
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


@app.post("/projects/{id_or_slug}/voice")
def trigger_project_voice(
    id_or_slug: str,
    payload: AudioGenerateRequest = Body(default_factory=AudioGenerateRequest),
    db: Session = Depends(get_db),
) -> dict:
    """Trigger Kokoro or MiniMax TTS + Faster-Whisper alignment for Node 2."""
    project = _find_project(id_or_slug, db)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project '{id_or_slug}' not found")

    variant = (
        db.query(TranslationVariant)
        .filter(
            TranslationVariant.project_id == project.id,
            TranslationVariant.language_code == payload.language_code,
        )
        .first()
    )
    ws = ensure_project_tree(project.slug)
    script_text = (payload.script or (variant.script if variant else None) or "").strip()
    if not script_text and (ws.root / "script.md").exists():
        script_text = (ws.root / "script.md").read_text(encoding="utf-8").strip()

    if not script_text:
        raise HTTPException(
            status_code=400,
            detail=f"No script found for project '{project.slug}'. Run Node 1 (POST /projects/{id_or_slug}/script) first.",
        )

    active_provider = (
        payload.provider
        or os.environ.get("TTS_PROVIDER")
        or os.environ.get("TTS_ENGINE", "stepfun")
    ).lower().strip()
    if active_provider == "stepfun":
        provider_display = "StepFun StepAudio"
    elif active_provider == "minimax":
        provider_display = "MiniMax"
    else:
        provider_display = "Kokoro"

    with _JOB_LOCK:
        ACTIVE_VOICE_JOBS[project.slug] = {
            "status": "running",
            "step": "tts",
            "provider": active_provider,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "error": None
        }

    # Start worker in background
    t = threading.Thread(
        target=_voice_and_subtitles_worker,
        args=(project.slug, payload.language_code, payload.speaker, payload.provider, payload.speed),
        daemon=True,
        name=f"VoiceWorker-{project.slug}",
    )
    t.start()

    return {
        "status": "processing",
        "project_id": project.id,
        "slug": project.slug,
        "provider": active_provider,
        "message": f"Síntese neural {provider_display} TTS iniciada em segundo plano para '{project.slug}'.",
        "word_count": len(script_text.split()),
    }


@app.post("/projects/{slug}/audio/generate", response_model=AudioRegenerateResponse)
def generate_project_narrative_audio(
    slug: str,
    payload: Optional[AudioGenerateRequest] = None,
    db: Session = Depends(get_db),
):
    """Regenerate master narrative audio (StepFun StepAudio) & Faster-Whisper subtitles for Node 2."""
    project = _find_project(slug, db)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")
    canonical_slug = project.slug or slug
    ws = ensure_project_tree(canonical_slug)

    language_code = (payload.language_code if payload and payload.language_code else "en")
    variant = (
        db.query(TranslationVariant)
        .filter(
            TranslationVariant.project_id == project.id,
            TranslationVariant.language_code == language_code,
        )
        .first()
    )

    script_text = (payload.script if payload and payload.script else None) or (variant.script if variant else None) or ""
    script_text = script_text.strip()

    if not script_text:
        candidates = [
            ws.root / "text" / f"script_{language_code}.md",
            ws.root / "text" / "script_en.md",
            ws.root / "text" / "script.md",
            ws.root / "script.md",
        ]
        for cand in candidates:
            if cand.exists():
                txt = cand.read_text(encoding="utf-8").strip()
                if txt:
                    script_text = txt
                    break

    if not script_text:
        raise HTTPException(
            status_code=400,
            detail=f"No script found for project '{canonical_slug}'. Generate script first in Node 1.",
        )

    if variant:
        variant.script = script_text
        db.commit()

    active_provider = (
        (payload.provider if payload and payload.provider else None)
        or os.environ.get("TTS_PROVIDER")
        or os.environ.get("TTS_ENGINE", "stepfun")
    ).lower().strip()

    speaker = (
        (payload.speaker if payload and payload.speaker else None)
        or (os.environ.get("STEPFUN_VOICE") if active_provider == "stepfun" else None)
        or "magnetic-voiced-male"
    )
    speed = (
        (payload.speed if payload and payload.speed is not None else None)
        or float(os.environ.get("STEPFUN_SPEED", "0.95"))
    )

    provider_display = (
        "StepFun StepAudio" if active_provider == "stepfun"
        else ("MiniMax" if active_provider == "minimax" else "Kokoro")
    )

    def _log(level: str, msg: str):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] [{level}] {msg}\n"
        try:
            with open(ws.pipeline_log_path, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass
        if level == "ERROR":
            logger.error(msg)
        else:
            logger.info(msg)

    words_count = len(script_text.split())
    _log("INFO", f"Nó 02: Regenerando áudio via {provider_display} ({words_count} palavras, voz '{speaker}', speed {speed})...")

    with _JOB_LOCK:
        ACTIVE_VOICE_JOBS[canonical_slug] = {
            "status": "running",
            "step": "tts",
            "provider": active_provider,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "error": None,
        }

    storage_root = os.environ.get(
        "REDSUN_STORAGE_DIR",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "storage"),
    )
    project_dir = os.path.join(storage_root, str(project.id))
    os.makedirs(project_dir, exist_ok=True)
    storage_audio_path = os.path.join(project_dir, f"audio_{language_code}.wav")
    ws_audio_path = ws.root / "audio" / "audio_main.wav"
    ws_audio_path.parent.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    try:
        # Step 6: TTS synthesis with pronunciation map
        result = tts_generate(
            script_text,
            storage_audio_path,
            speaker=speaker,
            provider=active_provider,
            speed=speed,
            slug=canonical_slug,
        )
        elapsed_tts = time.time() - t0

        shutil.copy2(result.wav_path, str(ws_audio_path))
        dur = result.duration_sec
        wav_size = ws_audio_path.stat().st_size
        wav_size_kb = wav_size / 1024.0
        wav_size_fmt = f"{wav_size / (1024 * 1024):.1f} MB" if wav_size >= 1024 * 1024 else f"{wav_size_kb:.1f} KB"
        dur_fmt = f"{int(dur // 60)}m {int(dur % 60):02d}s"

        if variant:
            variant.audio_path = str(ws_audio_path)
            db.commit()

        _log("INFO", f"{provider_display} TTS: audio_main.wav regerado em {elapsed_tts:.1f}s — Duração: {dur_fmt} ({dur:.1f}s, {wav_size_fmt})")

        # Step 7: Faster-Whisper alignment
        _log("INFO", "Nó 02: Alinhando legendas com Faster-Whisper...")
        with _JOB_LOCK:
            if canonical_slug in ACTIVE_VOICE_JOBS:
                ACTIVE_VOICE_JOBS[canonical_slug]["step"] = "whisper"

        storage_timestamps_path = os.path.join(project_dir, f"timestamps_{language_code}.json")
        ws_subtitles_text_path = ws.root / "text" / "subtitles_en.json"
        ws_subtitles_audio_path = ws.root / "audio" / "subtitles_en.json"
        ws_subtitles_text_path.parent.mkdir(parents=True, exist_ok=True)
        ws_subtitles_audio_path.parent.mkdir(parents=True, exist_ok=True)

        t1 = time.time()
        whisper_res = whisper_transcribe(
            str(ws_audio_path),
            storage_timestamps_path,
            language=language_code,
            reference_text=script_text,
        )
        elapsed_whisper = time.time() - t1

        shutil.copy2(whisper_res.timestamps_path, str(ws_subtitles_text_path))
        shutil.copy2(whisper_res.timestamps_path, str(ws_subtitles_audio_path))

        if variant:
            variant.timestamps_path = str(ws_subtitles_text_path)
        project.status = VideoStatus.ASSETS_READY
        project.error_message = None
        db.commit()

        with _JOB_LOCK:
            ACTIVE_VOICE_JOBS[canonical_slug] = {
                "status": "completed",
                "step": "completed",
                "provider": active_provider,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "error": None,
            }

        total_elapsed = time.time() - t0
        _log("SUCCESS", f"Nó 02 Concluído ({provider_display} + Whisper): {whisper_res.word_count} palavras em {whisper_res.segment_count} segmentos ({total_elapsed:.1f}s).")

        return AudioRegenerateResponse(
            status="success",
            slug=canonical_slug,
            message=f"Áudio narrativo ({provider_display}) e legendas sincronizadas com sucesso!",
            provider=active_provider,
            speaker=speaker,
            speed=speed,
            duration_seconds=round(dur, 2),
            duration_formatted=dur_fmt,
            wav_path=str(ws_audio_path),
            wav_size_bytes=wav_size,
            wav_size_formatted=wav_size_fmt,
            subtitles_count=whisper_res.segment_count,
            subtitles_path=str(ws_subtitles_text_path),
            elapsed_seconds=round(total_elapsed, 2),
        )
    except Exception as exc:
        logger.exception("Audio generation failed for %s: %s", canonical_slug, exc)
        with _JOB_LOCK:
            ACTIVE_VOICE_JOBS[canonical_slug] = {
                "status": "failed",
                "step": "failed",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "error": str(exc),
            }
        _log("ERROR", f"Falha na geração de áudio: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@app.api_route("/projects/{slug}/audio/main", methods=["GET", "HEAD"])
def get_project_audio_main(slug: str, db: Session = Depends(get_db)):
    """Stream or return the master narrative audio_main.wav file."""
    project = _find_project(slug, db)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")
    canonical_slug = project.slug or slug
    ws = ensure_project_tree(canonical_slug)
    audio_path = ws.root / "audio" / "audio_main.wav"
    if not audio_path.exists() or audio_path.stat().st_size < 1000:
        storage_root = os.environ.get("REDSUN_STORAGE_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "storage"))
        cand = Path(storage_root) / str(project.id) / "audio_en.wav"
        if cand.exists() and cand.stat().st_size > 1000:
            audio_path = cand
    if not audio_path.exists() or audio_path.stat().st_size < 1000:
        raise HTTPException(status_code=404, detail=f"audio_main.wav not found for project '{slug}'")
    return FileResponse(
        path=str(audio_path),
        media_type="audio/wav",
        filename=f"{canonical_slug}_audio_main.wav",
    )


@app.post("/projects/{id_or_slug}/audio", response_model=AudioJobResponse)
def generate_project_audio(
    id_or_slug: str,
    payload: AudioGenerateRequest = Body(default_factory=AudioGenerateRequest),
    db: Session = Depends(get_db),
) -> AudioJobResponse:
    """Run Qwen3-TTS / Kokoro on the given script and attach the WAV to the project."""
    project = _find_project(id_or_slug, db)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project '{id_or_slug}' not found")

    variant = (
        db.query(TranslationVariant)
        .filter(
            TranslationVariant.project_id == project.id,
            TranslationVariant.language_code == payload.language_code,
        )
        .first()
    )
    if variant is None:
        variant = TranslationVariant(
            project_id=project.id,
            language_code=payload.language_code,
            script=payload.script,
        )
        db.add(variant)
        db.commit()
        db.refresh(variant)

    storage_root = os.environ.get(
        "REDSUN_STORAGE_DIR",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "storage"),
    )
    project_dir = os.path.join(storage_root, str(project.id))
    os.makedirs(project_dir, exist_ok=True)
    audio_path = os.path.join(project_dir, f"audio_{payload.language_code}.wav")

    script_text = (payload.script or "").strip() or _fallback_script(variant)
    variant.script = script_text
    project.status = VideoStatus.READY_FOR_AUDIO
    db.commit()

    try:
        result = tts_generate(
            script_text,
            audio_path,
            speaker=payload.speaker,
            instruct=payload.instruct,
            provider=payload.provider,
            speed=payload.speed,
            slug=project.slug,
        )
    except Exception as exc:
        logger.exception("TTS failed for project %s", id_or_slug)
        project.status = VideoStatus.FAILED
        project.error_message = f"tts: {exc}"
        db.commit()
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    variant.audio_path = result.wav_path
    project.status = VideoStatus.ASSETS_READY
    db.commit()

    try:
        ws = ensure_project_tree(project.slug)
        shutil.copy2(result.wav_path, str(ws.root / "audio" / "audio_main.wav"))
    except Exception as exc:
        logger.warning("Could not sync audio to workspace: %s", exc)

    logger.info(
        "Project %s audio done: %s (%.2fs, %d chunk(s))",
        project.slug, result.wav_path, result.duration_sec, result.chunk_count,
    )

    return AudioJobResponse(
        project_id=project.id,
        language_code=payload.language_code,
        audio_path=result.wav_path,
        sample_rate=result.sample_rate,
        duration_sec=result.duration_sec,
        chunk_count=result.chunk_count,
        speaker=result.speaker,
        language=result.language,
        model_id=result.model_id,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints — LLM Content Engine (Phase 3)
# ─────────────────────────────────────────────────────────────────────────────
def _get_or_create_variant(
    db: Session, project_id: int, language_code: str, script: str | None = None
) -> TranslationVariant:
    """Resolve the ``TranslationVariant`` for (project, lang), creating one if absent."""
    variant = (
        db.query(TranslationVariant)
        .filter(
            TranslationVariant.project_id == project_id,
            TranslationVariant.language_code == language_code,
        )
        .first()
    )
    if variant is None:
        variant = TranslationVariant(
            project_id=project_id,
            language_code=language_code,
            script=script,
        )
        db.add(variant)
        db.commit()
        db.refresh(variant)
    return variant


@app.post("/projects/{id_or_slug}/script", response_model=LLMScriptResponse)
def generate_project_script(
    id_or_slug: str,
    payload: LLMScriptRequest = Body(default_factory=LLMScriptRequest),
    db: Session = Depends(get_db),
) -> LLMScriptResponse:
    """Generate the English narration script for a project.

    Requires the project to already exist. The Japanese source text is
    taken from the request body if provided, otherwise from
    ``project.source_text`` (set when the project was created).
    """
    project = _find_project(id_or_slug, db)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project '{id_or_slug}' not found")

    src = (payload.source_text_ja or project.source_text or "").strip()
    if not src:
        raise HTTPException(
            status_code=400,
            detail=(
                "No Japanese source text available. Pass `source_text_ja` "
                "in the request, or set it on the project via POST /projects."
            ),
        )

    keywords_csv = project.target_keywords or None
    target_kw = [s.strip() for s in keywords_csv.split(",") if s.strip()] if keywords_csv else None

    cfg = LLMConfig.from_env()
    if not cfg.is_configured:
        raise HTTPException(
            status_code=503,
            detail="OPENROUTER_API_KEY not configured on the server.",
        )

    with _JOB_LOCK:
        ACTIVE_SCRIPT_JOBS[project.slug] = {
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "error": None
        }

    try:
        rating = getattr(payload, "content_rating", None) or getattr(project, "content_rating", None) or "pg13_prestige"
        result = generate_script_from_sources(
            src,
            topic=project.title,
            target_keywords=target_kw,
            content_rating=rating,
            cfg=cfg,
        )
        with _JOB_LOCK:
            ACTIVE_SCRIPT_JOBS[project.slug] = {
                "status": "completed",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "error": None
            }
    except Exception as exc:
        logger.exception("Script generation failed for project %s", id_or_slug)
        with _JOB_LOCK:
            ACTIVE_SCRIPT_JOBS[project.slug] = {
                "status": "failed",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "error": str(exc)
            }
        project.status = VideoStatus.FAILED
        project.error_message = f"script: {exc}"
        db.commit()
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    variant = _get_or_create_variant(db, project.id, payload.language_code, script=result.text)
    variant.script = result.text
    project.status = VideoStatus.SCRIPTED
    db.commit()

    # Materialize script files to workspace so subsequent pipeline nodes find them
    try:
        ws = ensure_project_tree(project.slug)
        (ws.root / "script.md").write_text(result.text, encoding="utf-8")
        (ws.root / "text" / "script.md").write_text(result.text, encoding="utf-8")
        if src:
            (ws.root / "text" / "source.ja.txt").write_text(src, encoding="utf-8")
        logger.info("Persisted script.md in workspace for %s", project.slug)
    except Exception as exc:
        logger.warning("Could not persist script.md to workspace: %s", exc)

    logger.info(
        "Project %s script done: %d chars in %.1fs",
        project.slug, len(result.text), result.elapsed_sec,
    )
    return LLMScriptResponse(
        project_id=project.id,
        language_code=payload.language_code,
        script=result.text,
        model=result.model,
        elapsed_sec=result.elapsed_sec,
    )


@app.post("/projects/{id_or_slug}/review", response_model=LLMReviewResponse)
def review_project_script(
    id_or_slug: str,
    payload: LLMReviewRequest = Body(default_factory=LLMReviewRequest),
    db: Session = Depends(get_db),
) -> LLMReviewResponse:
    """Run the standalone script-review agent against the current script."""
    project = _find_project(id_or_slug, db)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project '{id_or_slug}' not found")

    project_id = project.id

    variant = (
        db.query(TranslationVariant)
        .filter(
            TranslationVariant.project_id == project.id,
            TranslationVariant.language_code == payload.language_code,
        )
        .first()
    )
    if variant is None or not variant.script:
        raise HTTPException(
            status_code=400,
            detail=f"No script for this language. Run POST /projects/{project.slug}/script first.",
        )

    cfg = LLMConfig.from_env()
    if not cfg.is_configured:
        raise HTTPException(
            status_code=503,
            detail="OPENROUTER_API_KEY not configured on the server.",
        )

    try:
        from llm_client import review_script_from_sources

        # Resolve source_type and atmosphere via priority cascade:
        #   1. Request payload (explicit override from caller)
        #   2. Project model (persisted per-project)
        #   3. Sensible generic default
        resolved_source_type = str(
            payload.source_type
            or project.source_type
            or "classical_kaidan"
        )
        resolved_atmosphere = str(
            payload.atmosphere
            or project.atmosphere
            or "dark and atmospheric"
        )

        result = review_script_from_sources(
            variant.script,
            topic=project.title,
            source_type=resolved_source_type,
            atmosphere=resolved_atmosphere,
            cfg=cfg,
        )
    except Exception as exc:
        logger.exception("Script review failed for project %s", project_id)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    from llm_client import extract_json_safely

    parsed = extract_json_safely(result.text)
    if not isinstance(parsed, dict):
        parsed = {
            "approved": False,
            "score": 0,
            "issues": [
                {
                    "severity": "critical",
                    "category": "review",
                    "message": "Reviewer returned non-JSON output; treat as failed review.",
                }
            ],
            "suggested_edits": [],
            "revised_script": None,
        }

    logger.info(
        "Project %s review done: approved=%s score=%s in %.1fs",
        project_id, parsed.get("approved"), parsed.get("score"), result.elapsed_sec,
    )

    if parsed.get("revised_script") and isinstance(parsed["revised_script"], str):
        variant.script = parsed["revised_script"].strip()
        db.commit()

    return LLMReviewResponse(
        project_id=project_id,
        language_code=payload.language_code,
        approved=bool(parsed.get("approved")),
        score=int(parsed.get("score", 0)),
        issues=list(parsed.get("issues", [])),
        suggested_edits=parsed.get("suggested_edits"),
        revised_script=parsed.get("revised_script"),
        model=result.model,
        elapsed_sec=result.elapsed_sec,
    )


@app.post("/projects/{project_id}/blog", response_model=LLMBlogResponse)
def generate_project_blog(
    project_id: int,
    payload: LLMBlogRequest,
    db: Session = Depends(get_db),
) -> LLMBlogResponse:
    """Generate an SEO blog article for a project from its English script."""
    project = db.query(VideoProject).filter(VideoProject.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")

    variant = (
        db.query(TranslationVariant)
        .filter(
            TranslationVariant.project_id == project_id,
            TranslationVariant.language_code == payload.language_code,
        )
        .first()
    )
    if variant is None or not variant.script:
        raise HTTPException(
            status_code=400,
            detail=(
                "No script for this language. Run "
                f"POST /projects/{project_id}/script first."
            ),
        )

    cfg = LLMConfig.from_env()
    if not cfg.is_configured:
        raise HTTPException(
            status_code=503,
            detail="OPENROUTER_API_KEY not configured on the server.",
        )

    keywords_csv = project.target_keywords or None
    target_kw = [s.strip() for s in keywords_csv.split(",") if s.strip()] if keywords_csv else None

    try:
        rating = getattr(project, "content_rating", None) or "pg13_prestige"
        result = generate_blog_article_from_script(
            variant.script,
            topic=project.title,
            target_keywords=target_kw,
            content_rating=rating,
            cfg=cfg,
        )
    except Exception as exc:
        logger.exception("Blog generation failed for project %s", project_id)
        project.status = VideoStatus.FAILED
        project.error_message = f"blog: {exc}"
        db.commit()
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # Quality assurance linter & media curation
    try:
        from redsun.services.blog_article_generator import BlogArticleGenerator
        generator = BlogArticleGenerator()
        curated_text = generator.generate_and_curate(
            slug=project.slug or f"project-{project_id}",
            title=project.title,
            script=variant.script,
            existing_draft=result.text,
            force_regenerate=False,
        )
        variant.blog_article = curated_text
    except Exception as exc:
        logger.warning("Blog QA/curator fallback for project %s: %s", project_id, exc)
        variant.blog_article = result.text

    db.commit()

    # Trigger Hook Gate 1.5: audit script_en.md and emit pronunciation_map.json
    try:
        from services.agent_dispatcher import dispatch_hermes_agent, dispatch_background
        slug = project.slug or f"project-{project_id}"
        prompt_gate_1_5 = (
            f"JingJing, o Nó 1 do projeto '{slug}' foi concluído com sucesso. "
            f"Execute o Gate 1.5 estritamente conforme o AGENTS.md: "
            f"1. Inspecione 'projects/{slug}/text/script_en.md' validando consistência e epílogo. "
            f"2. Extraia os termos japoneses e gere 'projects/{slug}/audio/pronunciation_map.json'. "
            f"Avise no chat quando o arquivo estiver gravado."
        )
        dispatch_background(dispatch_hermes_agent(prompt_gate_1_5))
    except Exception as exc:
        logger.warning("Could not dispatch JingJing Gate 1.5 hook: %s", exc)

    logger.info(
        "Project %s blog done: %d chars in %.1fs",
        project_id, len(variant.blog_article), result.elapsed_sec,
    )
    return LLMBlogResponse(
        project_id=project_id,
        language_code=payload.language_code,
        blog_article=variant.blog_article,
        model=result.model,
        elapsed_sec=result.elapsed_sec,
    )


@app.post("/projects/{id_or_slug}/shorts", response_model=LLMShortsResponse)
def generate_project_shorts(
    id_or_slug: str,
    payload: LLMShortsRequest,
    db: Session = Depends(get_db),
) -> LLMShortsResponse:
    """Identify 3–5 short-clip candidates from the project's English script."""
    project = _find_project(id_or_slug, db)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project '{id_or_slug}' not found")

    variant = (
        db.query(TranslationVariant)
        .filter(
            TranslationVariant.project_id == project.id,
            TranslationVariant.language_code == payload.language_code,
        )
        .first()
    )
    if variant is None or not variant.script:
        raise HTTPException(
            status_code=400,
            detail=(
                "No script for this language. Run "
                f"POST /projects/{project.slug}/script first."
            ),
        )

    cfg = LLMConfig.from_env()
    if not cfg.is_configured:
        raise HTTPException(
            status_code=503,
            detail="OPENROUTER_API_KEY not configured on the server.",
        )

    try:
        result = identify_short_clips(
            variant.script,
            topic=project.title,
            max_clips=payload.max_clips,
            cfg=cfg,
        )
    except Exception as exc:
        logger.exception("Shorts identification failed for project %s", id_or_slug)
        project.status = VideoStatus.FAILED
        project.error_message = f"shorts: {exc}"
        db.commit()
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    parsed = result.parsed or {}
    variant.short_clips_json = json.dumps(parsed, ensure_ascii=False)
    if project.status == VideoStatus.FAILED:
        project.status = VideoStatus.ASSETS_READY
        logger.info(
            "Project %s recovered from FAILED -> ASSETS_READY after successful shorts",
            project.slug,
        )
    project.error_message = None
    db.commit()

    logger.info(
        "Project %s shorts done: %d clips in %.1fs",
        project.slug, len(parsed.get("clips", [])), result.elapsed_sec,
    )
    return LLMShortsResponse(
        project_id=project.id,
        language_code=payload.language_code,
        clips=parsed.get("clips", []),
        pacing_notes=parsed.get("pacing_notes"),
        model=result.model,
        elapsed_sec=result.elapsed_sec,
    )


@app.post(
    "/projects/{id_or_slug}/transcribe",
    response_model=TranscribeResponse,
)
def transcribe_project_audio(
    id_or_slug: str,
    payload: TranscribeRequest = Body(default_factory=TranscribeRequest),
    db: Session = Depends(get_db),
) -> TranscribeResponse:
    """Transcribe the project's WAV to word-level timestamps JSON."""
    project = _find_project(id_or_slug, db)
    if not project:
        raise HTTPException(
            status_code=404, detail=f"Project '{id_or_slug}' not found"
        )

    variant = (
        db.query(TranslationVariant)
        .filter(
            TranslationVariant.project_id == project.id,
            TranslationVariant.language_code == payload.language_code,
        )
        .first()
    )
    if variant is None or not variant.audio_path:
        raise HTTPException(
            status_code=400,
            detail=(
                f"No audio for language '{payload.language_code}'. "
                f"Run POST /projects/{project.slug}/audio first."
            ),
        )

    storage_root = os.environ.get(
        "REDSUN_STORAGE_DIR",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "storage"),
    )
    project_dir = os.path.join(storage_root, str(project.id))
    os.makedirs(project_dir, exist_ok=True)
    timestamps_path = os.path.join(
        project_dir, f"timestamps_{payload.language_code}.json"
    )

    if not os.path.exists(variant.audio_path):
        raise HTTPException(
            status_code=500,
            detail=(
                f"audio_path on variant does not exist on disk: {variant.audio_path}"
            ),
        )

    ref_script = (variant.script or "").strip()
    if not ref_script:
        ws_script = ws.root / "text" / "script_en.md"
        if ws_script.exists():
            ref_script = ws_script.read_text(encoding="utf-8").strip()

    try:
        result = whisper_transcribe(
            variant.audio_path,
            timestamps_path,
            language=payload.language,
            reference_text=ref_script,
        )
    except Exception as exc:
        logger.exception("Whisper failed for project %s", id_or_slug)
        project.status = VideoStatus.FAILED
        project.error_message = f"transcribe: {exc}"
        db.commit()
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    # Persist + recover
    variant.timestamps_path = result.timestamps_path
    if project.status == VideoStatus.FAILED:
        project.status = VideoStatus.ASSETS_READY
        logger.info(
            "Project %s recovered from FAILED -> ASSETS_READY after successful transcribe",
            project.slug,
        )
    project.error_message = None
    db.commit()

    try:
        ws = ensure_project_tree(project.slug)
        shutil.copy2(result.timestamps_path, str(ws.root / "text" / "subtitles_en.json"))
    except Exception as exc:
        logger.warning("Could not sync subtitles to workspace: %s", exc)

    logger.info(
        "Project %s transcribe done: %d words / %d segments in %.1fs -> %s",
        project.slug, result.word_count, result.segment_count,
        result.elapsed_sec, result.timestamps_path,
    )

    return TranscribeResponse(
        project_id=project.id,
        language_code=payload.language_code,
        timestamps_path=result.timestamps_path,
        word_count=result.word_count,
        segment_count=result.segment_count,
        duration_sec=result.duration_sec,
        elapsed_sec=result.elapsed_sec,
        model_size=result.model_size,
        language=result.language,
    )



# ─────────────────────────────────────────────────────────────────────────────
# Endpoints — image prompts (Phase 3.5: LLM-driven visual prompts per scene)
# ─────────────────────────────────────────────────────────────────────────────

class ImagePromptsRequest(BaseModel):
    """Input shape for ``POST /projects/{id}/image-prompts``."""

    language_code: str = Field("en", min_length=2, max_length=10)
    theme_hint: str = Field(
        "",
        description=(
            "Theme name for image generation (e.g. 'yuki_onna', 'mujina'). "
            "If empty, falls back to project title."
        ),
    )


class ImagePromptsResponse(BaseModel):
    project_id: int
    language_code: str
    scene_count: int
    prompts: list[dict]
    model: str
    elapsed_sec: float


@app.post(
    "/projects/{project_id}/image-prompts",
    response_model=ImagePromptsResponse,
)
def generate_image_prompts(
    project_id: int,
    payload: ImagePromptsRequest,
    db: Session = Depends(get_db),
) -> ImagePromptsResponse:
    """Generate per-scene image prompts using the LLM (theme-aware).

    Reads the project's script, splits it into scenes, and calls the LLM
    to produce a visual prompt for each scene that is aware of the project's
    theme (yokai entity, atmosphere, style).
    """
    project = db.query(VideoProject).filter(VideoProject.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")

    variant = (
        db.query(TranslationVariant)
        .filter(
            TranslationVariant.project_id == project_id,
            TranslationVariant.language_code == payload.language_code,
        )
        .first()
    )
    if variant is None or not str(variant.script or ""):
        raise HTTPException(
            status_code=400,
            detail=f"No script for this language. Run POST /projects/{project_id}/script first.",
        )

    cfg = LLMConfig.from_env()
    if not cfg.is_configured:
        raise HTTPException(
            status_code=503,
            detail="OPENROUTER_API_KEY not configured on the server.",
        )

    from generate_video import split_into_scenes, build_image_prompts_for_script

    theme_name = str(payload.theme_hint or project.title or "japanese horror")
    # Dynamic scene count: ~1 image per 250 chars of script, min 12, max 50.
    # For a 6000-char script (~15 min video) this yields ~24 scenes (~37s/image).
    _script_text = str(variant.script)
    _dynamic_max = max(12, min(50, len(_script_text) // 250))
    scenes_text = split_into_scenes(_script_text, max_scenes=_dynamic_max)
    logger.info(
        "Project %s image-prompts: %d scenes (dynamic_max=%d from %d chars), theme=%s",
        project_id, len(scenes_text), _dynamic_max, len(_script_text), theme_name,
    )

    import time as _time
    t0 = _time.time()
    try:
        prompts = build_image_prompts_for_script(scenes_text, theme_name)
    except Exception as exc:
        logger.exception("Image prompt generation failed for project %s", project_id)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    elapsed = _time.time() - t0
    logger.info(
        "Project %s image-prompts done: %d prompts in %.1fs",
        project_id, len(prompts), elapsed,
    )

    return ImagePromptsResponse(
        project_id=project_id,
        language_code=payload.language_code,
        scene_count=len(prompts),
        prompts=prompts,
        model=cfg.model,
        elapsed_sec=elapsed,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints — generate images via ComfyUI (Phase 4: image generation)
# ─────────────────────────────────────────────────────────────────────────────

class GenerateImagesRequest(BaseModel):
    """Input shape for ``POST /projects/{id}/generate-images``."""

    language_code: str = Field("en", min_length=2, max_length=10)
    theme_hint: str = Field(
        "",
        description="Theme name for image prompts (e.g. 'yuki_onna'). Falls back to project title.",
    )
    model: str = Field(
        "epicrealism",
        description="ComfyUI model to use (epicrealism, juggernaut, zimage).",
    )
    base_seed: int = Field(
        42,
        description="Base seed for reproducibility (each scene gets base+index).",
    )


class GenerateImagesResponse(BaseModel):
    project_id: int
    scene_count: int
    generated_count: int
    failed_count: int
    image_paths: list[str]
    character_scene_count: int = 0
    character_scenes: list[dict] = field(default_factory=list)
    elapsed_sec: float


@app.post(
    "/projects/{project_id}/generate-images",
    response_model=GenerateImagesResponse,
)
def generate_project_images(
    project_id: int,
    payload: GenerateImagesRequest,
    db: Session = Depends(get_db),
) -> GenerateImagesResponse:
    """Generate scene images via ComfyUI using LLM-generated prompts.

    This endpoint:
    1. Reads the project script and splits into scenes
    1. Generates image prompts via LLM (theme-aware)
    2. Image generation happens OUTSIDE this endpoint — the gemini-assistant
       Chrome extension (operated manually by Isamu) writes assets to
       projects/<slug>/images/, then the pipeline resumes via gate_image_handoff.
       See PIPELINE_STATE.md for the canonical pipeline state.
    """
    project = db.query(VideoProject).filter(VideoProject.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")

    variant = (
        db.query(TranslationVariant)
        .filter(
            TranslationVariant.project_id == project_id,
            TranslationVariant.language_code == payload.language_code,
        )
        .first()
    )
    if variant is None or not str(variant.script or ""):
        raise HTTPException(
            status_code=400,
            detail=f"No script for this language. Run POST /projects/{project_id}/script first.",
        )

    storage_root = os.environ.get(
        "REDSUN_STORAGE_DIR",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "storage"),
    )
    image_dir = os.path.join(storage_root, str(project_id), "images")
    os.makedirs(image_dir, exist_ok=True)

    from generate_video import split_into_scenes, build_image_prompts_for_script

    theme_name = str(payload.theme_hint or project.title or "japanese horror")
    # Dynamic scene count: ~1 image per 250 chars, min 12, max 50.
    _script_text = str(variant.script)
    _dynamic_max = max(12, min(50, len(_script_text) // 250))
    scenes_text = split_into_scenes(_script_text, max_scenes=_dynamic_max)

    logger.info(
        "Project %s generate-images: %d scenes (dynamic_max=%d from %d chars), theme=%s, model=%s",
        project_id, len(scenes_text), _dynamic_max, len(_script_text), theme_name, payload.model,
    )

    # Generate prompts via LLM
    t0 = time.time()
    prompts = build_image_prompts_for_script(scenes_text, theme_name)

    # Image generation moved off the API surface on 2026-09-13.
    # Images are now generated via the gemini-assistant Chrome extension,
    # operated manually by Isamu. The pipeline PAUSES at gate_image_handoff and
    # resumes once assets appear in projects/<slug>/images/.
    # This endpoint is kept as a deprecated stub that returns 410 Gone so any
    # stale client gets a clear, machine-readable signal.
    logger.warning(
        "POST /projects/%s/generate-images called but the endpoint is deprecated. "
        "Images are now generated via gemini-assistant (Chrome extension). "
        "See PIPELINE_STATE.md.", project_id,
    )
    raise HTTPException(
        status_code=410,
        detail=(
            "generate-images endpoint removed 2026-09-15. The pipeline no longer "
            "calls ComfyUI. Images are produced via the gemini-assistant Chrome "
            "extension (operated manually by Isamu). See PIPELINE_STATE.md for "
            "the canonical pipeline state and gate_image_handoff docs."
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints — render video via Remotion (Phase 7: final render)
# ─────────────────────────────────────────────────────────────────────────────

class RenderVideoRequest(BaseModel):
    """Input shape for ``POST /projects/{id}/render``."""

    language_code: str = Field("en", min_length=2, max_length=10)
    skip_sync: bool = Field(
        False,
        description="If true, skip sync_project_assets.sh (use when already synced).",
    )
    chunked: bool = Field(
        True,
        description="Use chunked rendering (recommended for videos > 5 min).",
    )
    chunk_seconds: int = Field(
        30,
        description="Chunk duration in seconds (default: 30s = 900 frames @ 30fps).",
    )


class RenderVideoResponse(BaseModel):
    project_id: int
    slug: str
    status: str
    output_path: str | None = None
    chunks_rendered: int = 0
    total_chunks: int = 0
    elapsed_sec: float
    message: str


@app.post(
    "/projects/{id_or_slug}/render",
    response_model=RenderVideoResponse,
)
def render_project_video(
    id_or_slug: str,
    payload: RenderVideoRequest,
    db: Session = Depends(get_db),
) -> RenderVideoResponse:
    """Render the final video via Remotion with robust asset prep and background execution."""
    project = _find_project(id_or_slug, db)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project '{id_or_slug}' not found")

    project_id = project.id
    slug = str(project.slug or f"project-{project_id}")

    from redsun.services.remotion_runner import start_remotion_render

    t0 = time.time()
    try:
        render_job = start_remotion_render(slug, concurrency=2)
        elapsed = time.time() - t0
        manifest = render_job.get("manifest") or {}
        image_count = manifest.get("image_count", 0)
        duration_sec = manifest.get("duration_seconds", 0)
        chunk_sec = payload.chunk_seconds or 30
        total_chunks = max(1, int((duration_sec + chunk_sec - 1) // chunk_sec)) if duration_sec > 0 else 1

        return RenderVideoResponse(
            project_id=project_id,
            slug=slug,
            status="rendering",
            output_path=f"projects/{slug}/video/{slug}_master.mp4",
            chunks_rendered=0,
            total_chunks=total_chunks,
            elapsed_sec=elapsed,
            message=f"Renderização Remotion iniciada com sucesso ({image_count} imagens, ~{total_chunks} chunks).",
        )
    except Exception as exc:
        logger.exception("Failed to start Remotion render for %s: %s", slug, exc)
        raise HTTPException(status_code=500, detail=str(exc))


class CancelRenderResponse(BaseModel):
    success: bool
    message: str
    slug: str | None = None
    status: str | None = None


@app.post(
    "/projects/{id_or_slug}/render/cancel",
    response_model=CancelRenderResponse,
)
def cancel_project_render(
    id_or_slug: str,
    db: Session = Depends(get_db),
) -> CancelRenderResponse:
    """Kill active Remotion render processes, clean temp files, and restore project status."""
    project = _find_project(id_or_slug, db)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project '{id_or_slug}' not found")

    slug = str(project.slug or f"project-{project.id}")
    from redsun.services.remotion_runner import cancel_remotion_render
    result = cancel_remotion_render(slug)
    return CancelRenderResponse(
        success=result.get("success", True),
        message=result.get("message", "Renderização cancelada com sucesso."),
        slug=result.get("slug", slug),
        status=result.get("status"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints — render manifest (Phase 5: bridge backend → Remotion)
# ─────────────────────────────────────────────────────────────────────────────
class RenderManifestRequest(BaseModel):
    """Input shape for ``POST /projects/{id}/render-manifest``."""

    language_code: str = Field("en", min_length=2, max_length=10)
    # Optional: how many images to expect in the manifest. Defaults to all
    # assets fetched for the project's theme.
    image_count: int | None = Field(
        None,
        ge=1, le=50,
        description="Number of images to include (default: all assets for theme).",
    )


class RenderManifestResponse(BaseModel):
    project_id: int
    language_code: str
    slug: str
    title_en: str = ''
    title_ja: str = ''
    duration_seconds: float
    fps: int
    width: int
    height: int
    audio_filename: str
    timestamps_filename: str
    image_theme: str
    image_count: int
    image_filenames: list[str]
    bgm_filename: str | None = None
    bgm_mood: str | None = None
    manifest_path: str


def _build_render_manifest(
    project: VideoProject,
    variant: TranslationVariant,
    *,
    image_count: int | None,
    storage_root: str,
) -> dict:
    """Construct the manifest.json dict that Remotion reads at render time.

    Convention: every file is referenced by *basename* relative to
    ``render-remotion/public/`` — that's the directory Remotion's dev server
    serves via ``staticFile()``.
    """
    # Check if project has gemini-assistant/output images first
    image_filenames: list[str] = []
    image_theme = "historical_dark_fantasy"
    try:
        from redsun.workspace import ProjectWorkspace
        ws = ProjectWorkspace.from_slug(project.slug)
        output_dir = ws.gemini_output_dir
        if output_dir.exists():
            real_images = sorted([
                f.name for f in output_dir.iterdir()
                if f.is_file() and f.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"} and not f.name.startswith(".")
            ])
            if real_images:
                if image_count is not None:
                    real_images = real_images[:image_count]
                image_filenames = real_images
    except Exception as exc:
        logger.debug("Could not check gemini-assistant images: %s", exc)

    if not image_filenames:
        db = SessionLocal()
        try:
            images = (
                db.query(AssetImage)
                .filter(AssetImage.project_id == project.id)
                .order_by(AssetImage.id)
                .all()
            )
            if not images:
                # Fallback: most-frequent theme across the library
                from sqlalchemy import func

                row = (
                    db.query(AssetImage.theme, func.count(AssetImage.id).label("c"))
                    .group_by(AssetImage.theme)
                    .order_by(func.count(AssetImage.id).desc())
                    .first()
                )
                if row:
                    images = (
                        db.query(AssetImage)
                        .filter(AssetImage.theme == row[0])
                        .order_by(AssetImage.id)
                        .all()
                    )
        finally:
            db.close()

        if image_count is not None:
            images = images[:image_count]

        image_filenames = [Path(img.relative_path).name for img in images]
        image_theme = images[0].theme if images else "default"

    # Audio + timestamps — file basename only.
    audio_filename = Path(variant.audio_path).name if variant.audio_path else "audio.wav"
    timestamps_filename = Path(variant.timestamps_path).name if variant.timestamps_path else "timestamps.json"

    # Compute duration from timestamps.
    duration_sec = 60.0
    if variant.timestamps_path and os.path.exists(variant.timestamps_path):
        with open(variant.timestamps_path) as f:
            words = json.load(f)
        if words:
            duration_sec = max(float(w["end"]) for w in words) + 2.0

    # Optional BGM: pick by slug keyword (e.g. "yuki-onna" -> "mystery")
    bgm_filename = None
    bgm_mood = None
    try:
        from bgm_picker import pick_bgm_by_mood, MOOD_ALIASES

        # Heuristic: map slug to mood
        slug_lower = (project.slug or "").lower()
        mood = None
        for keyword, mapped_mood in MOOD_ALIASES.items():
            if keyword in slug_lower:
                mood = mapped_mood
                break
        # Also peek at target_keywords
        if not mood and project.target_keywords:
            kw_lower = project.target_keywords.lower()
            for keyword, mapped_mood in MOOD_ALIASES.items():
                if keyword in kw_lower:
                    mood = mapped_mood
                    break
        # Default: mystery (horror channel — prefer dark atmospheric BGM)
        if not mood:
            mood = "mystery"

        bgm = pick_bgm_by_mood(mood, target_duration_sec=duration_sec)
        bgm_filename = Path(bgm.filename).name
        bgm_mood = bgm.mood
    except Exception as exc:
        logger.warning("BGM picker failed for project %s: %s", project.id, exc)

    return {
        "project_id": project.id,
        "slug": project.slug,
        "language_code": variant.language_code,
        "title_en": project.title,
        "title_ja": getattr(project, 'title_ja', '') or '',
        "duration_seconds": duration_sec,
        "fps": 30,
        "width": 1920,
        "height": 1080,
        "audio_filename": audio_filename,
        "timestamps_filename": timestamps_filename,
        "image_theme": image_theme,
        "image_count": len(image_filenames),
        "image_filenames": image_filenames,
        "bgm_filename": bgm_filename,
        "bgm_mood": bgm_mood,
    }


@app.post(
    "/projects/{project_id}/render-manifest",
    response_model=RenderManifestResponse,
)
def generate_render_manifest(
    project_id: int,
    payload: RenderManifestRequest,
    db: Session = Depends(get_db),
) -> RenderManifestResponse:
    """Generate ``storage/{id}/manifest.json`` for Remotion consumption.

    Phase 5: this is the contract between the Python backend and the
    Remotion frontend. The frontend reads ``manifest.json`` at runtime to
    discover audio + timestamps + image filenames + duration.

    The manifest is gitignored (storage/) — the operator copies files to
    ``render-remotion/public/`` separately (or runs the symlink helper).
    """
    project = db.query(VideoProject).filter(VideoProject.id == project_id).first()
    if not project:
        raise HTTPException(
            status_code=404, detail=f"Project {project_id} not found"
        )

    variant = (
        db.query(TranslationVariant)
        .filter(
            TranslationVariant.project_id == project_id,
            TranslationVariant.language_code == payload.language_code,
        )
        .first()
    )
    if variant is None or not variant.audio_path or not variant.timestamps_path:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Project {project_id} (lang={payload.language_code}) is not "
                "ready for render: needs both audio_path and timestamps_path. "
                "Run /audio and /transcribe first."
            ),
        )

    storage_root = os.environ.get(
        "REDSUN_STORAGE_DIR",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "storage"),
    )
    manifest = _build_render_manifest(
        project, variant,
        image_count=payload.image_count,
        storage_root=storage_root,
    )

    # Persist to storage/{id}/manifest.json
    manifest_path = os.path.join(
        storage_root, str(project_id), "manifest.json"
    )
    os.makedirs(os.path.dirname(manifest_path), exist_ok=True)
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    logger.info(
        "Project %s manifest written: %d images, %.1fs, audio=%s",
        project_id, manifest["image_count"], manifest["duration_seconds"],
        manifest["audio_filename"],
    )

    return RenderManifestResponse(
        project_id=manifest["project_id"],
        language_code=manifest["language_code"],
        slug=manifest["slug"],
        title_en=manifest.get("title_en", ""),
        title_ja=manifest.get("title_ja", ""),
        duration_seconds=manifest["duration_seconds"],
        fps=manifest["fps"],
        width=manifest["width"],
        height=manifest["height"],
        audio_filename=manifest["audio_filename"],
        timestamps_filename=manifest["timestamps_filename"],
        image_theme=manifest["image_theme"],
        image_count=manifest["image_count"],
        image_filenames=manifest["image_filenames"],
        bgm_filename=manifest.get("bgm_filename"),
        bgm_mood=manifest.get("bgm_mood"),
        manifest_path=manifest_path,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints — blog output & Pinterest queue (Phase 6)
# ─────────────────────────────────────────────────────────────────────────────
class BlogExportRequest(BaseModel):
    """Input shape for ``POST /projects/{id}/blog-export``."""

    language_code: str = Field("en", min_length=2, max_length=10)
    overwrite: bool = Field(
        False,
        description="If true, overwrite an existing blog_articles/<slug>.<lang>.md.",
    )
    destination_url: str | None = Field(
        None,
        description="Canonical URL the blog post will live at (used in Pinterest pin).",
    )


class BlogExportResponse(BaseModel):
    project_id: int
    language_code: str
    slug: str
    file_path: str
    file_size_bytes: int
    frontmatter_valid: bool
    frontmatter_missing: list[str]
    frontmatter_errors: list[str]
    pinterest_queue_path: str | None = None


@app.post(
    "/projects/{project_id}/blog-export",
    response_model=BlogExportResponse,
)
def export_blog_article(
    project_id: int,
    payload: BlogExportRequest,
    db: Session = Depends(get_db),
) -> BlogExportResponse:
    """Export the project's blog article to ``blog_articles/<slug>.<lang>.md``.

    Phase 6: turns the LLM-generated blog text (with YAML frontmatter) into
    a publishable Markdown file. The frontmatter is parsed, validated, and
    re-serialized to catch any structural issues. Missing keys are filled
    with safe defaults so the file is always loadable downstream.

    Side effect: also writes a Pinterest-pin metadata JSON to
    ``pinterest_queue/<slug>.<lang>.json`` for operator review.
    """
    from blog_export import (
        build_pinterest_metadata,
        parse_frontmatter,
        write_blog_article,
        write_pinterest_queue,
    )

    project = db.query(VideoProject).filter(VideoProject.id == project_id).first()
    if not project:
        raise HTTPException(
            status_code=404, detail=f"Project {project_id} not found"
        )

    variant = (
        db.query(TranslationVariant)
        .filter(
            TranslationVariant.project_id == project_id,
            TranslationVariant.language_code == payload.language_code,
        )
        .first()
    )
    if variant is None or not variant.blog_article:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Project {project_id} (lang={payload.language_code}) has no blog_article. "
                "Run POST /projects/{id}/blog first."
            ),
        )

    # 1) Export the blog article
    file_path = write_blog_article(
        variant.blog_article,
        slug=project.slug or f"project-{project_id}",
        lang=payload.language_code,
        overwrite=payload.overwrite,
    )

    # 2) Generate Pinterest metadata side-effect
    fm, body = parse_frontmatter(variant.blog_article)
    pinterest_path = None
    if fm.is_valid or not fm.missing:
        # Only queue Pinterest if we have a slug; if FM is missing slug,
        # the article filename still works (we filled it with the project slug).
        from blog_export import fill_default_frontmatter

        filled_fm_dict = fill_default_frontmatter(fm, slug=project.slug,
                                                    lang=payload.language_code)
        from blog_export import Frontmatter
        filled_fm = Frontmatter(
            raw=filled_fm_dict,
            is_valid=True,
            missing=[],
            errors=[],
        )
        try:
            pinterest_path = write_pinterest_queue(
                filled_fm,
                body_excerpt=body[:400],
                destination_url=payload.destination_url or "",
            )
        except Exception as exc:
            logger.warning("Pinterest queue write failed for project %s: %s",
                            project_id, exc)

    logger.info(
        "Project %s blog exported: %s (frontmatter valid=%s)",
        project_id, file_path, fm.is_valid,
    )

    return BlogExportResponse(
        project_id=project_id,
        language_code=payload.language_code,
        slug=project.slug or f"project-{project_id}",
        file_path=str(file_path),
        file_size_bytes=file_path.stat().st_size,
        frontmatter_valid=fm.is_valid,
        frontmatter_missing=fm.missing,
        frontmatter_errors=fm.errors,
        pinterest_queue_path=str(pinterest_path) if pinterest_path else None,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints — drafts (Phase 6: review queue)
# ─────────────────────────────────────────────────────────────────────────────
class DraftItem(BaseModel):
    """One project that has assets ready but is not yet exported/published."""

    project_id: int
    title: str
    slug: str
    status: str
    language_code: str
    has_audio: bool
    has_timestamps: bool
    has_blog: bool
    has_blog_article_file: bool
    has_pinterest_queue: bool
    has_video: bool


class DraftListResponse(BaseModel):
    drafts: list[DraftItem]
    total: int


@app.get(
    "/drafts",
    response_model=DraftListResponse,
)
def list_drafts(db: Session = Depends(get_db)) -> DraftListResponse:
    """List projects whose assets are ready for review/export.

    Phase 6: surfaces everything that's 'done' (assets_ready status with
    audio + timestamps) but hasn't been exported as a blog post or queued
    for Pinterest yet. Operator reviews this list and runs /blog-export
    on each before publishing.
    """
    from blog_export import BLOG_DIR, PINTEREST_DIR

    drafts: list[DraftItem] = []
    for project in db.query(VideoProject).all():
        for variant in project.translations:
            slug = project.slug or f"project-{project.id}"
            # The LLM-generated blog may have a slightly different slug
            # (e.g. it drops "the" or "a" articles). We probe both.
            from blog_export import parse_frontmatter

            candidate_slugs = [slug]
            if variant.blog_article:
                fm, _ = parse_frontmatter(variant.blog_article)
                if fm.is_valid and fm.slug and fm.slug != slug:
                    candidate_slugs.append(fm.slug)

            has_blog_file = any(
                (BLOG_DIR / f"{s}.{variant.language_code}.md").exists()
                for s in candidate_slugs
            )
            has_pin = any(
                (PINTEREST_DIR / f"{s}.{variant.language_code}.json").exists()
                for s in candidate_slugs
            )
            has_video = bool(variant.output_video_path and
                             os.path.exists(variant.output_video_path))
            drafts.append(
                DraftItem(
                    project_id=project.id,
                    title=project.title,
                    slug=slug,
                    status=project.status.value,
                    language_code=variant.language_code,
                    has_audio=bool(variant.audio_path),
                    has_timestamps=bool(variant.timestamps_path),
                    has_blog=bool(variant.blog_article),
                    has_blog_article_file=has_blog_file,
                    has_pinterest_queue=has_pin,
                    has_video=has_video,
                )
            )
    # Sort: most-incomplete first (so operator sees what still needs work)
    drafts.sort(
        key=lambda d: (
            not d.has_video,
            not d.has_blog_article_file,
            not d.has_pinterest_queue,
            d.project_id,
        )
    )
    return DraftListResponse(drafts=drafts, total=len(drafts))


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints — output video registration (Phase 6 helper)
# ─────────────────────────────────────────────────────────────────────────────
class OutputVideoRequest(BaseModel):
    """Set a variant's output_video_path to a file on disk.

    The render orchestrator (Remotion) writes the MP4 to
    ``storage/{id}/<slug>.mp4`` but doesn't touch the DB. This endpoint
    is how the operator (or future orchestrator script) records that the
    video exists for downstream consumers like ``/drafts``.
    """

    language_code: str = Field("en", min_length=2, max_length=10)
    video_path: str = Field(..., description="Absolute path to the rendered MP4.")
    # Optional: also set status to assets_ready (only if currently failed)
    recover_status: bool = Field(True, description="If true and project is FAILED, recover to ASSETS_READY.")


class OutputVideoResponse(BaseModel):
    project_id: int
    language_code: str
    video_path: str
    file_size_bytes: int
    project_status: str


@app.post(
    "/projects/{project_id}/output-video",
    response_model=OutputVideoResponse,
)
def register_output_video(
    project_id: int,
    payload: OutputVideoRequest,
    db: Session = Depends(get_db),
) -> OutputVideoResponse:
    """Register a rendered video's path on the variant.

    Phase 6: this is a thin endpoint for the render step. It validates the
    file exists, persists the path, and optionally recovers project status.
    """
    video_path = Path(payload.video_path)
    if not video_path.exists():
        raise HTTPException(
            status_code=400,
            detail=f"Video file does not exist: {video_path}",
        )

    project = db.query(VideoProject).filter(VideoProject.id == project_id).first()
    if not project:
        raise HTTPException(
            status_code=404, detail=f"Project {project_id} not found"
        )

    variant = (
        db.query(TranslationVariant)
        .filter(
            TranslationVariant.project_id == project_id,
            TranslationVariant.language_code == payload.language_code,
        )
        .first()
    )
    if variant is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No variant for project {project_id} / {payload.language_code}"
            ),
        )

    variant.output_video_path = str(video_path)
    if payload.recover_status and project.status == VideoStatus.FAILED:
        project.status = VideoStatus.ASSETS_READY
        logger.info(
            "Project %s recovered from FAILED -> ASSETS_READY after output-video",
            project_id,
        )
    project.error_message = None
    db.commit()

    logger.info(
        "Project %s output_video_path set: %s (%.1fMB)",
        project_id, video_path, video_path.stat().st_size / 1024 / 1024,
    )

    return OutputVideoResponse(
        project_id=project_id,
        language_code=payload.language_code,
        video_path=str(video_path),
        file_size_bytes=video_path.stat().st_size,
        project_status=project.status.value,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints — post kit (Phase 6+)
# ─────────────────────────────────────────────────────────────────────────────
class PostKitRequest(BaseModel):
    """Generate the post-publishing kit for a project.

    Produces 5 files in ``post_kits/<slug>.<lang>/``:
      - youtube_description.md   (YouTube video description)
      - youtube_pinned_comment.md (engagement hook, pinned comment)
      - social_captions.md        (Twitter, Instagram, TikTok)
      - thumbnail_brief.md        (Affinity designer brief)
      - post_metadata.json        (consolidated, ready for upload scripts)
    """

    language_code: str = Field("en", min_length=2, max_length=10)
    blog_url: str | None = Field(
        None, description="Canonical URL the blog post will live at."
    )
    pinterest_url: str | None = Field(
        None, description="Public URL of the matching Pinterest pin."
    )
    image_credits: list[str] | None = Field(
        None,
        description="Per-image credits (e.g. ['Utagawa 1868, Wikimedia Commons', ...]).",
    )


class PostKitFileInfo(BaseModel):
    filename: str
    size_bytes: int
    purpose: str


class PostKitResponse(BaseModel):
    project_id: int
    language_code: str
    slug: str
    kit_root: str
    files: list[PostKitFileInfo]
    youtube_title: str
    youtube_hashtags: list[str]
    chapter_count: int


@app.post(
    "/projects/{project_id}/post-kit",
    response_model=PostKitResponse,
)
def generate_project_post_kit(
    project_id: int,
    payload: PostKitRequest,
    db: Session = Depends(get_db),
) -> PostKitResponse:
    """Generate the complete post-publishing kit for a project.

    Operator uses these files when publishing manually (until Phase 7 wires
    up the YouTube Data API). Requires:
      - Project exists
      - Variant has script + blog + audio + video
      - output_video_path is set (run /output-video first)
    """
    from post_kit_generator import generate_post_kit

    project = db.query(VideoProject).filter(VideoProject.id == project_id).first()
    if not project:
        raise HTTPException(
            status_code=404, detail=f"Project {project_id} not found"
        )

    variant = (
        db.query(TranslationVariant)
        .filter(
            TranslationVariant.project_id == project_id,
            TranslationVariant.language_code == payload.language_code,
        )
        .first()
    )
    if variant is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No variant for project {project_id} / {payload.language_code}"
            ),
        )

    if not variant.script:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Project {project_id} has no script. Run /script first."
            ),
        )
    if not variant.blog_article:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Project {project_id} has no blog_article. Run /blog first."
            ),
        )
    if not variant.audio_path or not os.path.exists(variant.audio_path):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Project {project_id} has no audio on disk. Run /audio first."
            ),
        )
    if not variant.output_video_path or not os.path.exists(variant.output_video_path):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Project {project_id} has no rendered video. Run /output-video first."
            ),
        )

    # Pull BGM info from manifest if available
    bgm_attribution: str | None = None
    manifest_path = (
        Path(os.environ.get(
            "REDSUN_STORAGE_DIR",
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "storage"),
        ))
        / str(project_id)
        / "manifest.json"
    )
    if manifest_path.exists():
        try:
            with open(manifest_path) as f:
                manifest = json.load(f)
            bgm_filename = manifest.get("bgm_filename")
            bgm_mood = manifest.get("bgm_mood")
            if bgm_filename and bgm_mood:
                bgm_attribution = (
                    f"Classical {bgm_mood} piece ({bgm_filename}), "
                    "Public Domain, Wikimedia Commons"
                )
        except Exception as exc:
            logger.warning("Could not read manifest for project %s: %s",
                           project_id, exc)

    try:
        paths = generate_post_kit(
            project_id=project_id,
            project_title=project.title,
            project_slug=project.slug or f"project-{project_id}",
            lang=payload.language_code,
            script=variant.script or "",
            blog_article=variant.blog_article or "",
            audio_path=variant.audio_path,
            video_path=variant.output_video_path,
            bgm_attribution=bgm_attribution,
            blog_url=payload.blog_url,
            pinterest_url=payload.pinterest_url,
            image_credits=payload.image_credits or [],
            output_video_duration_sec=None,  # let generator estimate from script
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Read back the post_metadata.json to surface the title + hashtags
    with open(paths.post_metadata) as f:
        meta = json.load(f)

    file_infos = [
        PostKitFileInfo(
            filename=paths.youtube_description.name,
            size_bytes=paths.youtube_description.stat().st_size,
            purpose="YouTube video description (paste into YouTube Studio)",
        ),
        PostKitFileInfo(
            filename=paths.youtube_pinned_comment.name,
            size_bytes=paths.youtube_pinned_comment.stat().st_size,
            purpose="YouTube pinned comment (engagement hook)",
        ),
        PostKitFileInfo(
            filename=paths.social_captions.name,
            size_bytes=paths.social_captions.stat().st_size,
            purpose="Social media captions (Twitter/X, Instagram, TikTok)",
        ),
        PostKitFileInfo(
            filename=paths.thumbnail_brief.name,
            size_bytes=paths.thumbnail_brief.stat().st_size,
            purpose="Thumbnail design brief for Affinity designer",
        ),
        PostKitFileInfo(
            filename=paths.post_metadata.name,
            size_bytes=paths.post_metadata.stat().st_size,
            purpose="Consolidated post metadata (titles, tags, chapters, links)",
        ),
    ]

    logger.info(
        "Project %s post kit generated: %d files at %s",
        project_id, len(file_infos), paths.root,
    )

    return PostKitResponse(
        project_id=project_id,
        language_code=payload.language_code,
        slug=project.slug or f"project-{project_id}",
        kit_root=str(paths.root),
        files=file_infos,
        youtube_title=meta.get("title", project.title),
        youtube_hashtags=meta.get("hashtags", []),
        chapter_count=len(meta.get("chapters", [])),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Editorial & Book Branch (Amazon KDP + Audiobook Master)
# ─────────────────────────────────────────────────────────────────────────────
class BookChapterResponse(BaseModel):
    status: str
    slug: str
    chapter_path: str
    word_count: int
    char_count: int
    message: str


class BookAudiobookRequest(BaseModel):
    provider: Optional[str] = "stepfun"
    voice: Optional[str] = "magnetic-voiced-male"
    speed: Optional[float] = 0.95


class BookAudiobookResponse(BaseModel):
    status: str
    slug: str
    title: str
    engine_used: Optional[str] = None
    fallback_reason: Optional[str] = None
    primary_engine: Optional[str] = "stepfun"
    provider: Optional[str] = "stepfun"
    voice: Optional[str] = "magnetic-voiced-male"
    fallbacks_triggered: Optional[Dict[str, int]] = None
    fallback_chunks_count: Optional[int] = 0
    engine_breakdown: Optional[Dict[str, int]] = None
    wav_path: str
    mp3_path: str
    wav_size_bytes: int
    mp3_size_bytes: int
    wav_size_mb: float
    mp3_size_mb: float
    duration_seconds: float
    duration_minutes: float
    audiobook_duration_seconds: Optional[float] = None
    audiobook_duration_minutes: Optional[float] = None
    chunk_count: int
    elapsed_seconds: float
    qa_report: Optional[Dict[str, Any]] = None


class BookStatusResponse(BaseModel):
    slug: str
    has_chapter: bool
    chapter_path: Optional[str] = None
    chapter_size_bytes: int = 0
    chapter_word_count: int = 0
    has_audiobook: bool
    wav_path: Optional[str] = None
    wav_size_bytes: int = 0
    mp3_path: Optional[str] = None
    mp3_size_bytes: int = 0
    audiobook_duration_seconds: float = 0.0
    audiobook_duration_minutes: float = 0.0


class AudioQaReportResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    has_qa: bool = True
    slug: str
    audit_timestamp: Optional[str] = None
    verdict: Optional[str] = "PASSED"  # "PASSED" | "WARNING" | "FAILED"
    duration_seconds: float = 0.0
    duration_formatted: str = ""
    total_words_expected: int = 0
    total_words_transcribed: int = 0
    wpm: float = 0.0
    wpm_status: str = "NORMAL"
    text_match_percentage: float = 100.0
    long_silences_detected: int = 0
    long_silence_details: List[Dict[str, Any]] = []
    clipping_detected: bool = False
    max_volume_db: float = 0.0
    mean_volume_db: float = 0.0
    anomalies: List[Dict[str, Any]] = []
    primary_anomaly_timestamp: Optional[str] = None
    primary_anomaly_sec: Optional[float] = None
    primary_anomaly_seconds: Optional[float] = None
    primary_anomaly_formatted: Optional[str] = None
    summary: str = ""
    elapsed_seconds: Optional[float] = None


class OpenFolderRequest(BaseModel):
    subfolder: Optional[str] = None  # e.g.: None (project root), "distribution", "book", "video"


class OpenFolderResponse(BaseModel):
    success: bool
    opened_path: str


class YouTubeStatusResponse(BaseModel):
    authenticated: bool
    channel_title: Optional[str] = None
    channel_id: Optional[str] = None
    custom_url: Optional[str] = None
    has_client_secret: bool = False
    has_token: bool = False
    error: Optional[str] = None


class YouTubePublishRequest(BaseModel):
    title_index: Optional[int] = 0
    privacy_status: Optional[str] = "private"
    category_id: Optional[str] = "27"


class YouTubePublishResponse(BaseModel):
    success: bool
    video_id: str
    url: str
    studio_url: str
    status: str
    title: str
    thumbnail_uploaded: bool
    slug: str


class YouTubeShortPublishResponse(BaseModel):
    success: bool
    video_id: str
    url: str
    studio_url: str
    status: str
    title: str
    clip_index: int
    slug: str


class PinterestBoardSummary(BaseModel):
    id: str
    name: str
    pin_count: Optional[int] = 0


class PinterestStatusResponse(BaseModel):
    connected: bool
    username: Optional[str] = None
    business_name: Optional[str] = None
    boards_count: int = 0
    boards: List[PinterestBoardSummary] = []
    error: Optional[str] = None


class PublishedPinItem(BaseModel):
    pin_index: int
    id: str
    url: str
    board_id: str
    title: str
    destination_link: str
    image_file: str


class PinterestPublishResponse(BaseModel):
    success: bool
    slug: str
    published_pins: List[PublishedPinItem] = []
    total_published: int = 0



@app.post("/projects/{slug}/book/chapter", response_model=BookChapterResponse)
def create_book_chapter(slug: str):
    """Generate an unabridged, annotated book chapter for Amazon KDP via Claude Sonnet 5."""
    from redsun.services.book_generator import generate_book_chapter
    try:
        chapter_path_str = generate_book_chapter(slug)
        chapter_path = Path(chapter_path_str)
        content = chapter_path.read_text(encoding="utf-8")
        return BookChapterResponse(
            status="success",
            slug=slug,
            chapter_path=chapter_path_str,
            word_count=len(content.split()),
            char_count=len(content),
            message="Capítulo do livro gerado com sucesso via Claude Sonnet 5.",
        )
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as exc:
        logger.exception("Failed to generate book chapter for '%s': %s", slug, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/projects/{slug}/book/audiobook", response_model=BookAudiobookResponse)
def create_book_audiobook(slug: str, payload: Optional[BookAudiobookRequest] = None):
    """Synthesize complete chapter narration via StepFun StepAudio (24kHz WAV + 192k MP3) with Edge-TTS fallback."""
    from redsun.services.book_audio_generator import generate_book_audiobook
    provider = payload.provider if payload and payload.provider else "stepfun"
    voice = payload.voice if payload and payload.voice else "magnetic-voiced-male"
    speed = payload.speed if payload and payload.speed is not None else 0.95
    try:
        res = generate_book_audiobook(slug, provider=provider, voice=voice, speed=speed)
        return BookAudiobookResponse(**res)
    except FileNotFoundError as fnf:
        raise HTTPException(status_code=404, detail=str(fnf))
    except Exception as exc:
        logger.exception("Failed to generate book audiobook for '%s': %s", slug, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/projects/{slug}/book/status", response_model=BookStatusResponse)
def get_book_status(slug: str, db: Session = Depends(get_db)):
    """Return status, file existence and size metrics for chapter and audiobook."""
    from redsun.services.book_generator import _resolve_project, get_book_dir
    project, canonical_slug = _resolve_project(slug, db)
    book_dir = get_book_dir(canonical_slug)
    
    chapter_file = book_dir / "chapter.md"
    wav_file = book_dir / "chapter_audiobook.wav"
    mp3_file = book_dir / "chapter_audiobook.mp3"
    
    has_chap = chapter_file.exists() and chapter_file.stat().st_size > 100
    chap_words = len(chapter_file.read_text(encoding="utf-8").split()) if has_chap else 0
    chap_bytes = chapter_file.stat().st_size if has_chap else 0
    
    has_wav = wav_file.exists() and wav_file.stat().st_size > 1000
    has_mp3 = mp3_file.exists() and mp3_file.stat().st_size > 1000
    
    dur_sec = 0.0
    if has_wav:
        try:
            import wave
            with wave.open(str(wav_file), "rb") as w:
                dur_sec = w.getnframes() / float(w.getframerate())
        except Exception:
            pass

    return BookStatusResponse(
        slug=canonical_slug,
        has_chapter=has_chap,
        chapter_path=str(chapter_file.resolve()) if has_chap else None,
        chapter_size_bytes=chap_bytes,
        chapter_word_count=chap_words,
        has_audiobook=has_wav and has_mp3,
        wav_path=str(wav_file.resolve()) if has_wav else None,
        wav_size_bytes=wav_file.stat().st_size if has_wav else 0,
        mp3_path=str(mp3_file.resolve()) if has_mp3 else None,
        mp3_size_bytes=mp3_file.stat().st_size if has_mp3 else 0,
        audiobook_duration_seconds=round(dur_sec, 2),
        audiobook_duration_minutes=round(dur_sec / 60.0, 2),
    )


@app.get("/projects/{slug}/book/chapter/content")
def get_book_chapter_content(slug: str, db: Session = Depends(get_db)):
    """Return raw markdown content of the chapter."""
    from redsun.services.book_generator import _resolve_project, get_book_dir
    project, canonical_slug = _resolve_project(slug, db)
    book_dir = get_book_dir(canonical_slug)
    chapter_file = book_dir / "chapter.md"
    if not chapter_file.exists():
        raise HTTPException(status_code=404, detail="Chapter not found")
    return {"slug": canonical_slug, "content": chapter_file.read_text(encoding="utf-8")}


@app.api_route("/projects/{slug}/book/audio", methods=["GET", "HEAD"])
def get_book_audio(slug: str, db: Session = Depends(get_db)):
    """Stream or return the chapter_audiobook.mp3 file."""
    from fastapi.responses import FileResponse
    from redsun.services.book_generator import _resolve_project, get_book_dir
    project, canonical_slug = _resolve_project(slug, db)
    book_dir = get_book_dir(canonical_slug)
    mp3_file = book_dir / "chapter_audiobook.mp3"
    if not mp3_file.exists():
        raise HTTPException(status_code=404, detail="Audiobook MP3 not found")
    return FileResponse(
        path=str(mp3_file),
        media_type="audio/mpeg",
        filename=f"{canonical_slug}_chapter_audiobook.mp3",
    )


@app.post("/projects/{slug}/book/audio/audit", response_model=AudioQaReportResponse)
def audit_project_book_audio(slug: str):
    """Run automated local reverse audio QA audit (Text fidelity, DSP clipping/silence, WPM pacing)."""
    from redsun.services.audio_qa_service import audit_audiobook
    try:
        res = audit_audiobook(slug)
        res["has_qa"] = True
        return AudioQaReportResponse(**res)
    except FileNotFoundError as fnf:
        raise HTTPException(status_code=404, detail=str(fnf))
    except Exception as exc:
        logger.exception("Failed to run audio QA audit for '%s': %s", slug, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/projects/{slug}/book/audio/audit", response_model=AudioQaReportResponse)
def get_project_book_audio_audit(slug: str):
    """Return existing audio QA audit report if present, or has_qa: false."""
    from redsun.services.audio_qa_service import get_existing_audio_qa_report
    report = get_existing_audio_qa_report(slug)
    if not report or not report.get("has_qa"):
        return AudioQaReportResponse(
            has_qa=False,
            slug=slug,
            verdict="NOT_AUDITED",
            summary="Audiobook has not been audited yet."
        )
    return AudioQaReportResponse(**report)


# ─────────────────────────────────────────────────────────────────────────────
# YouTube Data API v3 Endpoints
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/youtube/status", response_model=YouTubeStatusResponse)
def get_youtube_auth_status():
    """Return status of YouTube Data API v3 OAuth2 credentials and channel connectivity."""
    from redsun.services.youtube_auth import check_auth_status
    return check_auth_status()


@app.post("/projects/{slug}/publish/youtube", response_model=YouTubePublishResponse)
def publish_project_to_youtube(slug: str, payload: Optional[YouTubePublishRequest] = None):
    """Publish master video and custom thumbnail to YouTube as a private or unlisted draft."""
    from redsun.services.youtube_uploader import upload_project_master_video
    title_idx = payload.title_index if payload and payload.title_index is not None else 0
    privacy = payload.privacy_status if payload and payload.privacy_status else "private"
    category = payload.category_id if payload and payload.category_id else "27"
    try:
        res = upload_project_master_video(
            slug=slug,
            title_index=title_idx,
            privacy_status=privacy,
            category_id=category,
        )
        return YouTubePublishResponse(**res)
    except FileNotFoundError as fnf:
        raise HTTPException(status_code=404, detail=str(fnf))
    except RuntimeError as re:
        raise HTTPException(status_code=400, detail=str(re))
    except Exception as exc:
        logger.exception("Failed to publish video for '%s' to YouTube: %s", slug, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/projects/{slug}/publish/youtube/shorts/{clip_index}", response_model=YouTubeShortPublishResponse)
def publish_short_to_youtube(slug: str, clip_index: int, payload: Optional[YouTubePublishRequest] = None):
    """Publish an individual vertical short clip to YouTube as a private or unlisted draft."""
    from redsun.services.youtube_uploader import upload_project_short
    privacy = payload.privacy_status if payload and payload.privacy_status else "private"
    category = payload.category_id if payload and payload.category_id else "27"
    try:
        res = upload_project_short(
            slug=slug,
            short_index=clip_index,
            privacy_status=privacy,
            category_id=category,
        )
        return YouTubeShortPublishResponse(**res)
    except FileNotFoundError as fnf:
        raise HTTPException(status_code=404, detail=str(fnf))
    except RuntimeError as re:
        raise HTTPException(status_code=400, detail=str(re))
    except Exception as exc:
        logger.exception("Failed to publish short #%d for '%s' to YouTube: %s", clip_index, slug, exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# Pinterest API v5 Endpoints
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/pinterest/status", response_model=PinterestStatusResponse)
def get_pinterest_api_status():
    """Return status of Pinterest API v5 token and brand account connectivity."""
    from redsun.services.pinterest_uploader import check_pinterest_status
    return check_pinterest_status()


@app.post("/projects/{slug}/publish/pinterest", response_model=PinterestPublishResponse)
def publish_project_to_pinterest(slug: str):
    """Publish the 4 pins of the project distribution kit to Pinterest via API v5."""
    from redsun.services.pinterest_uploader import publish_project_pins
    try:
        res = publish_project_pins(slug=slug)
        return PinterestPublishResponse(**res)
    except HTTPException:
        raise
    except FileNotFoundError as fnf:
        raise HTTPException(status_code=404, detail=str(fnf))
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except RuntimeError as re:
        raise HTTPException(status_code=502, detail=str(re))
    except Exception as exc:
        logger.exception("Failed to publish pins for '%s' to Pinterest: %s", slug, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/projects/{slug}/open-folder", response_model=OpenFolderResponse)
async def open_project_folder_endpoint(slug: str, req: Optional[OpenFolderRequest] = None):
    """Abre o diretório do projeto ativo diretamente no Finder do macOS."""
    import platform
    import subprocess
    from redsun.workspace import resolve_project_dir

    if platform.system() != "Darwin":
        raise HTTPException(status_code=400, detail="Command supported only on macOS")

    # Resolve o caminho físico canônico do projeto (respeitando pastas numeradas 001_slug)
    project_dir = resolve_project_dir(slug)
    if not project_dir or not project_dir.exists():
        raise HTTPException(status_code=404, detail=f"Project directory not found on disk for '{slug}'")

    target_path = project_dir
    if req and req.subfolder:
        sub_dir = project_dir / req.subfolder
        if sub_dir.exists():
            target_path = sub_dir

    try:
        subprocess.run(["open", str(target_path)], check=True)
        return OpenFolderResponse(success=True, opened_path=str(target_path))
    except Exception as e:
        logger.exception("Failed to open folder in Finder for '%s': %s", slug, e)
        raise HTTPException(status_code=500, detail=f"Error opening folder in Finder: {str(e)}")


# ─────────────────────────────────────────────────────────────────────────────
# Multi-Story Compilations Endpoints
# ─────────────────────────────────────────────────────────────────────────────

class BuildCompilationRequest(BaseModel):
    compilation_title: str
    selected_slugs: List[str]
    trim_intermediate_outros_seconds: float = 12.0
    normalize_audio: bool = True
    playlist_name: Optional[str] = "Edo Japan Folklore for Deep Sleep & Rainy Nights (Full Compilations)"


class PreviewTimelineRequest(BaseModel):
    selected_slugs: List[str]
    trim_intermediate_outros_seconds: float = 12.0


class PublishCompilationYouTubeRequest(BaseModel):
    title_index: int = 0
    playlist_name: Optional[str] = None


@app.get("/compilations/eligible-projects")
def get_eligible_compilation_projects_endpoint():
    """Scan all projects with completed master videos eligible for compilations."""
    from redsun.services.compilation_builder import get_eligible_compilation_projects
    return get_eligible_compilation_projects()


@app.post("/compilations/preview-timeline")
def preview_compilation_timeline_endpoint(req: PreviewTimelineRequest):
    """Calculate unified timeline, duration, and chapters without triggering rendering."""
    from redsun.services.compilation_builder import calculate_compilation_timeline
    try:
        return calculate_compilation_timeline(
            selected_slugs=req.selected_slugs,
            trim_intermediate_outros_seconds=req.trim_intermediate_outros_seconds,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/compilations")
def list_compilations_endpoint():
    """List all compilations built on disk."""
    from redsun.services.compilation_builder import list_compilations
    return list_compilations()


@app.post("/compilations/build")
def build_compilation_endpoint(req: BuildCompilationRequest, background_tasks: BackgroundTasks):
    """Trigger asynchronous multi-story compilation video and metadata build."""
    import time
    from redsun.services.compilation_builder import build_compilation_async

    if not req.selected_slugs or len(req.selected_slugs) < 1:
        raise HTTPException(status_code=400, detail="At least 1 project slug must be selected for compilation.")

    # Generate canonical compilation ID
    clean_slug = re.sub(r"[^a-z0-9]+", "-", req.compilation_title.lower()).strip("-")
    if not clean_slug:
        clean_slug = f"compilation_{int(time.time())}"
    compilation_id = clean_slug

    background_tasks.add_task(
        build_compilation_async,
        compilation_id=compilation_id,
        title=req.compilation_title,
        selected_slugs=req.selected_slugs,
        trim_intermediate_outros_seconds=req.trim_intermediate_outros_seconds,
        normalize_audio=req.normalize_audio,
        playlist_name=req.playlist_name,
    )

    return {
        "success": True,
        "compilation_id": compilation_id,
        "message": f"Compilation '{req.compilation_title}' build started in background.",
    }


@app.get("/compilations/{compilation_id}/status")
def get_compilation_status_endpoint(compilation_id: str):
    """Return real-time build status or completed manifest for a compilation."""
    from redsun.services.compilation_builder import get_compilation_status
    status = get_compilation_status(compilation_id)
    if status.get("status") == "not_found":
        raise HTTPException(status_code=404, detail=f"Compilation '{compilation_id}' not found")
    return status


@app.post("/compilations/{compilation_id}/publish/youtube")
def publish_compilation_youtube_endpoint(compilation_id: str, req: Optional[PublishCompilationYouTubeRequest] = None):
    """Upload compilation master to YouTube as private draft and assign to playlist."""
    from redsun.services.compilation_builder import upload_compilation_to_youtube
    try:
        title_idx = req.title_index if req else 0
        pl_name = req.playlist_name if req else None
        res = upload_compilation_to_youtube(
            compilation_id=compilation_id,
            title_index=title_idx,
            playlist_name=pl_name,
        )
        return res
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to publish compilation '%s' to YouTube: %s", compilation_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# Book Publishing & Anthology Compilation (KDP & Ko-fi)
# ─────────────────────────────────────────────────────────────────────────────

class CompileBookRequest(BaseModel):
    slugs: list[str]
    volume: int = 1
    title: Optional[str] = None
    foreword: Optional[str] = None
    output_dir: Optional[str] = None

class CompileBookResponse(BaseModel):
    status: str
    volume: int
    book_title: str
    chapters_count: int
    output_dir: str
    manuscript_html: str
    manuscript_html_size_bytes: int
    book_pdf: str
    book_pdf_size_bytes: int


@app.post("/books/compile", response_model=CompileBookResponse)
async def compile_book_endpoint(req: CompileBookRequest):
    """
    Compiles selected project chapters into a 6x9" KDP & Ko-fi collector's edition anthology.
    """
    from scripts.compile_kdp_anthology import compile_anthology
    try:
        if not req.slugs:
            raise HTTPException(status_code=400, detail="A lista de slugs não pode estar vazia.")
        res = await compile_anthology(
            slugs=req.slugs,
            volume=req.volume,
            title=req.title,
            foreword_path=req.foreword,
            output_dir=req.output_dir,
        )
        return res
    except ValueError as val_err:
        raise HTTPException(status_code=400, detail=str(val_err))
    except Exception as exc:
        logger.exception("Failed to compile book anthology: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))



# ─────────────────────────────────────────────────────────────────────────────
# Newsletter ("The Midnight Archive") & Listmonk Orchestration
# ─────────────────────────────────────────────────────────────────────────────
class NewsletterDraftRequest(BaseModel):
    project_slugs: List[str]
    issue_number: Optional[int] = None
    custom_notes: Optional[str] = None


@app.get("/api/newsletter/info")
def get_newsletter_info_endpoint():
    """Return Listmonk integration status and configuration info."""
    from services.newsletter_service import NewsletterService
    svc = NewsletterService()
    return {
        "api_url": svc.api_url,
        "is_configured": svc.is_listmonk_configured(),
        "list_uuid": svc.list_uuid,
        "template_id": svc.template_id,
        "dashboard_url": f"{svc.api_url}/admin/campaigns",
    }


@app.post("/api/newsletter/generate-draft")
async def generate_newsletter_draft_endpoint(req: NewsletterDraftRequest):
    """
    Synthesize weekly newsletter dossier via LLM and create draft campaign in Listmonk.
    """
    from services.newsletter_service import compose_weekly_dispatch
    if not req.project_slugs:
        raise HTTPException(status_code=400, detail="Pelo menos 1 slug de projeto deve ser selecionado.")
    try:
        result = await compose_weekly_dispatch(
            project_slugs=req.project_slugs,
            issue_number=req.issue_number,
            custom_notes=req.custom_notes,
        )
        return result
    except ValueError as val_err:
        raise HTTPException(status_code=400, detail=str(val_err))
    except Exception as exc:
        logger.exception("Failed to generate newsletter draft: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# Entrypoint
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("main:app", host=host, port=port, reload=True)
