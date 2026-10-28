"""Proxy export for ThumbnailGenerator and ThumbnailService."""
from redsun.services.thumbnail_generator import (
    ThumbnailGenerator,
    ThumbnailService,
    derive_headline_copy,
)

__all__ = [
    "ThumbnailGenerator",
    "ThumbnailService",
    "derive_headline_copy",
]
