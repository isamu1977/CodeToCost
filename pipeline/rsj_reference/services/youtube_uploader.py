"""Proxy export for youtube_uploader service."""
from redsun.services.youtube_uploader import (
    upload_project_master_video,
    upload_project_short,
)

__all__ = [
    "upload_project_master_video",
    "upload_project_short",
]
