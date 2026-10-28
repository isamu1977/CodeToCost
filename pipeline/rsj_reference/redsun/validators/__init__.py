"""ValidationResult — common shape for all pipeline validators.

Each validator returns a `ValidationResult` with `ok`, `errors`,
`warnings`, and `metrics`. Validators may raise on failure, but
also return a non-`ok` result so callers can choose between
strict (raise) and lenient (collect) modes.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ValidationResult:
    ok: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    def fail(self, message: str) -> "ValidationResult":
        self.ok = False
        self.errors.append(message)
        return self

    def warn(self, message: str) -> "ValidationResult":
        self.warnings.append(message)
        return self

    def merge(self, other: "ValidationResult") -> "ValidationResult":
        if not other.ok:
            self.ok = False
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)
        self.metrics.update(other.metrics)
        return self


# Re-exports for `from redsun.validators import ...` convenience.
from .audio import validate_audio, validate_transcript  # noqa: E402
from .images import validate_images  # noqa: E402
from .manifest import validate_render_manifest  # noqa: E402
from .narration import validate_narration  # noqa: E402
from .video import validate_final_video, validate_main_video  # noqa: E402
from .visual_plan import validate_visual_plan  # noqa: E402

__all__ = [
    "ValidationResult",
    "validate_audio",
    "validate_transcript",
    "validate_narration",
    "validate_render_manifest",
    "validate_visual_plan",
    "validate_images",
    "validate_main_video",
    "validate_final_video",
]