"""Pinterest API v5 automated pin uploader for Red Sun Japan distribution kits."""
from __future__ import annotations

import base64
import json
import logging
import mimetypes
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv
from fastapi import HTTPException

# Ensure environment variables are loaded
load_dotenv()

from redsun import workspace
from redsun.workspace import ProjectWorkspace, RUNTIME_ROOT, resolve_project_dir

logger = logging.getLogger("redsun.pinterest_uploader")

PINTEREST_API_BASE = "https://api.pinterest.com/v5"


def get_pinterest_token() -> Optional[str]:
    """Retrieve Pinterest API v5 access token from environment."""
    return os.environ.get("PINTEREST_ACCESS_TOKEN", "").strip() or None


def _get_headers() -> Dict[str, str]:
    """Construct standard request headers for Pinterest API v5."""
    token = get_pinterest_token()
    if not token:
        raise ValueError("PINTEREST_ACCESS_TOKEN is not set in environment or .env file.")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def check_pinterest_status() -> Dict[str, Any]:
    """Check connectivity and token validity with Pinterest API v5.

    Returns:
        Dict with connected (bool), username, business_name, boards_count, boards list, and error.
    """
    token = get_pinterest_token()
    if not token:
        return {
            "connected": False,
            "username": None,
            "business_name": None,
            "boards_count": 0,
            "boards": [],
            "error": "PINTEREST_ACCESS_TOKEN is not configured in .env",
        }

    try:
        headers = _get_headers()
        # 1. Fetch user account information
        user_res = requests.get(f"{PINTEREST_API_BASE}/user_account", headers=headers, timeout=10)
        if user_res.status_code != 200:
            error_msg = f"Pinterest API HTTP {user_res.status_code}: {user_res.text}"
            logger.warning("Pinterest user_account check failed: %s", error_msg)
            return {
                "connected": False,
                "username": None,
                "business_name": None,
                "boards_count": 0,
                "boards": [],
                "error": error_msg,
            }

        user_data = user_res.json()
        username = user_data.get("username")
        business_name = user_data.get("business_name")

        # 2. Fetch boards list
        boards_res = requests.get(f"{PINTEREST_API_BASE}/boards", headers=headers, timeout=10)
        boards_items: List[Dict[str, Any]] = []
        if boards_res.status_code == 200:
            boards_items = boards_res.json().get("items", [])

        board_summaries = [
            {"id": b.get("id"), "name": b.get("name"), "pin_count": b.get("pin_count", 0)}
            for b in boards_items
        ]

        return {
            "connected": True,
            "username": username or "redsunjapan",
            "business_name": business_name or "Red Sun Japan",
            "boards_count": len(board_summaries),
            "boards": board_summaries,
            "error": None,
        }
    except Exception as exc:
        logger.exception("Error checking Pinterest API status: %s", exc)
        return {
            "connected": False,
            "username": None,
            "business_name": None,
            "boards_count": 0,
            "boards": [],
            "error": str(exc),
        }


