"""Pick background music for a video project.

Phase 4 helper: read music_library/metadata.json and choose a track
for a given mood that matches the project's target duration.

Selection rules:
1. Filter by mood (one of: contemplative, dramatic, mystery, triumphant,
   ambient, piano).
2. Filter by duration: track length should be at least the project's
   audio duration (so the music doesn't cut out mid-sentence). If
   no track is long enough, pick the longest available and note that
   the music will loop or fade.
3. **Rotation:** recently-used tracks are deprioritized to maximize
   variety across consecutive videos. History is persisted in
   ``music_library/bgm_history.json``.
4. Sort by "closeness" to project duration (smallest gap first), then
   by file size (smaller first = faster to load).

The chosen track is returned as a dict with the metadata + the resolved
absolute path. The caller (Remotion or the orchestrator) decides what to
do with fade-in / loop / volume ducking.
"""
from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).parent
MUSIC_DIR = ROOT / "music_library"
METADATA_PATH = MUSIC_DIR / "metadata.json"
HISTORY_PATH = MUSIC_DIR / "bgm_history.json"

# How many recent tracks to deprioritize (not ban — just push to bottom).
ROTATION_WINDOW = 15

# Available moods
VALID_MOODS = {
    "contemplative",
    "dramatic",
    "mystery",
    "triumphant",
    "ambient",
    "piano",
}

# Aliases — project descriptions may not match the mood name exactly
MOOD_ALIASES = {
    "snow": "contemplative",
    "quiet": "contemplative",
    "calm": "contemplative",
    "mountain": "contemplative",
    "winter": "contemplative",
    "battle": "dramatic",
    "war": "dramatic",
    "samurai": "dramatic",
    "fight": "dramatic",
    "yokai": "mystery",
    "ghost": "mystery",
    "supernatural": "mystery",
    "kaidan": "mystery",
    "horror": "mystery",
    "terror": "mystery",
    "creepy": "mystery",
    "dark": "mystery",
    "ending": "triumphant",
    "resolution": "triumphant",
    "victory": "triumphant",
    "opening": "triumphant",
    "background": "ambient",
    "underscore": "ambient",
    "soft": "piano",
    "melancholy": "piano",
}


@dataclass
class BGMChoice:
    """A track chosen for a project's background music."""

    filename: str           # path relative to music_library/
    absolute_path: str      # path on disk
    duration_sec: float
    size_kb: float
    mood: str
    codec: str | None
    license: str
    source_url: str | None

    def __repr__(self) -> str:
        return (
            f"<BGMChoice {self.mood:14s} {self.duration_sec:.0f}s "
            f"{self.size_kb/1024:.1f}MB  {Path(self.filename).name}>"
        )


# ─────────────────────────────────────────────────────────────────────────────
# History — track recently used filenames to rotate
# ─────────────────────────────────────────────────────────────────────────────

def _load_history() -> list[str]:
    """Load list of recently-used BGM filenames (most recent last)."""
    if not HISTORY_PATH.exists():
        return []
    try:
        data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        # Expect {"recent": ["file1.mp3", "file2.mp3", ...]}
        return list(data.get("recent", []))
    except Exception:
        return []


