"""Deterministic Audio & Soundtrack Management Service.

Handles BGM catalog registration, intelligent non-repeating track rotation
across projects, physical asset resolution for Remotion/FFmpeg audio mix,
and legal Creative Commons attribution generation.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session
from database import VideoProject

logger = logging.getLogger("redsun.audio_manager")

_DEFAULT_MUSIC_DIR = Path("/Users/isamumatsuyama/Documents/red-sun-japan-runtime/music_library")
if not _DEFAULT_MUSIC_DIR.exists():
    _FALLBACK_REPO_DIR = Path(__file__).resolve().parent.parent.parent / "music_library"
    if _FALLBACK_REPO_DIR.exists():
        _DEFAULT_MUSIC_DIR = _FALLBACK_REPO_DIR

MUSIC_LIBRARY_DIR = Path(os.environ.get("REDSUN_MUSIC_DIR", str(_DEFAULT_MUSIC_DIR)))
MANIFEST_PATH = MUSIC_LIBRARY_DIR / "manifest.json"


class AudioManager:
    """Manages BGM library catalog, round-robin rotation, and legal attribution."""

    @staticmethod
    def load_catalog() -> list[dict[str, Any]]:
        """Load track metadata from the canonical manifest.json."""
        if not MANIFEST_PATH.exists():
            logger.warning("BGM manifest not found at %s", MANIFEST_PATH)
            return []
        try:
            with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("tracks", [])
        except Exception as exc:
            logger.error("Failed to parse BGM manifest %s: %s", MANIFEST_PATH, exc)
            return []

    @classmethod
    def get_track_by_id(cls, track_id: str) -> dict[str, Any] | None:
        """Find track metadata by its unique ID (e.g. 'bgm_01')."""
        tracks = cls.load_catalog()
        for t in tracks:
            if t.get("id") == track_id:
                return t
        return None

    @classmethod
    def select_next_bgm(cls, db: Session, project_id: int, force_new: bool = False) -> dict[str, Any]:
        """
        Seleciona a próxima trilha em rotação, evitando as músicas
        utilizadas nos últimos 5 projetos concluídos.
        """
        tracks = cls.load_catalog()
        if not tracks:
            raise FileNotFoundError(f"BGM catalog manifest not found or empty at {MANIFEST_PATH}")

        project = db.query(VideoProject).filter(VideoProject.id == project_id).first()
        if not force_new and project and project.selected_bgm:
            existing_track = cls.get_track_by_id(project.selected_bgm)
            if existing_track:
                return existing_track

        # Consulta as últimas faixas usadas no banco
        recent_projects = (
            db.query(VideoProject.selected_bgm)
            .filter(VideoProject.id != project_id, VideoProject.selected_bgm.isnot(None))
            .order_by(VideoProject.id.desc())
            .limit(5)
            .all()
        )
        recent_bgm_ids = {p[0] for p in recent_projects if p[0]}

        # Filtra faixas que não foram usadas recentemente
        available_tracks = [t for t in tracks if t["id"] not in recent_bgm_ids]
        selected = available_tracks[0] if available_tracks else tracks[0]

        # Persiste a escolha no projeto atual
        if project:
            project.selected_bgm = selected["id"]
            db.commit()
            logger.info(
                "Assigned BGM '%s' (%s) to project #%d (%s)",
                selected["id"],
                selected.get("title"),
                project.id,
                project.slug,
            )

        return selected

    @classmethod
    def get_bgm_file_path(cls, track_id: str) -> Path | None:
        """Resolve absolute physical path to the track's audio file."""
        track = cls.get_track_by_id(track_id)
        if not track:
            return None
        file_path = MUSIC_LIBRARY_DIR / track["filename"]
        return file_path if file_path.exists() else None

    @classmethod
    def format_attribution_text(cls, track: dict[str, Any]) -> str:
        """Produce the standard legal attribution text for the track."""
        if not track:
            return ""
        return track.get(
            "credit_text",
            f"Music: '{track.get('title')}' by {track.get('artist', 'Kraosando')} — Licensed under {track.get('license', 'CC BY 4.0')}"
        )
