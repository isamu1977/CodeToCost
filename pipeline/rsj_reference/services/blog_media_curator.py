"""Proxy export for BlogMediaCurator and editorial formatting functions."""
from redsun.services.blog_media_curator import (
    BlogMediaCurator,
    format_editorial_caption,
    format_editorial_alt,
)

__all__ = [
    "BlogMediaCurator",
    "format_editorial_caption",
    "format_editorial_alt",
]
