"""Narration validator — runs after `prepare_narration`."""
from __future__ import annotations

import re
from pathlib import Path

from ..exceptions import NarrationValidationError
from ..narration import has_markdown_artifacts
from . import ValidationResult


_WS_RE = re.compile(r"\s")


def validate_narration(
    narration_path,
    *,
    min_size_bytes: int = 100,
) -> ValidationResult:
    """Reject narration.txt that is empty, too small, contains
    Markdown artifacts, or has no whitespace at all.

    Always returns a ValidationResult; raises NarrationValidationError
    only when the file does not exist entirely.
    """
    narration_path = Path(narration_path)
    if not narration_path.exists():
        raise NarrationValidationError(
            f"narration file missing: {narration_path}"
        )

    result = ValidationResult(metrics={"narration_path": str(narration_path)})
    text = narration_path.read_text(encoding="utf-8")
    size = len(text.encode("utf-8"))
    result.metrics["size_bytes"] = size
    result.metrics["char_count"] = len(text)
    result.metrics["word_count"] = len(text.split())

    if size == 0:
        result.fail("narration is empty")
        raise NarrationValidationError("narration is empty")
    if size < min_size_bytes:
        result.fail(
            f"narration too small: {size} bytes < {min_size_bytes}"
        )

    if has_markdown_artifacts(text):
        result.fail("narration still contains Markdown artifacts")

    if not _WS_RE.search(text.strip()):
        result.fail("narration has no whitespace (single long unbroken line)")

    if not result.ok:
        raise NarrationValidationError("; ".join(result.errors))
    return result