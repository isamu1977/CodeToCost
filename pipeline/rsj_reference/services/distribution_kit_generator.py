"""Proxy export for DistributionKitGenerator."""
from redsun.services.distribution_kit_generator import (
    DistributionKitGenerator,
    generate_distribution_kit,
    _build_default_instagram_kit,
    format_instagram_posts_markdown,
    _enrich_shorts_kit,
    format_youtube_shorts_text,
)

__all__ = [
    "DistributionKitGenerator",
    "generate_distribution_kit",
    "_build_default_instagram_kit",
    "format_instagram_posts_markdown",
    "_enrich_shorts_kit",
    "format_youtube_shorts_text",
]
