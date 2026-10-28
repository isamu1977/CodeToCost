"""Markdown sanitization for TTS.

Pure deterministic function. No I/O, no randomness, no time.

Stripped markers (per audit + design.md D13):
- `*`, `**` (emphasis/bold)
- `_`, `__` (italic/underline)
- `# `#` headings (with optional closing space)
- `> ` blockquote marker
- backticks (`)
- `[text](url)` markdown links -> plain text
- bare http(s) URLs
"""
from __future__ import annotations

import re


_ASTERISK_RE = re.compile(r"\*{1,2}([^*\n]+)\*{1,2}")
_UNDERSCORE_RE = re.compile(r"_{1,2}([^_\n]+)_{1,2}")
_LINK_RE = re.compile(r"\[([^\]\n]+)\]\((?:https?://|www\.)?[^)\n]+\)")
_HEADING_RE = re.compile(r"^#{1,6}\s+", flags=re.MULTILINE)
_BLOCKQUOTE_RE = re.compile(r"^>\s*", flags=re.MULTILINE)
_BARE_URL_RE = re.compile(r"https?://[^\s)\]\"']+")
_BACKTICK_RE = re.compile(r"`+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


def sanitize_for_tts(text: str) -> str:
    """Strip Markdown constructs that TTS engines read literally."""
    if not text:
        return ""

    out = text
    out = _ASTERISK_RE.sub(r"\1", out)
    out = _UNDERSCORE_RE.sub(r"\1", out)
    out = _LINK_RE.sub(r"\1", out)
    out = _HEADING_RE.sub("", out)
    out = _BLOCKQUOTE_RE.sub("", out)
    out = _BARE_URL_RE.sub("", out)
    out = _BACKTICK_RE.sub("", out)
    out = out.replace("*", "")
    out = out.replace("_", "")
    out = _BLANK_LINES_RE.sub("\n\n", out)

    return out.strip()


def has_markdown_artifacts(text: str) -> bool:
    """True if `text` still contains any marker TTS would read aloud."""
    if not text:
        return False
    if _ASTERISK_RE.search(text):
        return True
    if _UNDERSCORE_RE.search(text):
        return True
    if _LINK_RE.search(text):
        return True
    if _HEADING_RE.search(text):
        return True
    if _BLOCKQUOTE_RE.search(text):
        return True
    if _BARE_URL_RE.search(text):
        return True
    if _BACKTICK_RE.search(text):
        return True
    if "*" in text or "_" in text:
        return True
    return False