def get_or_map_boards() -> Dict[str, Any]:
    """Retrieve boards from Pinterest account and map brand categories.

    Maps:
      - 'Kaidan': Dark folklore, kaidan, ghost stories
      - 'Yokai': Yōkai, monsters, supernatural spirits
      - 'Samurai': Samurai, Edo period chronicles
      - 'fallback': First available board if no keyword match

    Returns:
        Dict with 'mapped': dict of category -> board_id, 'all_boards': list of board dicts, 'default_board_id': str
    """
    headers = _get_headers()
    res = requests.get(f"{PINTEREST_API_BASE}/boards", headers=headers, timeout=10)
    if res.status_code != 200:
        logger.error(f"[Pinterest] Erro ao buscar boards (HTTP {res.status_code}): {res.text}")
        raise HTTPException(status_code=res.status_code, detail=f"Pinterest API Error: {res.text}")

    items = res.json().get("items", [])
    if not items:
        raise HTTPException(status_code=404, detail="No Pinterest boards found for account @redsunjapan. Create at least one board on Pinterest.")

    mapped: Dict[str, Optional[str]] = {
        "Kaidan": None,
        "Yokai": None,
        "Samurai": None,
    }

    # Keywords for matching
    kaidan_kw = ("kaidan", "folklore", "ghost", "dark", "horror", "怪談")
    yokai_kw = ("yōkai", "yokai", "monster", "spirit", "creature", "妖怪")
    samurai_kw = ("samurai", "edo", "ronin", "warrior", "history", "feudal", "侍")

    for board in items:
        b_name = (board.get("name") or "").lower()
        b_desc = (board.get("description") or "").lower()
        b_id = board.get("id")

        if not mapped["Kaidan"] and any(kw in b_name or kw in b_desc for kw in kaidan_kw):
            mapped["Kaidan"] = b_id
        if not mapped["Yokai"] and any(kw in b_name or kw in b_desc for kw in yokai_kw):
            mapped["Yokai"] = b_id
        if not mapped["Samurai"] and any(kw in b_name or kw in b_desc for kw in samurai_kw):
            mapped["Samurai"] = b_id

    # Fallback to the first board
    first_board_id = items[0]["id"]
    default_board_id = mapped["Kaidan"] or mapped["Yokai"] or mapped["Samurai"] or first_board_id

    return {
        "mapped": mapped,
        "default_board_id": default_board_id,
        "all_boards": items,
    }


def resolve_board_id_for_pin(pin_data: Dict[str, Any], project_slug: str, board_mapping: Dict[str, Any]) -> str:
    """Determine the most accurate board_id for a given pin or project."""
    title = (pin_data.get("pin_title") or "").lower()
    desc = (pin_data.get("pin_description") or "").lower()
    combined = f"{title} {desc} {project_slug.lower()}"

    mapped = board_mapping.get("mapped", {})
    default_id = board_mapping.get("default_board_id")

    # Match in priority
    if any(k in combined for k in ("samurai", "ronin", "edo", "katana", "feudal")) and mapped.get("Samurai"):
        return mapped["Samurai"]
    if any(k in combined for k in ("yōkai", "yokai", "kappa", "monster", "demon", "spirit")) and mapped.get("Yokai"):
        return mapped["Yokai"]
    if any(k in combined for k in ("kaidan", "ghost", "haunt", "specter", "curse", "folklore")) and mapped.get("Kaidan"):
        return mapped["Kaidan"]

    return default_id


def _find_pin_image(dist_dir: Path, pin: Dict[str, Any], index: int) -> Path:
    """Find the physical image file on disk for a given pin index."""
    pins_media_dir = dist_dir / "pins_media"

    # 1. Check specified media_file in pins_media
    media_file = pin.get("media_file")
    if media_file:
        cand = pins_media_dir / Path(media_file).name
        if cand.exists() and cand.stat().st_size > 1000:
            return cand

    # 2. Check standard names pin_01.jpeg, pin_01.jpg, pin_01.png
    candidates = [
        pins_media_dir / f"pin_{index:02d}.jpeg",
        pins_media_dir / f"pin_{index:02d}.jpg",
        pins_media_dir / f"pin_{index:02d}.png",
        dist_dir / f"pin_{index:02d}.jpeg",
        dist_dir / f"pin_{index:02d}.jpg",
    ]
    for cand in candidates:
        if cand.exists() and cand.stat().st_size > 1000:
            return cand

    # 3. Check media_path if valid
    raw_path = pin.get("media_path")
    if raw_path:
        cand = Path(raw_path)
        if cand.exists() and cand.stat().st_size > 1000:
            return cand

    raise FileNotFoundError(
        f"Pin media image not found for pin #{index} in {pins_media_dir}. "
        f"Expected pin_{index:02d}.jpeg or valid media_file."
    )


