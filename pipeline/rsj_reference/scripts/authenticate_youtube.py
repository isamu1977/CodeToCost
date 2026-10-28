#!/usr/bin/env python3
"""CLI utility to authenticate with YouTube Data API v3 and generate token.json."""
import os
import sys
from pathlib import Path

# Allow token scope flexibility if Google returns normalized/granted scopes
os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"

# Ensure motor-python root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from redsun.services.youtube_auth import (
    CLIENT_SECRET_FILE,
    TOKEN_FILE,
    check_auth_status,
    get_youtube_client,
)


def main():
    print("=" * 70)
    print("  RED SUN JAPAN — YOUTUBE OAUTH 2.0 CLI AUTHENTICATION")
    print("=" * 70)
    print(f"Client Secret: {CLIENT_SECRET_FILE}")
    print(f"Token Target:  {TOKEN_FILE}")
    print()

    if not CLIENT_SECRET_FILE.exists():
        print(f"❌ ERROR: client_secret.json was not found at {CLIENT_SECRET_FILE}.")
        print("Please download your OAuth 2.0 Desktop Client credentials from Google Cloud Console")
        print("and place it at channel-redsunjapan/motor-python/client_secret.json.")
        sys.exit(1)

    print("Initiating OAuth 2.0 authorization...")
    print("A browser window will open automatically on http://localhost:8080 (or dynamic free port if 8080 is busy).")
    print("Log in with your Red Sun Japan YouTube channel Google account and grant permissions.\n")

    try:
        # Interactive mode opens local server on port 8080 and launches browser
        youtube = get_youtube_client(interactive=True)
        status = check_auth_status()

        if status.get("authenticated"):
            print("\n" + "=" * 70)
            print("  ✅ AUTHENTICATION SUCCESSFUL!")
            print("=" * 70)
            print(f"  Channel Title: {status.get('channel_title')}")
            print(f"  Channel ID:    {status.get('channel_id')}")
            if status.get("custom_url"):
                print(f"  Custom URL:    {status.get('custom_url')}")
            print(f"  Token File:    {TOKEN_FILE}")
            print("=" * 70)
            print("You can now upload videos and shorts directly from the Studio Pipeline dashboard.\n")
        else:
            print("\n❌ Warning: Token was generated, but channel details could not be retrieved:")
            print(f"   {status.get('error')}")
            sys.exit(1)

    except Exception as exc:
        print(f"\n❌ Authentication failed: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
