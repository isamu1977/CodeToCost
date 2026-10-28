"""OAuth 2.0 Authentication and client management for YouTube Data API v3."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

# Allow token scope flexibility if Google returns normalized/granted scopes
os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import Resource, build

logger = logging.getLogger("redsun.youtube_auth")

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]

# Path to motor-python directory
MOTOR_PYTHON_DIR = Path(__file__).resolve().parent.parent.parent
CLIENT_SECRET_FILE = Path(os.environ.get("YOUTUBE_CLIENT_SECRET_FILE", MOTOR_PYTHON_DIR / "client_secret.json"))
TOKEN_FILE = Path(os.environ.get("YOUTUBE_TOKEN_FILE", MOTOR_PYTHON_DIR / "token.json"))


def get_credentials(interactive: bool = True) -> Credentials:
    """Load or refresh Google OAuth2 credentials.
    
    If token.json does not exist and interactive is True, launches the local
    OAuth2 browser flow on port 8080 and writes the generated token.json.
    """
    creds: Optional[Credentials] = None

    if TOKEN_FILE.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        except Exception as exc:
            logger.warning("Failed to load existing token.json: %s", exc)
            creds = None

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            logger.info("Refreshing expired YouTube OAuth2 credentials...")
            creds.refresh(Request())
            TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
            logger.info("YouTube OAuth2 credentials refreshed and saved to %s", TOKEN_FILE)
            return creds
        except Exception as exc:
            logger.error("Failed to refresh token: %s", exc)
            creds = None

    if not interactive:
        raise RuntimeError(
            f"YouTube OAuth token is missing or invalid at {TOKEN_FILE}. "
            "Please run 'python scripts/authenticate_youtube.py' in the terminal to authorize."
        )

    if not CLIENT_SECRET_FILE.exists():
        raise FileNotFoundError(
            f"YouTube client_secret.json not found at {CLIENT_SECRET_FILE}. "
            "Please ensure the OAuth client credentials are saved in motor-python/."
        )

    logger.info("Starting local server OAuth2 authorization flow...")
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET_FILE), SCOPES)
    try:
        creds = flow.run_local_server(port=8080, prompt="consent")
    except OSError as err:
        logger.warning("Port 8080 is unavailable (%s). Falling back to dynamic port...", err)
        creds = flow.run_local_server(port=0, prompt="consent")

    TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    logger.info("YouTube OAuth2 token successfully generated and written to %s", TOKEN_FILE)
    return creds


def get_youtube_client(interactive: bool = False) -> Resource:
    """Build and return an authorized YouTube Data API v3 Resource client.
    
    Args:
        interactive: If True, allows launching local web browser for OAuth login.
                     Default False for API / server routes to prevent hanging.
    """
    creds = get_credentials(interactive=interactive)
    return build("youtube", "v3", credentials=creds)


def check_auth_status() -> Dict[str, Any]:
    """Inspect YouTube API authentication status and return channel info if authenticated."""
    has_secret = CLIENT_SECRET_FILE.exists()
    has_token = TOKEN_FILE.exists()

    if not has_token:
        return {
            "authenticated": False,
            "channel_title": None,
            "channel_id": None,
            "custom_url": None,
            "has_client_secret": has_secret,
            "has_token": False,
            "error": "token.json not found. Run CLI authentication script (python scripts/authenticate_youtube.py).",
        }

    try:
        # Non-interactive client initialization (refreshes token if expired)
        youtube = get_youtube_client(interactive=False)
        response = youtube.channels().list(mine=True, part="snippet").execute()
        items = response.get("items", [])

        if items:
            channel = items[0]
            snippet = channel.get("snippet", {})
            title = snippet.get("title")
            channel_id = channel.get("id")
            custom_url = snippet.get("customUrl")
            return {
                "authenticated": True,
                "channel_title": title,
                "channel_id": channel_id,
                "custom_url": custom_url,
                "has_client_secret": has_secret,
                "has_token": True,
                "error": None,
            }
        else:
            return {
                "authenticated": False,
                "channel_title": None,
                "channel_id": None,
                "custom_url": None,
                "has_client_secret": has_secret,
                "has_token": True,
                "error": "No YouTube channel associated with this account.",
            }
    except Exception as exc:
        logger.warning("YouTube auth check failed: %s", exc)
        return {
            "authenticated": False,
            "channel_title": None,
            "channel_id": None,
            "custom_url": None,
            "has_client_secret": has_secret,
            "has_token": has_token,
            "error": str(exc),
        }
