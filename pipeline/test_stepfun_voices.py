"""CodeToCost — StepFun voice audition (English B2B tech channel).

Synthesizes the SAME technical sample with the English system voices available
to the StepFun StepAudio account, so the team can listen and pick the voice
that best fits the "senior software analyst" persona.

The English system voices (per StepFun docs, GET
``https://api.stepfun.ai/v1/audio/system_voices?model=stepaudio-2.5-tts``):

* ``magnetic-voiced-male``  — deep, analytical, warm (current default)
* ``soft-spoken-gentleman`` — calm, intimate
* ``vibrant-youth``         — approachable, energetic

The phonetic dictionary (``agent_workspace/phonetic_dictionary.json``) is
applied to the sample text before synthesis, exactly like the production path.

Run from the repo root with the project venv::

    pipeline/core/.venv/bin/python pipeline/test_stepfun_voices.py
    pipeline/core/.venv/bin/python pipeline/test_stepfun_voices.py magnetic-voiced-male vibrant-youth

Output: ``projects/001-n8n-vs-make-scale/03_audio/stepfun_voice_<id>.wav``
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

PIPELINE_DIR = Path(__file__).resolve().parent
CORE_DIR = PIPELINE_DIR / "core"
PROJECT_ROOT = PIPELINE_DIR.parent
OUT_DIR = PROJECT_ROOT / "projects" / "001-n8n-vs-make-scale" / "03_audio"

# 1) Load .env BEFORE importing the engine (it reads env at import time).
load_dotenv(CORE_DIR / ".env")

# 2) Make pipeline/core importable (tts_engine, pronunciation, stepfun_client).
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from stepfun_client import (  # noqa: E402
    DEFAULT_STEPFUN_VOICE_ID,
    get_stepfun_config,
    list_system_voices,
    load_voice_catalog,
)
from pronunciation import apply_pronunciation_map  # noqa: E402
from tts_engine import generate_tts  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

#: Technical/analytical sample — exercises acronyms, currency and product names.
SAMPLE_TEXT = (
    "Testing CodeToCost pipeline: n8n versus Make dot com on a $12/mo Docker VPS."
)


def default_voices() -> list[str]:
    """Return the English voices to audition (catalog order, 3 max)."""
    catalog = [v for v in load_voice_catalog() if v.get("english")]
    ids = [v["id"] for v in catalog] or [DEFAULT_STEPFUN_VOICE_ID]
    return ids[:3]


def show_catalog() -> None:
    """Print the packaged voice catalog and the live one (when reachable)."""
    print("─" * 70)
    print("StepFun voice catalog (packaged): pipeline/core/config/stepfun_voices.json")
    for voice in load_voice_catalog():
        flag = "EN" if voice.get("english") else "  "
        print(
            f"  [{flag}] {voice['id']:<26} {voice.get('description', '')[:46]:<46} "
            f"fit={voice.get('channel_fit', '-')}"
        )

    live = list_system_voices()
    print(f"\nLive system_voices endpoint: {'OK' if live else 'unavailable for this key'}")
    for voice in live:
        print(f"  - {voice['id']:<26} {voice.get('name', '')}")


def synthesize(voice_id: str, text: str) -> tuple[bool, str]:
    """Synthesize one sample with ``voice_id`` and write the WAV."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"stepfun_voice_{voice_id}.wav"

    spoken = apply_pronunciation_map(text)
    print("\n── voice=%s ─────────────────────────────────────────" % voice_id)
    print(f"   text   : {text}")
    print(f"   spoken : {spoken}")

    t0 = time.perf_counter()
    try:
        result = generate_tts(spoken, str(out_path), speaker=voice_id)
    except Exception as exc:
        print(f"   FAILED: {type(exc).__name__}: {exc}")
        return False, str(out_path)

    elapsed = time.perf_counter() - t0
    if not out_path.exists():
        print("   FAILED: no WAV file written")
        return False, str(out_path)

    print(f"   OK  wav={out_path}")
    print(
        f"       sr={result.sample_rate}Hz  duration={result.duration_sec:.2f}s  "
        f"size={out_path.stat().st_size / 1024:.1f}KB  wall={elapsed:.1f}s"
    )
    print(f"       provider={result.instruct}")
    return True, str(out_path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "voices",
        nargs="*",
        help="StepFun voice ids to audition (default: 3 English system voices).",
    )
    parser.add_argument("--text", default=SAMPLE_TEXT, help="Sample text.")
    args = parser.parse_args()

    cfg = get_stepfun_config()
    if not cfg["api_key"]:
        print("ERROR: STEPFUN_API_KEY is not set in pipeline/core/.env")
        return 2

    print(f"base_url = {cfg['base_url']}")
    print(f"model    = {cfg['model']}")
    print(f"env voice (STEPFUN_VOICE_ID) = {cfg['voice_id']}")
    print(f"out dir  = {OUT_DIR}")

    show_catalog()

    voices = args.voices or default_voices()
    results: dict[str, bool] = {}
    for voice_id in voices:
        results[voice_id] = synthesize(voice_id, args.text)[0]

    print("\n" + "=" * 70)
    for voice_id, ok in results.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {voice_id}")
    print("  Listen to the WAVs above and set STEPFUN_VOICE_ID in pipeline/core/.env")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
