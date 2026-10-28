"""Structured `PipelineResult` returned by `PipelineRunner.run(...)`.

This is the in-tree replacement for the legacy `summary.json` blob.
Both success ("READY") and failure ("FAILED") paths use the same
shape so the CLI, the FastAPI endpoint, and downstream tooling
(post kits, blog export) can rely on a uniform structure.

Schema version is `1`. Backward-incompatible changes require a new
OpenSpec change that bumps `SCHEMA_VERSION` and the OpenSpec spec.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional


SCHEMA_VERSION = 1


@dataclass
class PhaseResult:
    """Per-phase execution record."""

    phase: str
    status: str  # "completed" | "skipped" | "failed"
    duration_seconds: float = 0.0
    error: Optional[str] = None
    error_code: Optional[str] = None
    expected: Any = None
    actual: Any = None


@dataclass
class ScriptArtifact:
    """Canonical script artifact produced by the LLM-driven script phases.

    `source` is one of:
      - "generated" — the body was produced by `generate_script`
      - "reviewed" — the body was produced by `review_script`'s
        `revised_text` (overrides `generated`)
      - "source_text" — the runner fell back to
        `context.episode.source_text` (no LLM call was made)
    """

    path: str = ""
    sha256: str = ""
    source: str = "generated"  # "generated" | "reviewed" | "source_text"
    model: str = ""


@dataclass
class PipelineResult:
    """Structured outcome of one `PipelineRunner.run(...)` invocation."""

    schema_version: int = SCHEMA_VERSION
    status: str = "FAILED"  # "READY" | "FAILED"
    technical_slug: str = ""
    workspace: str = ""
    project_id: Optional[int] = None
    pipeline_run_id: str = ""
    started_at: str = ""
    finished_at: str = ""
    duration_seconds: float = 0.0
    phases: list[PhaseResult] = field(default_factory=list)
    artifacts: dict = field(default_factory=dict)

    # ── success-only fields (READY) ──────────────────────────────────
    tts_engine: Optional[str] = None
    voice: Optional[str] = None
    transcript_coverage: Optional[float] = None
    visual_beats: Optional[int] = None
    images_validated: Optional[int] = None
    video_main: Optional[str] = None
    video_final: Optional[str] = None
    script: Optional[dict] = None  # ScriptArtifact serialized as dict

    # ── failure-only fields (FAILED) ─────────────────────────────────
    failed_phase: Optional[str] = None
    error_code: Optional[str] = None
    expected: Any = None
    actual: Any = None

    def to_dict(self) -> dict:
        """Serialize the result for JSON output."""
        return asdict(self)

    @property
    def is_ready(self) -> bool:
        return self.status == "READY"

    @property
    def is_failed(self) -> bool:
        return self.status == "FAILED"
