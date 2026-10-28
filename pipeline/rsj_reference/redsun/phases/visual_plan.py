"""VisualPlan models — `NarrativeScene` and `VisualBeat`.

`NarrativeScene` represents a logical chunk of narration
(paragraph-level). A long scene can hold multiple
`VisualBeats`, each with its own image and duration.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class VisualBeat:
    index: int
    narrative_scene_index: int
    text_excerpt: str
    duration_sec: float
    image_path: str = ""

    def __post_init__(self) -> None:
        if self.duration_sec <= 0:
            raise ValueError("VisualBeat.duration_sec must be > 0")


@dataclass
class NarrativeScene:
    index: int
    text: str
    start_sec: float = 0.0
    end_sec: float = 0.0
    visual_beats: list[VisualBeat] = field(default_factory=list)


@dataclass
class VisualPlan:
    target_image_seconds: float
    max_image_seconds: float
    audio_duration_sec: float
    narrative_scenes: list[NarrativeScene] = field(default_factory=list)

    @property
    def visual_beats(self) -> list[VisualBeat]:
        return [b for s in self.narrative_scenes for b in s.visual_beats]

    @property
    def beat_count(self) -> int:
        return len(self.visual_beats)

    @property
    def target_visual_beats(self) -> int:
        if self.target_image_seconds <= 0:
            return 0
        return max(1, math.ceil(self.audio_duration_sec / self.target_image_seconds))


def compute_target_visual_beats(
    audio_duration_sec: float, target_image_seconds: float
) -> int:
    """Pure function used by both the planner and the validator."""
    if target_image_seconds <= 0:
        raise ValueError("target_image_seconds must be > 0")
    if audio_duration_sec <= 0:
        raise ValueError("audio_duration_sec must be > 0")
    return max(1, math.ceil(audio_duration_sec / target_image_seconds))