def publish_project_pins(slug: str) -> Dict[str, Any]:
    """Publish the 4 pins of a project distribution kit directly to Pinterest via API v5.

    Args:
        slug: The technical slug of the project.

    Returns:
        Dict with success status, list of published pins with IDs and URLs.
    """
    token = get_pinterest_token()
    if not token:
        raise HTTPException(status_code=400, detail="PINTEREST_ACCESS_TOKEN is not configured in .env.")

    # 1. Resolução de Diretório do Projeto
    project_dir = workspace.resolve_project_dir(slug)
    if not project_dir:
        raise HTTPException(status_code=404, detail=f"Diretório do projeto {slug} não encontrado")
    dist_dir = project_dir / "distribution"
    if not dist_dir.exists():
        dist_dir = project_dir

    kit_path = dist_dir / "social_distribution_kit.json"
    if not kit_path.exists():
        raise HTTPException(status_code=404, detail=f"Distribution kit not found at {kit_path}. Run Node 4 distribution step first.")

    with open(kit_path, "r", encoding="utf-8") as f:
        kit_data = json.load(f)

    pinterest_pins = kit_data.get("pinterest", [])
    if not pinterest_pins:
        raise HTTPException(status_code=400, detail=f"No Pinterest pin data found in {kit_path}.")

    # 2. Map target Pinterest boards
    board_mapping = get_or_map_boards()
    headers = _get_headers()

    published_results: List[Dict[str, Any]] = []

    # 3. Iterate through up to 4 pins
    for idx, pin in enumerate(pinterest_pins[:4], start=1):
        image_path = _find_pin_image(dist_dir, pin, idx)

        # Determine mime type
        mime_type, _ = mimetypes.guess_type(str(image_path))
        if not mime_type or not mime_type.startswith("image/"):
            mime_type = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"

        with open(image_path, "rb") as f:
            raw_base64 = base64.b64encode(f.read()).decode("utf-8")
        # Se por acaso contiver cabeçalho data:image..., remova:
        if "," in raw_base64:
            raw_base64 = raw_base64.split(",")[1]

        board_id = resolve_board_id_for_pin(pin, slug, board_mapping)
        title = (pin.get("pin_title") or f"Red Sun Japan: {slug}")[:100].strip()
        description = (pin.get("pin_description") or f"Japanese folklore chronicle: {slug}")[:800].strip()
        destination_link = (pin.get("destination_link") or f"https://redsunjapan.com/posts/{slug}").strip()

        payload = {
            "board_id": str(board_id),
            "title": title,
            "description": description,
            "link": destination_link,
            "media_source": {
                "source_type": "image_base64",
                "content_type": mime_type,
                "data": raw_base64,
            },
        }

        logger.info(f"[Pinterest] Enviando Pin #{idx} para Board ID {board_id}...")

        res = requests.post(f"{PINTEREST_API_BASE}/pins", headers=headers, json=payload, timeout=30)
        if res.status_code not in (200, 201):
            logger.error("Pinterest API pin upload failed (HTTP %d): %s", res.status_code, res.text)
            raise HTTPException(
                status_code=res.status_code,
                detail=f"Pinterest API Error: {res.text}",
            )

        resp_data = res.json()
        pin_id = str(resp_data.get("id"))
        pin_url = f"https://www.pinterest.com/pin/{pin_id}/"

        # Update metadata in memory
        pin["published_id"] = pin_id
        pin["published_url"] = pin_url
        pin["published_board_id"] = board_id
        pin["published_at"] = datetime.utcnow().isoformat()

        published_results.append({
            "pin_index": idx,
            "id": pin_id,
            "url": pin_url,
            "board_id": board_id,
            "title": title,
            "destination_link": destination_link,
            "image_file": image_path.name,
        })

    # 4. Save updated distribution kit with audit records
    try:
        with open(kit_path, "w", encoding="utf-8") as f:
            json.dump(kit_data, f, indent=2, ensure_ascii=False)
        logger.info("Saved updated social_distribution_kit.json with Pinterest publish records.")
    except Exception as save_err:
        logger.warning("Could not persist published pins into %s: %s", kit_path, save_err)

    return {
        "success": True,
        "slug": slug,
        "published_pins": published_results,
        "total_published": len(published_results),
    }
