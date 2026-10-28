"""Proxy export for youtube_auth service."""
import os

# Allow token scope flexibility if Google returns normalized/granted scopes
os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"

from redsun.services.youtube_auth import (
    CLIENT_SECRET_FILE,
    TOKEN_FILE,
    SCOPES,
    get_credentials,
    get_youtube_client,
    check_auth_status,
)

__all__ = [
    "CLIENT_SECRET_FILE",
    "TOKEN_FILE",
    "SCOPES",
    "get_credentials",
    "get_youtube_client",
    "check_auth_status",
]
