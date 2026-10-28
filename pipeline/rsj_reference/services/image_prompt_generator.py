"""Proxy forwarder for image_prompt_generator."""
from redsun.services.image_prompt_generator import (
    calculate_dynamic_scene_count,
    slice_narrative_into_beats,
    slice_narrative_into_20_beats,
    generate_narrative_scene_prompts,
    mark_archival_bridge_beat,
    ARCHIVAL_BRIDGE_KEYWORDS_PATTERN,
)

__all__ = [
    "calculate_dynamic_scene_count",
    "slice_narrative_into_beats",
    "slice_narrative_into_20_beats",
    "generate_narrative_scene_prompts",
    "mark_archival_bridge_beat",
    "ARCHIVAL_BRIDGE_KEYWORDS_PATTERN",
]
