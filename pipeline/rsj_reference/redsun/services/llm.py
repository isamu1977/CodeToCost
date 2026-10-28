"""LLM service for the wired production pipeline.

The new `PipelineRunner` calls LLM-driven phase functions via the
`SERVICES` registry. The production implementation in this file
posts to the existing FastAPI endpoints (the same surface the legacy
orchestrator already exercises). Tests inject a stub via the same
`SERVICES` registry and never reach the network.

The `LLMService` exposes one method per phase:

- `open_or_create_project(slug, payload)` — POST /projects
- `generate_script(project_id, language)` — POST /projects/{id}/script
- `review_script(project_id, language, source_type, atmosphere)`
- `generate_blog(project_id, language)`
- `generate_shorts(project_id, language, max_clips)`
- `generate_post_kit(project_id, language)`

The default base URL is read from
`defaults.LLM_DEFAULT_BASE_URL`. The port comes from the runner's
context (the CLI's `--port` flag); the production runtime uses the
default `8771` to match `run_server_isolated.sh`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import requests


logger = logging.getLogger("redsun.services.llm")


# ── Result containers ─────────────────────────────────────────────────


@dataclass
class LLMResult:
    """Generic LLM service response.

    The text field is the primary payload (script body, blog article,
    etc.). The model and elapsed_sec are recorded for the structured
    `PipelineResult`. The extras dict carries phase-specific metadata
    (e.g. `revised_text` for review, `clips` for shorts).
    """

    text: str = ""
    model: str = ""
    elapsed_sec: float = 0.0
    project_id: Optional[int] = None
    extras: dict = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.extras is None:
            self.extras = {}


@dataclass
class ProjectCreateResult:
    """Specialized result for `open_or_create_project`."""

    project_id: int
    raw: dict


# ── HTTP client ─────────────────────────────────────────────────────


class LLMService:
    """HTTP-backed LLM driver for the wired pipeline.

    Construct via `LLMService.from_context(ctx)` so the base URL is
    derived from the `ProjectContext`'s `llm` slice. Tests construct
    LLMService directly or pass a stub callable.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout: int = 300,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    @classmethod
    def from_context(cls, context, *, timeout: int = 300) -> "LLMService":
        """Build an LLMService from a `ProjectContext`.

        Uses `context.llm.base_url` as the base URL. Falls back to
        `http://127.0.0.1:8771` when the context does not expose
        `base_url` (e.g. tests that build a minimal context).
        """
        base_url = getattr(getattr(context, "llm", None), "base_url", None) or "http://127.0.0.1:8771"
        return cls(base_url, timeout=timeout)

    # ── Endpoints ────────────────────────────────────────────────

    def open_or_create_project(
        self,
        slug: str,
        payload: dict,
    ) -> ProjectCreateResult:
        """POST /projects. Returns a `ProjectCreateResult`.

        The server may create a new row or return an existing one
        matching the slug. The caller treats both as success.
        """
        url = f"{self.base_url}/projects"
        params = {"slug": slug}
        resp = requests.post(
            url,
            params=params,
            json=payload,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        raw = resp.json()
        project_id = int(raw["id"])
        return ProjectCreateResult(project_id=project_id, raw=raw)

    def generate_script(
        self,
        project_id: int,
        language: str,
    ) -> LLMResult:
        """POST /projects/{id}/script. Returns the generated script body."""
        url = f"{self.base_url}/projects/{project_id}/script"
        resp = requests.post(
            url,
            json={"language_code": language},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        raw = resp.json()
        return LLMResult(
            text=raw.get("script", "") or "",
            model=raw.get("model", "") or "",
            elapsed_sec=float(raw.get("elapsed_sec", 0.0) or 0.0),
            project_id=project_id,
        )

    def review_script(
        self,
        project_id: int,
        language: str,
        *,
        source_type: Optional[str] = None,
        atmosphere: Optional[str] = None,
    ) -> LLMResult:
        """POST /projects/{id}/review.

        Returns the reviewed body. When the reviewer emits
        `revised_text`, it is stored under `extras["revised_text"]`.
        """
        url = f"{self.base_url}/projects/{project_id}/review"
        payload: dict = {"language_code": language}
        if source_type:
            payload["source_type"] = source_type
        if atmosphere:
            payload["atmosphere"] = atmosphere
        resp = requests.post(url, json=payload, timeout=self.timeout)
        resp.raise_for_status()
        raw = resp.json()
        revised = raw.get("revised_script") or raw.get("revised_text") or ""
        return LLMResult(
            text=raw.get("script", "") or "",
            model=raw.get("model", "") or "",
            elapsed_sec=float(raw.get("elapsed_sec", 0.0) or 0.0),
            project_id=project_id,
            extras={
                "approved": raw.get("approved"),
                "score": raw.get("score"),
                "issues": raw.get("issues", []),
                "revised_text": revised,
            },
        )

    def generate_blog(
        self,
        project_id: int,
        language: str,
    ) -> LLMResult:
        """POST /projects/{id}/blog."""
        url = f"{self.base_url}/projects/{project_id}/blog"
        resp = requests.post(
            url,
            json={"language_code": language},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        raw = resp.json()
        return LLMResult(
            text=raw.get("blog_article", "") or "",
            model=raw.get("model", "") or "",
            elapsed_sec=float(raw.get("elapsed_sec", 0.0) or 0.0),
            project_id=project_id,
        )

    def generate_shorts(
        self,
        project_id: int,
        language: str,
        *,
        max_clips: int = 5,
    ) -> LLMResult:
        """POST /projects/{id}/shorts."""
        url = f"{self.base_url}/projects/{project_id}/shorts"
        resp = requests.post(
            url,
            json={"language_code": language, "max_clips": max_clips},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        raw = resp.json()
        clips = raw.get("clips", []) or []
        return LLMResult(
            text=raw.get("text", "") or "",
            model=raw.get("model", "") or "",
            elapsed_sec=float(raw.get("elapsed_sec", 0.0) or 0.0),
            project_id=project_id,
            extras={"clips": clips},
        )

    def generate_post_kit(
        self,
        project_id: int,
        language: str,
    ) -> LLMResult:
        """POST /projects/{id}/post-kit."""
        url = f"{self.base_url}/projects/{project_id}/post-kit"
        resp = requests.post(
            url,
            json={"language_code": language},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        raw = resp.json()
        return LLMResult(
            text=raw.get("text", "") or "",
            model=raw.get("model", "") or "",
            elapsed_sec=float(raw.get("elapsed_sec", 0.0) or 0.0),
            project_id=project_id,
            extras={"files": raw.get("files", []) or []},
        )


# ── Convenience helpers for stub services in tests ──────────────────


def stub_open_or_create_project(
    project_id: int = 999,
    slug: str = "wanyudo",
) -> ProjectCreateResult:
    """Return a canned ProjectCreateResult for test stubs."""

    return ProjectCreateResult(
        project_id=project_id,
        raw={"id": project_id, "slug": slug},
    )


def stub_generate_script(
    body: str = "",
    *,
    project_id: int = 999,
    model: str = "stub-llm",
) -> LLMResult:
    """Return a canned LLMResult for `generate_script`.

    The default body is a 200-word narration honoring the
    `QualityConfig.min_audio_duration_sec * chars_per_second` ratio
    (~1700 chars at 60s × 28 chars/sec). Tests may override the
    body to exercise empty / short / malformed inputs.
    """
    if not body:
        body = (
            "The burning wheel of Wanyūdō rolled through the village "
            "at midnight, its tormented face screaming through the "
            "flames that consumed it. Villagers whispered that anyone "
            "who saw the wheel would have their soul stolen before "
            "dawn. Travelers crossed themselves and quickened their "
            "pace along the mountain road, refusing to look back at "
            "the glow that flickered between the trees. The monks at "
            "the nearby temple rang their bells loudly, hoping the "
            "sound would drive the yokai away. The wheel slowed, "
            "hesitated, and then rolled on toward the next village, "
            "leaving only scorched earth and a faint smell of burning "
            "hair. Old women told the children to stay indoors until "
            "the moon set, for the yokai hunted souls that wandered "
            "alone after nightfall. The priests offered prayers and "
            "burned incense, but the wheel returned every month, "
            "always on the fifteenth night, always hungry. Some said "
            "it was the spirit of a man who had been burned at the "
            "stake for a crime he did not commit. Others said it was "
            "a punishment from the gods for the village's sins. No "
            "one knew for certain, but everyone feared the sound of "
            "wooden wheels on stone, and the fire that burned without "
            "fuel or smoke."
        )
    return LLMResult(text=body, model=model, project_id=project_id)


def stub_review_script(
    body: str = "",
    *,
    revised_text: str = "",
    project_id: int = 999,
    model: str = "stub-llm",
) -> LLMResult:
    """Return a canned LLMResult for `review_script`."""
    if not body:
        body = (
            "The burning wheel of Wanyūdō rolled through the village "
            "at midnight, its screaming face lighting the dark road. "
            "Travelers quickened their pace, refusing to look back."
        )
    return LLMResult(
        text=body,
        model=model,
        project_id=project_id,
        extras={
            "approved": True,
            "score": 9,
            "issues": [],
            "revised_text": revised_text,
        },
    )


def stub_generate_blog(
    body: str = "",
    *,
    project_id: int = 999,
    model: str = "stub-llm",
) -> LLMResult:
    """Return a canned LLMResult for `generate_blog`."""
    if not body:
        body = "# Wanyūdō — The Burning Wheel\n\nA glimpse into the yokai..."
    return LLMResult(text=body, model=model, project_id=project_id)


def stub_generate_shorts(
    *,
    project_id: int = 999,
    model: str = "stub-llm",
    max_clips: int = 5,
) -> LLMResult:
    """Return a canned LLMResult for `generate_shorts`."""
    clips = [
        {"index": i, "title": f"clip {i}", "duration_sec": 30}
        for i in range(max_clips)
    ]
    return LLMResult(
        text="",
        model=model,
        project_id=project_id,
        extras={"clips": clips},
    )


def stub_generate_post_kit(
    *,
    project_id: int = 999,
    model: str = "stub-llm",
) -> LLMResult:
    """Return a canned LLMResult for `generate_post_kit`."""
    return LLMResult(
        text="",
        model=model,
        project_id=project_id,
        extras={
            "files": [
                {"filename": "youtube_description.md", "purpose": "youtube"},
            ],
        },
    )
