"""SQLAlchemy ORM models for the Red Sun Japan pipeline.

Schema design (Phase 1):

- ``VideoProject``: one row per "video idea", the durable artifact across the
  whole pipeline. Tracks the source-language (always ``ja`` for now) and the
  comma-separated list of output languages (``"en"`` by default; future i18n
  targets go here without code changes).
- ``TranslationVariant``: one row per (project, language) translation. Holds
  the script, blog article, generated audio + Whisper JSON. Phase 1 only
  touches ``en`` rows; the table exists now so Phase 6 (multi-language blog)
  does not require a migration.
- ``AssetImage``: registry of downloaded images. Lives in ``assets_library/``
  on disk; this table is the index that lets the scraper avoid duplicates, the
  print-on-demand operator find good merch candidates, and Remotion look up
  which images belong to a project theme.

Note: the SQLite file (``redsunjapan.db``) is gitignored — only the schema
lives in the repo. A Alembic setup will land in a later phase.
"""
from __future__ import annotations

import datetime
import enum
import os

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

from pathlib import Path

# SQLite local — perfect for development; switch DATABASE_URL to Postgres later
# for multi-worker / VPS deployment without code changes.
_DEFAULT_DB_PATH = Path(__file__).resolve().parent / "redsunjapan.db"
DATABASE_URL = os.environ.get(
    "REDSUN_DATABASE_URL", f"sqlite:///{_DEFAULT_DB_PATH}"
)

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()



def get_project_folder_name(project_id: int, slug: str) -> str:
    """Canonical folder name format: {id:03d}_{slug}."""
    return f"{project_id:03d}_{slug}"


# ─────────────────────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────────────────────
class VideoStatus(enum.Enum):
    """Strict state machine for a video project.

    Pipeline: PENDING → SCRIPTED → READY_FOR_AUDIO → ASSETS_READY →
              RENDERED → PUBLISHED (plus FAILED as a terminal sink).
    """

    PENDING = "pending"                # idea only, no script yet
    SCRIPTED = "scripted"              # English script + blog draft ready
    READY_FOR_AUDIO = "ready_for_audio"  # locked, awaiting TTS run
    ASSETS_PENDING = "assets_pending"  # awaiting image generation
    HANDOFF_GATE = "handoff_gate"      # paused at director handoff gate
    ASSETS_READY = "assets_ready"      # audio + images generated
    RENDERED = "rendered"              # MP4 produced by Remotion
    PUBLISHED = "published"            # live on YouTube
    FAILED = "failed"                  # terminal sink; see error_message
    COMPLETE = "complete"              # completed pipeline


class AssetLicense(enum.Enum):
    """Compatibility buckets for third-party images.

    Anything outside ``PD`` / ``CC0`` requires manual review before merch
    (print-on-demand) — that's the whole point of recording the license here.
    """

    PD = "pd"                        # public domain
    CC0 = "cc0"                      # CC0 / public-domain dedication
    CC_BY = "cc_by"                  # requires attribution
    CC_BY_SA = "cc_by_sa"            # requires attribution + share-alike
    OTHER = "other"                  # unknown, requires manual review
    UNVERIFIED = "unverified"        # fetched but not yet verified
    GENERATED = "generated"          # AI-generated asset


