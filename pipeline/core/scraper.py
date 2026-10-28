"""Public-domain image scraper for the Red Sun Japan pipeline.

Phase 1 scope:

- Wikimedia Commons only (Met Museum / Rijksmuseum land in Phase 2 or 3).
- Persistent library under ``ASSETS_LIBRARY_DIR/{theme}/...``.
- Indexed by ``AssetImage`` rows in SQLite; second-call hits are no-ops
  (no duplicate downloads, no duplicate DB rows).
- Returns a list of ``AssetImage`` objects so callers can attach them to a
  project via the ``assets`` relationship.

Notes on etiquette:

- Wikimedia's API blocks anonymous / generic User-Agents (``429 Too Many
  Requests``). We identify ourselves with a project-specific UA. Replace the
  contact email with a real one before any non-trivial volume.

Notes on Phase 2 / Phase 3 (TODOs, not in this commit):

- Add ``MetMuseumClient`` and ``RijksmuseumClient`` with the same
  ``fetch(query, theme, limit) -> list[AssetImage]`` signature.
- Add a fallback to ComfyUI when Wikimedia returns fewer than ``limit`` hits.
- Read ``theme`` from the project, not from the ``query`` string — this keeps
  the index useful for print-on-demand browsing.
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Iterable

import requests
from sqlalchemy.orm import Session

from database import AssetImage, AssetLicense, SessionLocal

# Per Wikimedia's API etiquette policy: identify yourself in the UA string.
# The default below is suitable for development; replace before scaling up.
WIKIMEDIA_UA = os.environ.get(
    "REDSUN_WIKIMEDIA_UA",
    "RedSunJapanBot/0.1 (educational video generator; contact via repo issues)",
)
WIKIMEDIA_API = "https://commons.wikimedia.org/w/api.php"

DEFAULT_ASSETS_DIR = os.environ.get(
    "REDSUN_ASSETS_LIBRARY_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets_library"),
)


# ─────────────────────────────────────────────────────────────────────────────
# Low-level Wikimedia fetch
# ─────────────────────────────────────────────────────────────────────────────
def _wikimedia_search(query: str, limit: int) -> list[dict]:
    """Return a list of ``{url, title, mime}`` dicts from Commons search."""
    params = {
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": f"{query} filetype:bitmap",
        "gsrnamespace": 6,           # File namespace
        "gsrlimit": limit,
        "prop": "imageinfo",
        "iiprop": "url|mime",
    }
    headers = {"User-Agent": WIKIMEDIA_UA}
    resp = requests.get(WIKIMEDIA_API, params=params, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    results: list[dict] = []
    for page_info in data.get("query", {}).get("pages", {}).values():
        for info in page_info.get("imageinfo", []):
            url = info.get("url")
            mime = info.get("mime", "")
            if url and mime.startswith("image/"):
                results.append({
                    "url": url,
                    "title": page_info.get("title", ""),
                    "mime": mime,
                })
    return results


def _slug_for_filename(title: str) -> str:
    """Turn ``File:Ukiyo-e samurai print.jpg`` into ``ukiyo_e_samurai_print``."""
    name = title.split(":", 1)[-1].strip()
    name = re.sub(r"\.[a-zA-Z0-9]+$", "", name)  # strip extension
    name = re.sub(r"[^a-zA-Z0-9_-]+", "_", name).strip("_")
    return name or "asset"


def _safe_extension(mime_or_url: str) -> str:
    mime_or_url = mime_or_url.lower()
    if "jpeg" in mime_or_url or ".jpg" in mime_or_url:
        return ".jpg"
    if "png" in mime_or_url:
        return ".png"
    if "webp" in mime_or_url:
        return ".webp"
    if "svg" in mime_or_url:
        return ".svg"
    return ".jpg"


# ─────────────────────────────────────────────────────────────────────────────
# High-level: fetch + persist
# ─────────────────────────────────────────────────────────────────────────────
def _file_already_indexed(db: Session, source_url: str) -> AssetImage | None:
    """Avoid duplicate downloads / DB rows by canonical source URL."""
    return db.query(AssetImage).filter(AssetImage.source_url == source_url).first()


def fetch_assets(
    query: str,
    theme: str,
    limit: int = 5,
    library_dir: str | None = None,
    db: Session | None = None,
) -> list[AssetImage]:
    """Fetch up to ``limit`` images for ``query`` into the persistent library.

    Args:
        query: free-text search (e.g. ``"ukiyo-e samurai"``).
        theme: library folder name (e.g. ``"samurai"``). Used both for the
            on-disk layout and the ``AssetImage.theme`` index.
        limit: maximum *new* images to download (existing assets are not
            re-downloaded, but they are returned).
        library_dir: override the library root (defaults to
            ``REDSUN_ASSETS_LIBRARY_DIR`` or ``./assets_library``).
        db: open SQLAlchemy session. A new one is opened if None; if one is
            passed in, the caller owns its lifecycle.

    Returns:
        list of ``AssetImage`` rows — both newly created and pre-existing
        matches for ``query`` are returned.
    """
    library_root = Path(library_dir or DEFAULT_ASSETS_DIR)
    theme_dir = library_root / theme
    theme_dir.mkdir(parents=True, exist_ok=True)

    own_session = db is None
    if own_session:
        db = SessionLocal()
    try:
        search_hits = _wikimedia_search(query, limit=limit * 2)   # ask for more
        # because some hits may already be in the library — we only want
        # ``limit`` *new* ones.

        registered: list[AssetImage] = []
        for hit in search_hits:
            existing = _file_already_indexed(db, hit["url"])
            if existing:
                registered.append(existing)
                continue

            if len([r for r in registered if r.id is None or r not in registered]) >= limit:
                break

            ext = _safe_extension(hit["mime"] or hit["url"])
            filename = f"{_slug_for_filename(hit['title'])}{ext}"
            target_path = theme_dir / filename

            # Download with Wikimedia-friendly pacing.
            try:
                resp = requests.get(
                    hit["url"],
                    headers={"User-Agent": WIKIMEDIA_UA},
                    stream=True,
                    timeout=60,
                )
                resp.raise_for_status()
                with open(target_path, "wb") as fh:
                    for chunk in resp.iter_content(chunk_size=8192):
                        if chunk:
                            fh.write(chunk)
            except requests.RequestException as exc:
                # Skip this hit; try the next.
                print(f"[scraper] download failed for {hit['url']}: {exc}")
                continue
            time.sleep(1.5)   # polite pacing

            # Get image dimensions cheaply (small overhead).
            width = height = None
            try:
                from PIL import Image  # Pillow is part of the cluster deps
                with Image.open(target_path) as im:
                    width, height = im.size
            except Exception:
                pass

            asset = AssetImage(
                project_id=None,           # attached later when project is known
                theme=theme,
                relative_path=str(target_path.relative_to(library_root)),
                source_name="Wikimedia Commons",
                source_url=hit["url"],
                license=AssetLicense.PD,   # Commons policy: assume PD for now;
                                           # Phase 3 will tighten this.
                description=hit["title"],
                width=width,
                height=height,
            )
            db.add(asset)
            db.flush()                    # populate id without committing yet
            registered.append(asset)

        db.commit()
        return registered
    finally:
        if own_session:
            db.close()


def list_assets_by_theme(theme: str, db: Session | None = None) -> list[AssetImage]:
    """Convenience lookup for the operator UI (Phase 2 onward)."""
    own_session = db is None
    if own_session:
        db = SessionLocal()
    try:
        return (
            db.query(AssetImage)
            .filter(AssetImage.theme == theme)
            .order_by(AssetImage.downloaded_at.desc())
            .all()
        )
    finally:
        if own_session:
            db.close()


def mark_merch_candidate(asset_id: int, merit_score: int | None = None) -> None:
    """Operator-side helper: mark an image as a merch candidate."""
    with SessionLocal() as db:
        asset = db.query(AssetImage).filter(AssetImage.id == asset_id).first()
        if not asset:
            raise ValueError(f"AssetImage #{asset_id} not found")
        asset.merch_candidate = 1
        if merit_score is not None:
            asset.merit_score = merit_score
        db.commit()


if __name__ == "__main__":
    # Smoke entrypoint for Phase 1: just see if Wikimedia answers.
    hits = fetch_assets("ukiyo-e samurai", theme="samurai", limit=3)
    print(f"fetched/persisted {len(hits)} assets:")
    for h in hits:
        print(f"  - {h.relative_path}  (Wikimedia: {h.source_url})")
