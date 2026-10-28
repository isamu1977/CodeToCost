"""CodeToCost — StepFun English validation (phonetic dictionary -> WAV).

Pipeline under test:

1. read ``agent_workspace/phonetic_dictionary.json``
2. normalize the narration text (acronyms, currency, scale numbers)
3. synthesize with StepFun StepAudio using the configured voice
   (``STEPFUN_VOICE_ID``)
4. write the WAV to
   ``projects/001-n8n-vs-make-scale/03_audio/voice_test_stepfun.wav``

Run from the repo root with the project venv::

    pipeline/core/.venv/bin/python pipeline/test_stepfun_en.py

Optional overrides::

    pipeline/core/.venv/bin/python pipeline/test_stepfun_en.py --voice vibrant-youth
    pipeline/core/.venv/bin/python pipeline/test_stepfun_en.py --text "n8n costs $12/mo"
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
OUT_PATH = OUT_DIR / "voice_test_stepfun.wav"

# 1) Load .env BEFORE importing the engine.
load_dotenv(CORE_DIR / ".env")

# 2) Make pipeline/core importable.
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from pronunciation import apply_pronunciation_map  # noqa: E402
from stepfun_client import get_stepfun_config, resolve_voice_id  # noqa: E402
from tts_engine import generate_tts  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

DEFAULT_TEXT = (
    "Testing CodeToCost pipeline: n8n versus Make dot com on a $12/mo Docker VPS."
)

#: Expected substitutions — the dictionary contract for the English B2B channel.
EXPECTED = {
    "n8n": "N-eight-N",
    "PostgreSQL": "Postgres Q-L",
    "$12/mo": "twelve dollars a month",
    "SaaS": "Sass",
    "VPS": "V-P-S",
    "API": "A-P-I",
}


def show_dictionary() -> None:
    """Print the phonetic dictionary rules that drive the normalization."""
    from pronunciation import load_channel_dictionary

    dictionary = load_channel_dictionary()
    if not dictionary:
        print("WARNING: agent_workspace/phonetic_dictionary.json not found or empty")
        return
    print("─" * 70)
    print(f"phonetic_dictionary.json  language={dictionary.get('language')}  "
          f"version={dictionary.get('version')}")
    print(f"  rules             : {len(dictionary.get('rules') or {})}")
    print(f"  numeric_patterns  : {len(dictionary.get('numeric_patterns') or [])}")
    print(f"  spell-out terms   : {', '.join(dictionary.get('first_occurrence_spellout') or [])}")
    print("  spot-check:")
    for term, expected in EXPECTED.items():
        print(f"    {term:<12} -> {expected}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text", default=DEFAULT_TEXT, help="Text to synthesize.")
    parser.add_argument("--voice", default=None, help="Override the StepFun voice id.")
    parser.add_argument(
        "--out", default=str(OUT_PATH), help="Output WAV path."
    )
    args = parser.parse_args()

    cfg = get_stepfun_config()
    if not cfg["api_key"]:
        print("ERROR: STEPFUN_API_KEY is not set in pipeline/core/.env")
        return 2

    voice = resolve_voice_id(args.voice)
    show_dictionary()

    print("\n" + "─" * 70)
    print("Normalization")
    spoken = apply_pronunciation_map(args.text)
    print(f"  raw    : {args.text}")
    print(f"  spoken : {spoken}")
    changed = spoken != args.text
    print(f"  changed: {changed}")

    print("\nSynthesis")
    print(f"  model  : {cfg['model']}")
    print(f"  voice  : {voice}")
    print(f"  out    : {args.out}")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()
    try:
        result = generate_tts(spoken, str(out_path), speaker=voice)
    except Exception as exc:
        print(f"  FAILED: {type(exc).__name__}: {exc}")
        return 1
    elapsed = time.perf_counter() - t0

    if not out_path.exists():
        print("  FAILED: no WAV file written")
        return 1

    print("\n" + "=" * 70)
    print(f"  PASS  wav={out_path}")
    print(f"        sr={result.sample_rate}Hz  duration={result.duration_sec:.2f}s  "
          f"size={out_path.stat().st_size / 1024:.1f}KB  wall={elapsed:.1f}s")
    print(f"        model={result.model_id}  speaker={result.speaker}")
    print(f"        provider={result.instruct}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
