"""Phase 3: prepare_narration.

Reads script.md (or variant.script), sanitizes Markdown
deterministically, writes narration.txt, and runs
`validate_narration` as a quality gate.

Outputs:
    workspace.narration_path  — sanitized plain text
    artifact_manifest.narration.{sha256, word_count, char_count}
    checkpoint `prepare_narration.json` (when wired to runner)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .. import defaults
from ..context import ProjectContext
from ..exceptions import (
    NarrationValidationError,
    SanitizationError,
)
from ..narration import sanitize_for_tts
from ..workspace import ProjectWorkspace
from ..validators.narration import validate_narration


logger = logging.getLogger("redsun.prepare_narration")


@dataclass
class NarrationResult:
    path: object  # Path
    sha256: str
    char_count: int
    word_count: int
    sanitization_applied: bool


def prepare_narration(
    context: ProjectContext,
    *,
    script_text: str | None = None,
) -> NarrationResult:
    """Run the prepare_narration phase.

    Order:
      1. resolve input: prefer explicit `script_text`, else
         `context.workspace.script_path`, else
         `context.episode.source_text`.
      2. sanitize via `sanitize_for_tts`.
      3. write `narration.txt`.
      4. validate via `validate_narration`.
      5. record SHA-256 + metrics on the context.
    """
    ws = context.workspace

    # 1. resolve input
    raw: str
    if script_text is not None:
        raw = script_text
    elif ws.script_path.exists():
        raw = ws.script_path.read_text(encoding="utf-8")
    else:
        raw = context.episode.source_text or ""

    if not raw.strip():
        raise NarrationValidationError(
            f"no script text available for {ws.technical_slug}"
        )

    # 2. sanitize
    sanitized = sanitize_for_tts(raw)
    if not sanitized:
        raise SanitizationError(
            f"sanitize_for_tts produced empty output for {ws.technical_slug}"
        )

    # 3. write
    ws.create()
    ws.narration_path.write_text(sanitized, encoding="utf-8")

    # 3b. generate phonetic TTS input text for Kokoro without touching narration.txt
    try:
        from ..services.pronunciation import apply_pronunciation_map
        tts_input_text = apply_pronunciation_map(sanitized, slug=ws.technical_slug)
        tts_input_path = ws.root / "text" / "tts_input.txt"
        tts_input_path.parent.mkdir(parents=True, exist_ok=True)
        tts_input_path.write_text(tts_input_text, encoding="utf-8")
    except Exception as exc:
        logger.warning("Could not generate tts_input.txt in prepare_narration: %s", exc)

    # 3c. trigger Gate 1.5 dispatcher hook (JingJing phonetic & script audit before Kokoro TTS)
    try:
        from ..services.agent_dispatcher import trigger_gate_1_5
        trigger_gate_1_5(ws.technical_slug, working_dir=ws.root)
    except Exception as exc:
        logger.warning("Could not trigger Gate 1.5 agent hook: %s", exc)


    # 4. validate
    result = validate_narration(ws.narration_path)
    if not result.ok:
        raise NarrationValidationError(
            f"narration validation failed: {'; '.join(result.errors)}"
        )

    sha = context.hash_file(ws.narration_path)
    word_count = len(sanitized.split())
    char_count = len(sanitized)

    # 5. record metrics on the context (the artifact manifest writer
    # picks them up from here).
    context.__dict__.setdefault("artifacts", {})
    context.artifacts["narration"] = {
        "path": str(ws.narration_path),
        "sha256": sha,
        "char_count": char_count,
        "word_count": word_count,
        "sanitization_applied": True,
    }

    logger.info(
        "prepare_narration: %d chars, %d words, sha256=%s",
        char_count, word_count, sha[:12],
    )

    return NarrationResult(
        path=ws.narration_path,
        sha256=sha,
        char_count=char_count,
        word_count=word_count,
        sanitization_applied=True,
    )