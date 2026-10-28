"""Proxy export for compilation_builder service."""
from redsun.services.compilation_builder import (
    get_eligible_compilation_projects,
    calculate_compilation_timeline,
    generate_compilation_metadata,
    build_composite_thumbnail,
    stitch_compilation_ffmpeg,
    build_compilation_async,
    get_compilation_status,
    list_compilations,
    upload_compilation_to_youtube,
)

__all__ = [
    "get_eligible_compilation_projects",
    "calculate_compilation_timeline",
    "generate_compilation_metadata",
    "build_composite_thumbnail",
    "stitch_compilation_ffmpeg",
    "build_compilation_async",
    "get_compilation_status",
    "list_compilations",
    "upload_compilation_to_youtube",
]
