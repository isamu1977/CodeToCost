"""Phonetic pronunciation dictionary service for Kokoro TTS.

Decouples phonetic pronunciation overrides from the canonical script.
The script on disk (script_en.md) retains authentic Japanese names and standard English spelling,
while Kokoro TTS receives phonetic respellings (e.g. 'Edo' -> 'Eh-doh', 'Yokai' -> 'Yoh-kai')
for natural, prestige-documentary pronunciation.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("redsun.pronunciation")

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "pronunciation_map.json"
DEFAULT_RUNTIME_ROOT = Path("/Users/isamumatsuyama/Documents/red-sun-japan-runtime/projects")


def get_canonical_pronunciation_path() -> Path:
    """Resolve the canonical config path to pronunciation_map.json."""
    env_path = os.environ.get("PRONUNCIATION_MAP_PATH")
    if env_path:
        p = Path(env_path)
        if p.exists():
            return p
    if DEFAULT_CONFIG_PATH.exists():
        return DEFAULT_CONFIG_PATH
    cwd_candidate = Path.cwd() / "config" / "pronunciation_map.json"
    if cwd_candidate.exists():
        return cwd_candidate
    return DEFAULT_CONFIG_PATH


def load_pronunciation_map(slug: Optional[str] = None) -> dict[str, str]:
    """Load canonical pronunciation map with optional per-project overrides.
    
    Order of precedence:
    1. Per-project override: projects/{slug}/pronunciation_map.json
    2. Canonical config: config/pronunciation_map.json
    """
    canonical_path = get_canonical_pronunciation_path()
    mapping: dict[str, str] = {}

    if canonical_path.exists():
        try:
            raw = json.loads(canonical_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                mapping.update({str(k): str(v) for k, v in raw.items()})
        except Exception as exc:
            logger.error("Failed to load canonical pronunciation map from %s: %s", canonical_path, exc)
    else:
        logger.warning("Canonical pronunciation map not found at %s.", canonical_path)

    # Check project-level override if slug is provided
    if slug:
        try:
            from redsun.workspace import ProjectWorkspace
            ws_root = ProjectWorkspace.from_slug(slug).root
        except Exception:
            ws_root = DEFAULT_RUNTIME_ROOT / slug

        candidates = [
            ws_root / "audio" / "pronunciation_map.json",
            ws_root / "pronunciation_map.json",
            ws_root / "config" / "pronunciation_map.json",
            DEFAULT_RUNTIME_ROOT / slug / "audio" / "pronunciation_map.json",
            Path.cwd() / "projects" / slug / "pronunciation_map.json",
        ]
        for cand in candidates:
            if cand.exists():
                try:
                    proj_raw = json.loads(cand.read_text(encoding="utf-8"))
                    if isinstance(proj_raw, dict):
                        mapping.update({str(k): str(v) for k, v in proj_raw.items()})
                        logger.info("Applied episode-specific pronunciation overrides from %s", cand)
                        break
                except Exception as exc:
                    logger.warning("Failed to parse project pronunciation override at %s: %s", cand, exc)

    return mapping


def apply_pronunciation_map(
    text: str,
    slug: Optional[str] = None,
    custom_map: Optional[dict[str, str]] = None,
) -> str:
    """Apply phonetic substitutions to text for TTS narration.
    
    Uses unicode-safe word boundaries to prevent substring collisions (e.g. 'Edo' inside 'freedom').
    Terms are sorted by length descending so multi-word entities (e.g. 'Ueda Akinari') are replaced
    before single tokens.
    """
    if not text:
        return ""

    mapping = custom_map if custom_map is not None else load_pronunciation_map(slug=slug)
    if not mapping:
        return text

    # Sort keys by length descending to match longest phrases first
    sorted_keys = sorted(mapping.keys(), key=lambda k: len(k), reverse=True)

    result = text
    for original in sorted_keys:
        replacement = mapping[original]
        if not original or not replacement or original == replacement:
            continue

        # Regex with unicode/accent-safe boundary checks
        # (?<![A-Za-z0-9\u00C0-\u024F]) ensures we are not preceded by an ASCII or accented Latin character
        # (?![A-Za-z0-9\u00C0-\u024F]) ensures we are not followed by an ASCII or accented Latin character
        pattern = re.compile(
            r"(?<![A-Za-z0-9\u00C0-\u024F])" + re.escape(original) + r"(?![A-Za-z0-9\u00C0-\u024F])",
            re.IGNORECASE,
        )
        result = pattern.sub(replacement, result)

    return result