def _save_history(filename: str) -> None:
    """Append a filename to the rotation history, keeping last ROTATION_WINDOW."""
    history = _load_history()
    # Remove if already present (move to end)
    history = [h for h in history if h != filename]
    history.append(filename)
    # Keep only the last ROTATION_WINDOW entries
    history = history[-ROTATION_WINDOW:]
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_PATH.write_text(
        json.dumps({"recent": history}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _load_metadata() -> dict:
    if not METADATA_PATH.exists():
        raise FileNotFoundError(
            f"{METADATA_PATH} not found. Run curate_music.py + postprocess_music.py first."
        )
    with METADATA_PATH.open() as f:
        return json.load(f)


def resolve_mood(mood_or_alias: str) -> str:
    """Map a free-form mood description to a known mood."""
    s = mood_or_alias.strip().lower()
    if s in VALID_MOODS:
        return s
    if s in MOOD_ALIASES:
        return MOOD_ALIASES[s]
    # Default: contemplative (safe choice)
    return "contemplative"


def pick_bgm_by_mood(
    mood: str,
    *,
    target_duration_sec: float | None = None,
    seed: int | None = None,
) -> BGMChoice:
    """Pick a BGM track for the given mood, with rotation to avoid repeats.

    Args:
        mood: One of the valid moods, or an alias (snow, battle, yokai, ...).
        target_duration_sec: Optional. The video's audio length in seconds.
            If provided, prefer tracks at least this long; among those, pick
            the one closest in duration.
        seed: Optional. Set for reproducible picks.

    Returns:
        BGMChoice with full metadata and absolute path.

    Raises:
        FileNotFoundError: if metadata.json is missing.
        ValueError: if no tracks available for the resolved mood.
    """
    resolved = resolve_mood(mood)
    metadata = _load_metadata()
    recent = _load_history()

    candidates = [info for info in metadata.values() if info.get("mood") == resolved]
    if not candidates:
        raise ValueError(
            f"No tracks available for mood {mood!r} (resolved={resolved!r}). "
            f"Valid moods: {sorted(VALID_MOODS)}"
        )

    # Filter: only tracks with known duration. Postprocessing should set this.
    candidates = [c for c in candidates if c.get("duration_sec") is not None]
    if not candidates:
        raise ValueError(
            f"No tracks with known duration for mood {resolved!r}. "
            f"Run postprocess_music.py to refresh durations via ffprobe."
        )

    # Rank with rotation penalty
    def _is_recent(c: dict) -> bool:
        fname = c.get("filename", "")
        return fname in recent

    if target_duration_sec is not None:
        def score(c: dict) -> tuple[int, float, float]:
            dur = c["duration_sec"]
            recent_penalty = 1000 if _is_recent(c) else 0
            if dur >= target_duration_sec:
                return (0 + recent_penalty, abs(dur - target_duration_sec * 1.2), c.get("size_kb", 0))
            return (1 + recent_penalty, target_duration_sec - dur, c.get("size_kb", 0))
        candidates.sort(key=score)
    else:
        def score_no_target(c: dict) -> tuple[int, float]:
            recent_penalty = 1000 if _is_recent(c) else 0
            return (recent_penalty, c.get("size_kb", 1e9))
        candidates.sort(key=score_no_target)

    if seed is not None:
        rng = random.Random(seed)
        # Among the top 5 candidates (after rotation sorting), pick one at random
        top = candidates[:5]
        chosen = rng.choice(top)
    else:
        chosen = candidates[0]

    # Record in history
    _save_history(chosen["filename"])

    return BGMChoice(
        filename=chosen["filename"],
        absolute_path=str((MUSIC_DIR / chosen["filename"]).resolve()),
        duration_sec=chosen["duration_sec"],
        size_kb=chosen.get("size_kb", 0),
        mood=chosen["mood"],
        codec=chosen.get("codec"),
        license=chosen.get("license", "Unknown"),
        source_url=chosen.get("source_url"),
    )


def list_moods() -> dict[str, int]:
    """Return {mood: count} for all moods in the library."""
    metadata = _load_metadata()
    by_mood: dict[str, int] = {}
    for info in metadata.values():
        m = info.get("mood", "unknown")
        by_mood[m] = by_mood.get(m, 0) + 1
    return by_mood


def reset_history() -> None:
    """Clear the BGM rotation history (useful for testing)."""
    if HISTORY_PATH.exists():
        HISTORY_PATH.unlink()
    print("BGM history reset.")


# ─────────────────────────────────────────────────────────────────────────────
# Smoke test
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=== Moods available ===")
    moods = list_moods()
    for mood, count in sorted(moods.items()):
        print(f"  {mood:14s}: {count} tracks")

    print("\n=== Rotation test: 5 consecutive picks (mystery, target=600s) ===")
    reset_history()
    for i in range(5):
        bgm = pick_bgm_by_mood("mystery", target_duration_sec=600)
        print(f"  Pick {i+1}: {Path(bgm.filename).name} ({bgm.duration_sec:.0f}s)")

    print("\n=== History after 5 picks ===")
    h = _load_history()
    for entry in h:
        print(f"  {entry}")

    print("\n=== Test: alias 'horror' -> mystery ===")
    bgm = pick_bgm_by_mood("horror", target_duration_sec=600)
    print(f"  {bgm}")
    print(f"  exists: {Path(bgm.absolute_path).exists()}")
