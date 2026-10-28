"""Phonetic pronunciation normalization for TTS — decoupled from ``redsun``.

The script on disk (``script_en.md`` / ``video_spec.json``) keeps authentic
spelling and standard English, while the TTS engine receives phonetically guided
text so it never stumbles on acronyms, symbols, currency or proper nouns.

Three layers, applied in order:

1. **Canonical map** — ``pipeline/core/config/pronunciation_map.json``
   (term -> respelling, e.g. ``Edo`` -> ``Eh-doh``). RSJ heritage; extend or
   prune per channel.
2. **Channel dictionary** — ``agent_workspace/phonetic_dictionary.json``
   (acronym/symbol rules, numeric patterns, first-occurrence spell-out).
   This is the CodeToCost layer and the one that matters for English B2B.
3. **Per-project override** — ``projects/<slug>/pronunciation_map.json``
   (or ``.../audio/pronunciation_map.json``), highest precedence.

No dependency on the ``redsun`` package. Paths are resolved relative to this
file so the module works regardless of the caller's working directory.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("codetocost.pronunciation")

# ── Path resolution ─────────────────────────────────────────────────────────
_CORE_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _CORE_DIR.parent.parent  # CodeToCost/

CANONICAL_MAP_PATH = _CORE_DIR / "config" / "pronunciation_map.json"
CHANNEL_DICTIONARY_PATH = _PROJECT_ROOT / "agent_workspace" / "phonetic_dictionary.json"

# Per-project override candidates, in precedence order.
_OVERRIDE_CANDIDATES = (
    "pronunciation_map.json",
    "audio/pronunciation_map.json",
    "config/pronunciation_map.json",
)


# ── Number to words (for "$12/mo" -> "twelve dollars a month") ──────────────
_ONES = (
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
    "sixteen", "seventeen", "eighteen", "nineteen",
)
_TENS = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")
_SCALES = ("", " thousand", " million", " billion", " trillion")


def _chunk_to_words(n: int) -> str:
    """Convert 0..999 to words."""
    if n < 20:
        return _ONES[n]
    if n < 100:
        tens, ones = divmod(n, 10)
        return _TENS[tens] + ("-" + _ONES[ones] if ones else "")
    hundreds, rest = divmod(n, 100)
    return _ONES[hundreds] + " hundred" + (" " + _chunk_to_words(rest) if rest else "")


def num_to_words(value: str) -> str:
    """Convert a numeric string to spoken English.

    Handles integers and simple decimals (``12`` -> ``twelve``,
    ``100000`` -> ``one hundred thousand``, ``10.59`` -> ``ten point five nine``).
    Non-numeric input is returned unchanged.
    """
    s = str(value).strip().replace(",", "")
    if not s:
        return s
    negative = s.startswith("-")
    if negative:
        s = s[1:]

    if "." in s:
        whole, _, frac = s.partition(".")
        if whole == "":
            whole = "0"
        if not (whole.isdigit() and (frac == "" or frac.isdigit())):
            return value
        parts = [_chunk_to_words(int(whole)) if whole else "zero"]
        if frac:
            parts.append("point " + " ".join(_ONES[int(d)] if d.isdigit() else d for d in frac))
        out = " ".join(p for p in parts if p)
    elif s.isdigit():
        n = int(s)
        if n == 0:
            out = "zero"
        else:
            chunks: list[str] = []
            scale_idx = 0
            while n > 0 and scale_idx < len(_SCALES):
                n, rem = divmod(n, 1000)
                if rem:
                    chunks.append(_chunk_to_words(rem) + _SCALES[scale_idx])
                scale_idx += 1
            out = " ".join(reversed(chunks))
    else:
        return value

    return ("negative " + out) if negative else out


# ── Loaders ─────────────────────────────────────────────────────────────────
def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        logger.debug("Pronunciation source not found: %s", path)
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception as exc:
        logger.warning("Failed to parse %s: %s", path, exc)
        return {}


def get_canonical_pronunciation_path() -> Path:
    """Resolve the canonical pronunciation map path.

    Precedence: ``PRONUNCIATION_MAP_PATH`` env var, then the packaged config.
    """
    env_path = os.environ.get("PRONUNCIATION_MAP_PATH")
    if env_path:
        p = Path(env_path)
        if p.exists():
            return p
    if CANONICAL_MAP_PATH.exists():
        return CANONICAL_MAP_PATH
    cwd_candidate = Path.cwd() / "config" / "pronunciation_map.json"
    if cwd_candidate.exists():
        return cwd_candidate
    return CANONICAL_MAP_PATH


def load_pronunciation_map(slug: Optional[str] = None) -> dict[str, str]:
    """Load the canonical term map, with optional per-project overrides.

    Order of precedence (last wins):
      1. ``config/pronunciation_map.json`` (canonical)
      2. ``projects/<slug>/pronunciation_map.json`` (or the audio/ variants)
    """
    mapping: dict[str, str] = {}

    canonical = _load_json(get_canonical_pronunciation_path())
    mapping.update({str(k): str(v) for k, v in canonical.items()})

    if slug:
        base = _PROJECT_ROOT / "projects" / slug
        for rel in _OVERRIDE_CANDIDATES:
            cand = base / rel
            if cand.exists():
                try:
                    proj_raw = json.loads(cand.read_text(encoding="utf-8"))
                    if isinstance(proj_raw, dict):
                        mapping.update({str(k): str(v) for k, v in proj_raw.items()})
                        logger.info("Applied project pronunciation overrides from %s", cand)
                        break
                except Exception as exc:
                    logger.warning("Failed to parse project override at %s: %s", cand, exc)

    return mapping


def load_channel_dictionary() -> dict[str, Any]:
    """Load ``agent_workspace/phonetic_dictionary.json`` (CodeToCost layer)."""
    return _load_json(CHANNEL_DICTIONARY_PATH)


# ── Applicators ─────────────────────────────────────────────────────────────
def _apply_term_map(text: str, mapping: dict[str, str]) -> str:
    """Apply a term->respelling map with unicode-safe word boundaries.

    Keys are sorted longest-first so multi-word entities are replaced before
    their single-token components. The negative lookaround prevents substring
    collisions (e.g. ``Edo`` inside ``freedom``).
    """
    if not text or not mapping:
        return text

    result = text
    for original in sorted(mapping, key=len, reverse=True):
        replacement = mapping[original]
        if not original or not replacement or original == replacement:
            continue
        pattern = re.compile(
            r"(?<![A-Za-z0-9\u00C0-\u024F])" + re.escape(original)
            + r"(?![A-Za-z0-9\u00C0-\u024F])",
            re.IGNORECASE,
        )
        result = pattern.sub(replacement.replace("\\", "\\\\"), result)
    return result


def _apply_channel_dictionary(text: str) -> str:
    """Apply the CodeToCost phonetic dictionary: rules, numerics, spell-outs."""
    if not text:
        return text

    d = load_channel_dictionary()
    if not d:
        return text

    # 1) Plain term rules (acronyms, symbols).
    rules = d.get("rules") or {}
    if rules:
        text = _apply_term_map(text, {str(k): str(v) for k, v in rules.items()})

    # 2) Numeric patterns ($12/mo, 100k, 45%, 3x).
    for entry in d.get("numeric_patterns") or []:
        pattern = entry.get("pattern")
        template = entry.get("template")
        if not pattern or not template:
            continue
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            logger.warning("Bad numeric_pattern %r: %s", pattern, exc)
            continue

        def _sub(m: re.Match, _tpl: str = template) -> str:
            spoken = num_to_words(m.group(1)) if m.lastindex else ""
            return _tpl.replace("{{amount}}", spoken)

        text = regex.sub(_sub, text)

    # 3) First-occurrence spell-out: expand an acronym only on its first use.
    for term in d.get("first_occurrence_spellout") or []:
        expanded = rules.get(term)
        if not expanded:
            continue
        regex = re.compile(
            r"(?<![A-Za-z0-9\u00C0-\u024F])" + re.escape(term)
            + r"(?![A-Za-z0-9\u00C0-\u024F])",
        )
        text = regex.sub(lambda _m, _e=expanded: _e, text, count=1)

    return text


def apply_pronunciation_map(
    text: str,
    slug: Optional[str] = None,
    custom_map: Optional[dict[str, str]] = None,
) -> str:
    """Apply all phonetic layers to ``text`` for TTS narration.

    Combines the canonical term map, the CodeToCost channel dictionary and any
    per-project override. Longest-match-first, unicode-safe boundaries.
    """
    if not text:
        return ""

    # Channel dictionary first (acronyms + numerics), then canonical term map.
    text = _apply_channel_dictionary(text)

    mapping = custom_map if custom_map is not None else load_pronunciation_map(slug=slug)
    if mapping:
        text = _apply_term_map(text, mapping)

    return text