# ─────────────────────────────────────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────────────────────────────────────
class VideoProject(Base):
    __tablename__ = "video_projects"

    id = Column(Integer, primary_key=True, index=True)

    # Meta
    channel_name = Column(String, default="Red Sun Japan")
    title = Column(String, nullable=False)
    title_ja = Column(String, nullable=True)   # Japanese title (e.g. 雪女)
    theme_source_url = Column(String)         # Nichibanken/NDL link the agent fetched

    # Source content (Japanese text the pipeline starts from).
    source_language = Column(String, default="ja")
    source_text = Column(Text)                # raw Japanese fetched from sources

    # What we intend to produce. CSV for forward compatibility
    # (e.g. "en,pt-BR,es"). Stored as parsed list at the schema boundary.
    output_languages = Column(String, default="en")
    target_keywords = Column(Text)            # CSV; recommended SEO keywords

    # Pipeline state + history.
    status = Column(Enum(VideoStatus), default=VideoStatus.PENDING, nullable=False, index=True)
    error_message = Column(Text)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.datetime.utcnow,
        onupdate=datetime.datetime.utcnow,
        nullable=False,
    )

    # Source classification (for review agent).
    # Defaults to None → endpoint falls back to heuristics or request payload.
    source_type = Column(String, nullable=True)   # e.g. "classical_kaidan", "regional_yokai"
    atmosphere = Column(String, nullable=True)     # e.g. "lantern-light-and-fog", "urban-neon-rain"

    # Convenience: human-readable slug for blog/Remotion asset paths.
    slug = Column(String, unique=True, index=True)

    # Visual entities curation payload (JingJing Phase 1 contract)
    visual_entities_json = Column(Text, nullable=True)

    # Soundtrack / Audio rotation
    selected_bgm = Column(String(50), nullable=True)

    # Image generation modality: "assistant" (Chrome extension) or "api" (Autonomous Meta Muse Image 1.0)
    image_generation_mode = Column(String(20), default="assistant", nullable=False)

    # Content rating / attenuation standard (default: "pg13_prestige")
    content_rating = Column(String(50), default="pg13_prestige", nullable=False)

    # Book / Editorial Branch
    has_book_chapter = Column(Boolean, default=False, nullable=False)
    has_book_audiobook = Column(Boolean, default=False, nullable=False)
    book_audiobook_engine = Column(String(50), nullable=True)
    book_audiobook_fallback_reason = Column(String(255), nullable=True)

    # YouTube Publishing
    youtube_video_id = Column(String(50), nullable=True)
    youtube_url = Column(String(255), nullable=True)
    youtube_status = Column(String(50), nullable=True)

    # Relationships.
    translations = relationship(
        "TranslationVariant",
        back_populates="project",
        cascade="all, delete-orphan",
    )
    assets = relationship(
        "AssetImage",
        back_populates="project",
        cascade="all, delete-orphan",
    )

    @property
    def folder_name(self) -> str:
        """Physical directory name in red-sun-japan-runtime/projects/."""
        if self.id is not None and self.slug:
            return get_project_folder_name(self.id, self.slug)
        return self.slug or ""

    def __repr__(self) -> str:
        return f"<VideoProject #{self.id} {self.title!r} [{self.status.value}]>"


class TranslationVariant(Base):
    """One row per (project, language). Holds script, blog, audio, timestamps."""

    __tablename__ = "translation_variants"
    __table_args__ = (UniqueConstraint("project_id", "language_code", name="uq_project_lang"),)

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("video_projects.id"), nullable=False, index=True)
    language_code = Column(String, nullable=False, index=True)   # "en", "pt-BR", ...

    # Content.
    script = Column(Text)
    blog_article = Column(Text)
    short_clips_json = Column(Text)   # JSON: [{"start":..., "end":..., "hook":"..."}]

    # Generated media paths.
    audio_path = Column(String)
    timestamps_path = Column(String)  # Whisper word-level JSON
    output_video_path = Column(String)

    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.datetime.utcnow,
        onupdate=datetime.datetime.utcnow,
        nullable=False,
    )

    project = relationship("VideoProject", back_populates="translations")

    def __repr__(self) -> str:
        return f"<TranslationVariant #{self.id} project={self.project_id} lang={self.language_code}>"


class AssetImage(Base):
    """One row per downloaded image. The index for the on-disk assets_library/."""

    __tablename__ = "asset_images"
    __table_args__ = (UniqueConstraint("source_url", name="uq_asset_source_url"),)

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("video_projects.id"), nullable=True, index=True)

    # On-disk path (relative to ASSETS_LIBRARY_DIR).
    theme = Column(String, nullable=False, index=True)     # "samurai", "yokai", "edo_period", ...
    relative_path = Column(String, nullable=False)         # "samurai/ukiyo_e_xxx.jpg"

    # Provenance.
    source_name = Column(String, nullable=False)           # "Wikimedia Commons", "Met Museum"
    source_url = Column(String, nullable=False)            # canonical source URL
    license = Column(
        Enum(AssetLicense, values_callable=lambda e: [x.value for x in e]),
        default=AssetLicense.UNVERIFIED,
        nullable=False,
    )
    description = Column(Text)
    width = Column(Integer)
    height = Column(Integer)

    # Merch signals (set manually by Isamu when picking favorites).
    merch_candidate = Column(Integer, default=0, nullable=False, index=True)  # 0/1 boolean
    merit_score = Column(Integer)                          # Isamu's 1-5 manual rating

    downloaded_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)

    project = relationship("VideoProject", back_populates="assets")

    def __repr__(self) -> str:
        return f"<AssetImage #{self.id} {self.theme}/{os.path.basename(self.relative_path)}>"


