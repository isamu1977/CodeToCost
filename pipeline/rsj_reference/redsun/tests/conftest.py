"""Pytest fixtures for the Red Sun Japan regression suite.

All tests use `tmp_path` to avoid touching real projects under
`motor-python/storage/`, `projects/`, or `render-remotion/`.
"""
from __future__ import annotations

import wave
from pathlib import Path

import pytest

from redsun.workspace import ProjectWorkspace
from redsun.context import (
    ProjectContext,
    EpisodeMetadata,
    ThemeMetadata,
    LLMConfig,
    TTSConfig,
    ImageConfig,
    RenderConfig,
    VisualDensityConfig,
    QualityConfig,
)
from redsun.narration import sanitize_for_tts
from redsun.pipeline.checkpoints import (
    CheckpointStore,
    DEPENDENCIES,
    downstream_phases,
)


@pytest.fixture
def make_workspace(tmp_path):
    """Factory that builds a ProjectWorkspace under tmp_path.

    Usage:
            ws = make_workspace("kappa")
    """
    def _factory(slug: str) -> ProjectWorkspace:
        ws = ProjectWorkspace.from_slug(slug)
        # Redirect the projects root to the test tmp dir.
        ws.projects_root = tmp_path
        ws.create()
        return ws

    return _factory


@pytest.fixture
def make_context(make_workspace):
    """Factory that builds a ProjectContext with sane defaults."""
    def _factory(
        slug: str = "kappa",
        title: str = "Kappa Test",
        theme_metadata: ThemeMetadata = None,
        **overrides,
    ) -> ProjectContext:
        ws = make_workspace(slug)
        episode = EpisodeMetadata(
            slug=slug,
            title=title,
            output_language="en",
        )
        ctx = ProjectContext(
            project_id=99,
            workspace=ws,
            episode=episode,
            theme_metadata=theme_metadata or ThemeMetadata(),
        )
        # apply per-slice overrides if requested
        for key, value in overrides.items():
            if hasattr(ctx, key):
                setattr(ctx, key, value)
        return ctx

    return _factory


@pytest.fixture
def write_silent_wav(tmp_path):
    """Write a silent (or partially silent) WAV file at the given path."""
    def _writer(
        name: str,
        duration_sec: float,
        *,
        sample_rate: int = 24000,
        silent_tail_sec: float = 0.0,
    ) -> Path:
        path = tmp_path / name
        n_frames = int(duration_sec * sample_rate)
        n_tail_silent = int(silent_tail_sec * sample_rate)
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            # Main: silence
            if n_frames - n_tail_silent > 0:
                w.writeframes(b"\x00\x00" * (n_frames - n_tail_silent))
            # Tail: silence
            if n_tail_silent > 0:
                w.writeframes(b"\x00\x00" * n_tail_silent)
        return path

    return _writer