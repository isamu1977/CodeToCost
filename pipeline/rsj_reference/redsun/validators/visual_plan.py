"""VisualPlan validator — duration-derived density check."""
from __future__ import annotations

from ..exceptions import VisualPlanValidationError
from . import ValidationResult
from ..phases.visual_plan import VisualPlan, compute_target_visual_beats


def validate_visual_plan(
    plan: VisualPlan,
    *,
    min_visual_beats_floor: int = 0,
    overshoot_tolerance: float = 1.5,
) -> ValidationResult:
    """Reject plans that are too sparse or too dense.

    Rules:
    - len(visual_beats) >= max(min_visual_beats_floor, target * 0.5)
    - len(visual_beats) <= target * overshoot_tolerance
    """
    result = ValidationResult(metrics={"beat_count": plan.beat_count})
    target = compute_target_visual_beats(
        plan.audio_duration_sec, plan.target_image_seconds
    )
    result.metrics["target_visual_beats"] = target

    floor = max(min_visual_beats_floor, int(target * 0.5))
    ceil = int(target * overshoot_tolerance)
    result.metrics["floor"] = floor
    result.metrics["ceiling"] = ceil

    if plan.beat_count < floor:
        result.fail(
            f"visual plan too sparse: {plan.beat_count} beats < "
            f"floor {floor} (target≈{target}, audio≈{plan.audio_duration_sec:.0f}s)"
        )
    if plan.beat_count > ceil:
        result.fail(
            f"visual plan too dense: {plan.beat_count} beats > "
            f"ceiling {ceil} (target≈{target})"
        )

    if not result.ok:
        raise VisualPlanValidationError("; ".join(result.errors))
    return result