class CostTracking(Base):
    """One row per OpenRouter API call. Surfaces per-episode spend for
    budgeting and per-model cost comparison.

    Populated by :func:`llm_client.record_cost` right after each call
    (or by the smoke-test harness). ``generation_id`` is OpenRouter's
    ``gen-XXXX`` id, useful for a follow-up ``GET /api/v1/generation?id=…``
    audit when the cached row lacks detail.

    ``prompt_tokens`` + ``completion_tokens`` mirror the LLMResult;
    ``native_usage`` keeps the raw usage dict so Anthropic-native fields
    like ``cache_read_input_tokens`` survive even if the dataclass
    schema evolves.
    """

    __tablename__ = "cost_tracking"
    __table_args__ = (
        UniqueConstraint("generation_id", name="uq_cost_generation_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("video_projects.id"), nullable=True, index=True)
    translation_variant_id = Column(
        Integer, ForeignKey("translation_variants.id"), nullable=True, index=True
    )

    # What was being produced when the cost accrued.
    operation = Column(String, nullable=False, index=True)
    # One of: ``script``, ``blog``, ``review``, ``shorts``, ``translate``.
    model = Column(String, nullable=False, index=True)
    generation_id = Column(String, nullable=True, index=True)

    # Token accounting.
    prompt_tokens = Column(Integer, default=0, nullable=False)
    completion_tokens = Column(Integer, default=0, nullable=False)
    total_tokens = Column(Integer, default=0, nullable=False)

    # Cost + latency.
    cost_usd = Column(Float, default=0.0, nullable=False)
    elapsed_sec = Column(Float, default=0.0, nullable=False)
    finish_reason = Column(String, nullable=True)

    # Raw usage blob (cache_read_input_tokens etc.). JSON column keeps
    # forward compatibility with new provider-specific fields.
    native_usage = Column(JSON, nullable=True)

    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)

    def __repr__(self) -> str:
        return (
            f"<CostTracking #{self.id} {self.operation} model={self.model} "
            f"cost=${self.cost_usd:.6f}>"
        )


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI dependency
# ─────────────────────────────────────────────────────────────────────────────
def get_db():
    """FastAPI dependency: yields a request-scoped session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_schema() -> None:
    """Create all tables. Idempotent — safe to call on every startup."""
    from sqlalchemy import text
    Base.metadata.create_all(bind=engine)
    with engine.connect() as conn:
        try:
            conn.execute(text("ALTER TABLE video_projects ADD COLUMN visual_entities_json TEXT"))
            conn.commit()
        except Exception:
            pass
        try:
            conn.execute(text("ALTER TABLE video_projects ADD COLUMN image_generation_mode VARCHAR(20) DEFAULT 'assistant'"))
            conn.commit()
        except Exception:
            pass
        try:
            conn.execute(text("ALTER TABLE video_projects ADD COLUMN content_rating VARCHAR(50) DEFAULT 'pg13_prestige'"))
            conn.commit()
        except Exception:
            pass
        try:
            conn.execute(text("ALTER TABLE video_projects ADD COLUMN has_book_chapter BOOLEAN DEFAULT 0"))
            conn.commit()
        except Exception:
            pass
        try:
            conn.execute(text("ALTER TABLE video_projects ADD COLUMN has_book_audiobook BOOLEAN DEFAULT 0"))
            conn.commit()
        except Exception:
            pass
        try:
            conn.execute(text("ALTER TABLE video_projects ADD COLUMN book_audiobook_engine VARCHAR(50)"))
            conn.commit()
        except Exception:
            pass
        try:
            conn.execute(text("ALTER TABLE video_projects ADD COLUMN book_audiobook_fallback_reason VARCHAR(255)"))
            conn.commit()
        except Exception:
            pass
        try:
            conn.execute(text("ALTER TABLE video_projects ADD COLUMN youtube_video_id VARCHAR(50)"))
            conn.commit()
        except Exception:
            pass
        try:
            conn.execute(text("ALTER TABLE video_projects ADD COLUMN youtube_url VARCHAR(255)"))
            conn.commit()
        except Exception:
            pass
        try:
            conn.execute(text("ALTER TABLE video_projects ADD COLUMN youtube_status VARCHAR(50)"))
            conn.commit()
        except Exception:
            pass
