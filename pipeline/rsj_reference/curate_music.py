"""Curate the Red Sun Japan music library from Wikimedia Commons.

Why Wikimedia (not Pixabay / Incompetech / IMSLP):
- 100% PD or CC0/CC-BY audio is uploaded there for the public domain works
  of composers from the pre-1929 era.
- Direct file URLs from upload.wikimedia.org work with curl and yt-dlp.
- No login, no captcha, no user-agent blocks (Pachelbel Canon in D has 840 hits).
- License is recorded per-file by the uploader, so we can pick PD/CC0 only.

Curated query set (~30 tracks across 6 moods):

  - contemplative: Pachelbel Canon, Satie Gymnopedies, Debussy Reverie
  - dramatic:     Bach Toccata, Beethoven Coriolan, Mussorgsky Night on Bald Mountain
  - mystery:      Debussy Clair de Lune, Satie Gnossiennes, Ravel
  - triumphant:   Handel Water Music, Vivaldi Spring, Bach Brandenburg
  - ambient:      Erik Satie Vexations (long, slow, drones)
  - piano:        Chopin Nocturnes, Beethoven Moonlight

Output layout (gitignored by parent .gitignore):
  music_library/
    <mood>/
      <composer>_<work>.ogg
    metadata.json  -- one entry per file with mood, composer, source_url, license

The metadata is what `pick_bgm_by_mood()` (Phase 5) will read.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

import requests

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent
MUSIC_DIR = ROOT / "music_library"
METADATA_PATH = MUSIC_DIR / "metadata.json"
USER_AGENT = "RedSunJapan-MusicCuration/1.0 (https://redsunjapan.com; contact@redsunjapan.com)"

# Curated query set. Each tuple: (mood, search_query, max_results)
# max_results caps the candidates so we don't flood the API.
QUERIES = [
    # ─── Contemplative (snow, mountains, slow) ────────────────────────
    ("contemplative", "Pachelbel Canon in D", 3),
    ("contemplative", "Satie Gymnopedie", 4),
    ("contemplative", "Debussy Reverie piano", 2),
    # ─── Dramatic (battle, samurai, conflict) ─────────────────────────
    ("dramatic", "Bach Toccata and Fugue D minor", 3),
    ("dramatic", "Beethoven Coriolan Overture", 2),
    ("dramatic", "Mussorgsky Night on Bald Mountain", 2),
    # ─── Mystery (yokai, kaidan, supernatural) ────────────────────────
    ("mystery", "Debussy Clair de Lune", 2),
    ("mystery", "Satie Gnossienne", 3),
    ("mystery", "Ravel Pavane pour une infante defunte", 2),
    # ─── Triumphant (resolution, ending) ──────────────────────────────
    ("triumphant", "Handel Water Music", 3),
    ("triumphant", "Vivaldi Spring Allegro", 2),
    ("triumphant", "Bach Brandenburg Concerto", 2),
    # ─── Ambient (long, slow, drone-like) ─────────────────────────────
    ("ambient", "Satie Vexations", 2),
    ("ambient", "Pachelbel Chaconne", 2),
    # ─── Piano (solo contemplative) ───────────────────────────────────
    ("piano", "Chopin Nocturne", 3),
    ("piano", "Beethoven Moonlight Sonata", 2),
]

# Allowed file extensions (Wikimedia often has both .ogg and .mp3 transcodes)
ALLOWED_EXTS = {".ogg", ".mp3", ".wav", ".flac"}
# Skip files that are too long for BGM (over 8min — too dominant for narration)
MAX_DURATION_SEC = 8 * 60
# Skip files that are too short (under 30s — not useful for BGM)
MIN_DURATION_SEC = 30


def search_wikimedia_audio(query: str, limit: int = 5) -> list[dict]:
    """Search Wikimedia Commons for audio files matching query.

    Returns a list of file metadata dicts, filtered to AUDIO/MUSIC types
    and PD or CC0 / CC-BY licenses.
    """
    api = "https://commons.wikimedia.org/w/api.php"
    params = {
        "action": "query",
        "list": "search",
        "srsearch": f"{query} filetype:audio",
        "srnamespace": 6,  # File namespace
        "format": "json",
        "srlimit": str(limit * 3),  # oversample — we'll filter
    }
    try:
        resp = requests.get(
            api, params=params, headers={"User-Agent": USER_AGENT}, timeout=20
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        print(f"  [WARN] search failed for {query!r}: {exc}", file=sys.stderr)
        return []

    hits = (data.get("query") or {}).get("search") or []
    return [h for h in hits if any(h.get("title", "").lower().endswith(ext) for ext in ALLOWED_EXTS)][:limit]


def fetch_file_metadata(title: str) -> dict | None:
    """Get the direct URL + license + duration for a Commons file.

    Returns None if the file is not fetchable, not an audio file, or has
    a license we don't want.
    """
    api = "https://commons.wikimedia.org/w/api.php"
    params = {
        "action": "query",
        "titles": title,
        "prop": "imageinfo",
        "iiprop": "url|size|mime|extmetadata|metadata",
        "format": "json",
    }
    try:
        resp = requests.get(
            api, params=params, headers={"User-Agent": USER_AGENT}, timeout=20
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        print(f"  [WARN] metadata failed for {title!r}: {exc}", file=sys.stderr)
        return None

    pages = (data.get("query") or {}).get("pages") or {}
    if not pages:
        return None
    page = next(iter(pages.values()))
    imageinfo = (page.get("imageinfo") or [{}])[0]
    if not imageinfo:
        return None

    # Filter: must be audio MIME. Wikimedia returns MIME like 'application/ogg'
    # for OGG Vorbis (yes, weird) and 'audio/mpeg' / 'audio/wav' for others.
    # Accept any mime that starts with 'audio/' OR is 'application/ogg'.
    mime = imageinfo.get("mime", "")
    if not (mime.startswith("audio/") or mime == "application/ogg"):
        return None

    # Filter: license must be PD/CC0/CC-BY. Wikimedia extmetadata is messy —
    # some keys return strings, others return nested dicts, others return
    # floats. Defensive lookup.
    meta = imageinfo.get("extmetadata") or {}
    license_raw = meta.get("LicenseShortName") or {}
    license_short = ""
    if isinstance(license_raw, dict):
        license_short = str(license_raw.get("value", "") or "")
    elif isinstance(license_raw, str):
        license_short = license_raw
    acceptable_licenses = {
        "Public domain",
        "PD",
        "CC0",
        "CC BY",
        "CC BY-SA",
        "CC BY 4.0",
        "CC BY 3.0",
        "CC BY-SA 4.0",
        "CC BY-SA 3.0",
        "GPDL",
        "Art Libre",
    }
    if license_short and license_short not in acceptable_licenses:
        return None

    # Extract duration if present
    metadata = imageinfo.get("metadata") or {}
    duration_sec = None
    for md in metadata:
        if md.get("name") == "duration":
            try:
                # Format is "MM:SS" or "HH:MM:SS"
                parts = md["value"].split(":")
                duration_sec = sum(int(p) * (60 ** i) for i, p in enumerate(reversed(parts)))
            except Exception:
                pass
            break

    # Filter by duration
    if duration_sec is not None:
        if duration_sec < MIN_DURATION_SEC or duration_sec > MAX_DURATION_SEC:
            return None

    return {
        "title": title,
        "url": imageinfo.get("url", ""),
        "size_bytes": imageinfo.get("size", 0),
        "mime": mime,
        "license": license_short or "Unknown",
        "duration_sec": duration_sec,
    }


def download_file(url: str, dest: Path) -> bool:
    """Download with curl + retries. Returns True on success."""
    for attempt in range(1, 4):
        try:
            cmd = [
                "curl", "-sS", "-L",
                "-A", USER_AGENT,
                "-o", str(dest),
                "--max-time", "180",
                "--retry", "2",
                "--retry-delay", "3",
                url,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=200)
            if result.returncode == 0 and dest.exists() and dest.stat().st_size > 0:
                return True
            print(f"  [WARN] attempt {attempt} failed (exit {result.returncode})", file=sys.stderr)
        except Exception as exc:
            print(f"  [WARN] attempt {attempt} raised: {exc}", file=sys.stderr)
        time.sleep(2)
    return False


def safe_filename(title: str) -> str:
    """Convert 'File:Pachelbel - Canon in D major.ogg' to 'Pachelbel_-_Canon_in_D_major.ogg'."""
    name = title.replace("File:", "").strip()
    return name.replace(" ", "_").replace("(", "").replace(")", "").replace(",", "").replace("'", "")


def main():
    print(f"Music library root: {MUSIC_DIR}")
    print(f"Metadata file:     {METADATA_PATH}")
    print(f"Queries to run:    {len(QUERIES)}")
    print()

    # Ensure mood subdirs exist
    for mood in {q[0] for q in QUERIES}:
        (MUSIC_DIR / mood).mkdir(parents=True, exist_ok=True)

    metadata: dict[str, dict] = {}
    if METADATA_PATH.exists():
        with METADATA_PATH.open() as f:
            metadata = json.load(f)
        print(f"Loaded existing metadata: {len(metadata)} entries")

    seen_titles: set[str] = set()
    seen_titles.update(metadata.keys())

    total_queried = 0
    total_downloaded = 0
    total_skipped = 0
    total_failed = 0

    for mood, query, max_results in QUERIES:
        print(f"\n=== {mood:14s} :: {query} (max {max_results}) ===")
        candidates = search_wikimedia_audio(query, limit=max_results + 2)
        if not candidates:
            print(f"  [SKIP] no candidates")
            total_skipped += max_results
            continue

        downloaded_this_query = 0
        for cand in candidates:
            total_queried += 1
            title = cand["title"]
            if title in seen_titles:
                print(f"  [DUP]  {title}")
                total_skipped += 1
                continue

            info = fetch_file_metadata(title)
            if not info:
                print(f"  [SKIP] {title} (no metadata / wrong license)")
                total_skipped += 1
                continue

            if not info["url"]:
                print(f"  [SKIP] {title} (no direct URL)")
                total_skipped += 1
                continue

            fname = safe_filename(title)
            dest = MUSIC_DIR / mood / fname
            print(f"  [GET]  {fname} ({info['duration_sec']}s, {info['license']})")
            ok = download_file(info["url"], dest)
            if ok:
                seen_titles.add(title)
                metadata[title] = {
                    "mood": mood,
                    "query": query,
                    "filename": str(dest.relative_to(MUSIC_DIR)),
                    "source_url": info["url"],
                    "size_bytes": dest.stat().st_size,
                    "duration_sec": info["duration_sec"],
                    "license": info["license"],
                    "mime": info["mime"],
                }
                total_downloaded += 1
                downloaded_this_query += 1
                if downloaded_this_query >= max_results:
                    break
            else:
                total_failed += 1
                if dest.exists():
                    dest.unlink()

            time.sleep(1.5)  # polite pacing per Commons etiquette

    # Save metadata
    with METADATA_PATH.open("w") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*60}")
    print(f"Queried:     {total_queried}")
    print(f"Downloaded:  {total_downloaded}")
    print(f"Skipped:     {total_skipped}  (duplicates, no candidates, license filters)")
    print(f"Failed:      {total_failed}")
    print(f"Total tracks in library: {len(metadata)}")
    print(f"Metadata:    {METADATA_PATH}")


if __name__ == "__main__":
    main()
