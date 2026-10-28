"""ProjectContext — single source of truth for pipeline execution.

Every phase reads configuration from ProjectContext. Environment
variables are read at the boundary (`from_env`) and never inside
phase bodies.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from typing import Optional

from . import defaults
from .workspace import ProjectWorkspace


# ── Episode-level metadata ─────────────────────────────────────────────


@dataclass
class EpisodeMetadata:
    """Specific to one project. Always populated."""

    slug: str
    title: str
    title_ja: Optional[str] = None
    source_text: str = ""
    source_url: Optional[str] = None
    source_language: str = "ja"
    output_language: str = "en"
    target_keywords: list = field(default_factory=list)
    source_type: Optional[str] = None
    atmosphere: Optional[str] = None
    max_clips: int = 0  # 0 = disabled (default); >0 enables generate_shorts


# ── Theme-level metadata (generic, reusable across episodes) ──────────


@dataclass
class ThemeMetadata:
    """Generic theme hints. Reused across episodes of same theme.

    MAY be empty when the project is uncatalogued. Pipelines
    MUST NOT assume any field is populated.
    """

    name: str = ""
    description: str = ""
    atmosphere: str = ""
    visual_keywords: list = field(default_factory=list)
    category: str = ""
    source_type: str = ""
    bgm_mood_hints: list = field(default_factory=list)

    def is_empty(self) -> bool:
        return not any(
            [
                self.name,
                self.description,
                self.atmosphere,
                self.visual_keywords,
                self.category,
                self.source_type,
                self.bgm_mood_hints,
            ]
        )


# ── Configuration slices ─────────────────────────────────────────────


@dataclass
class LLMConfig:
    model: str = defaults.LLM_DEFAULT_MODEL
    temperature: float = defaults.LLM_DEFAULT_TEMPERATURE
    max_tokens: int = defaults.LLM_DEFAULT_MAX_TOKENS
    base_url: str = defaults.LLM_DEFAULT_BASE_URL
    api_key_ref: Optional[str] = None


@dataclass
class TTSConfig:
    engine: str = defaults.TTS_DEFAULT_ENGINE
    voice: str = defaults.TTS_DEFAULT_KOKORO_VOICE
    instruct: str = ""
    language: str = defaults.TTS_DEFAULT_KOKORO_LANG
    repo_id: str = defaults.TTS_DEFAULT_KOKORO_REPO
    qwen_speaker: str = defaults.TTS_DEFAULT_QWEN_SPEAKER
    qwen_instruct: str = defaults.TTS_DEFAULT_QWEN_INSTRUCT
    qwen_language: str = defaults.TTS_DEFAULT_QWEN_LANGUAGE
    qwen_model_id: str = defaults.TTS_DEFAULT_QWEN_MODEL
    qwen_device: str = defaults.TTS_DEFAULT_QWEN_DEVICE
    chunk_max_chars: int = defaults.TTS_CHUNK_MAX_CHARS
    chunk_target_chars: int = defaults.TTS_CHUNK_TARGET_CHARS


@dataclass
class ImageConfig:
    model: str = defaults.COMFY_DEFAULT_MODEL
    base_seed: int = defaults.COMFY_DEFAULT_BASE_SEED
    host: str = defaults.COMFY_DEFAULT_HOST
    port: int = defaults.COMFY_DEFAULT_PORT
    width: int = defaults.RENDER_WIDTH
    height: int = defaults.RENDER_HEIGHT


@dataclass
class RenderConfig:
    fps: int = defaults.RENDER_FPS
    width: int = defaults.RENDER_WIDTH
    height: int = defaults.RENDER_HEIGHT
    chunk_seconds: int = defaults.RENDER_CHUNK_SECONDS
    chunked: bool = defaults.RENDER_CHUNKED


@dataclass
class VisualDensityConfig:
    target_image_seconds: float = defaults.TARGET_IMAGE_SECONDS
    max_image_seconds: float = defaults.MAX_IMAGE_SECONDS
    min_visual_beats: int = defaults.MIN_VISUAL_BEATS


@dataclass
class QualityConfig:
    min_audio_duration_sec: int = defaults.MIN_AUDIO_DURATION_SEC
    chars_per_second: int = defaults.CHARS_PER_SECOND
    silent_tail_threshold_sec: float = defaults.SILENT_TAIL_THRESHOLD_SEC
    min_coverage_ratio: float = defaults.MIN_COVERAGE_RATIO
    min_image_bytes: int = defaults.MIN_IMAGE_BYTES


# ── Top-level context ──────────────────────────────────────────────────


@dataclass
class ProjectContext:
    project_id: Optional[int]
    workspace: ProjectWorkspace
    episode: EpisodeMetadata
    theme_metadata: ThemeMetadata
    llm: LLMConfig = field(default_factory=LLMConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)
    image: ImageConfig = field(default_factory=ImageConfig)
    render: RenderConfig = field(default_factory=RenderConfig)
    visual_density: VisualDensityConfig = field(
        default_factory=VisualDensityConfig
    )
    quality: QualityConfig = field(default_factory=QualityConfig)

    @classmethod
    def defaults(cls) -> "ProjectContext":
        ws = ProjectWorkspace.from_slug("untitled")
        episode = EpisodeMetadata(slug="untitled", title="Untitled")
        return cls(
            project_id=None,
            workspace=ws,
            episode=episode,
            theme_metadata=ThemeMetadata(),
        )

    @classmethod
    def from_cli_args(cls, args) -> "ProjectContext":
        """Build a ProjectContext from argparse namespace."""
        from .workspace import slugify_title

        slug = args.slug
        title = args.title
        if not slug or not title:
            raise ValueError("--slug and --title are required")

        ws = ProjectWorkspace.from_slug(slug)
        episode = EpisodeMetadata(
            slug=slug,
            title=title,
            title_ja=getattr(args, "title_ja", "") or None,
            source_text=getattr(args, "source_text", "") or "",
            source_url=getattr(args, "source_url", None),
            output_language=getattr(args, "language", "en"),
            target_keywords=(
                [s.strip() for s in (getattr(args, "keywords", "") or "").split(",")]
                if getattr(args, "keywords", "")
                else []
            ),
            source_type=getattr(args, "source_type", None),
            atmosphere=getattr(args, "atmosphere", None),
            max_clips=int(getattr(args, "max_clips", 0) or 0),
        )

        theme_metadata = cls._load_theme_metadata(slug)

        image = ImageConfig(
            model=getattr(args, "model", defaults.COMFY_DEFAULT_MODEL),
            base_seed=getattr(args, "base_seed", defaults.COMFY_DEFAULT_BASE_SEED),
        )

        render = RenderConfig(
            chunk_seconds=getattr(
                args, "chunk_seconds", defaults.RENDER_CHUNK_SECONDS
            ),
            chunked=getattr(args, "chunked", defaults.RENDER_CHUNKED),
        )

        return cls(
            project_id=None,
            workspace=ws,
            episode=episode,
            theme_metadata=theme_metadata,
            image=image,
            render=render,
        )

    @staticmethod
    def _load_theme_metadata(slug: str) -> ThemeMetadata:
        try:
            from .themes import catalog

            entry = catalog.find(slug)
            if entry is None:
                return ThemeMetadata()
            return ThemeMetadata(
                name=entry.get("name", ""),
                description=entry.get("description", ""),
                atmosphere=entry.get("atmosphere", ""),
                visual_keywords=list(entry.get("visual_keywords", [])),
                category=entry.get("category", ""),
                source_type=entry.get("source_type", ""),
                bgm_mood_hints=list(entry.get("bgm_mood_hints", [])),
            )
        except Exception:
            return ThemeMetadata()

    # ── hashing helpers ─────────────────────────────────────────────

    def config_hash(self, slice_name: str) -> str:
        if slice_name == "tts":
            payload = asdict(self.tts)
        elif slice_name == "image":
            payload = asdict(self.image)
        elif slice_name == "render":
            payload = asdict(self.render)
        elif slice_name == "visual_density":
            payload = asdict(self.visual_density)
        elif slice_name == "llm":
            payload = asdict(self.llm)
        elif slice_name == "quality":
            payload = asdict(self.quality)
        elif slice_name == "episode":
            payload = asdict(self.episode)
        else:
            raise ValueError(f"unknown config slice: {slice_name!r}")
        canonical = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def hash_text(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def hash_file(path) -> str:
        from pathlib import Path as _P

        p = _P(path)
        if not p.exists() or p.is_dir():
            return ""
        h = hashlib.sha256()
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()