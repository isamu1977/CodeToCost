"""CodeToCost — Kokoro TTS smoketest (English).

Synthesizes a short technical line to prove the local Kokoro-82M engine works
end to end: .env loading -> HF cache resolution -> model load -> WAV output.

Run from pipeline/core/ with the project venv:
    .venv/bin/python scripts_kokoro_test.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

CORE = Path(__file__).resolve().parent

# 1) Load the project .env FIRST — this is what points HF_HOME at the symlinked
#    model cache. tts_engine.py does NOT call load_dotenv() itself.
load_dotenv(CORE / ".env")

# 2) Force the Kokoro engine for this test (the .env default is "stepfun",
#    which is a paid cloud API and not what we are smoke-testing here).
os.environ["TTS_PROVIDER"] = "kokoro"
os.environ["TTS_ENGINE"] = "kokoro"
os.environ["KOKORO_LANG"] = "a"  # 'a' = American English

# 3) Import AFTER the env is set — the module reads KOKORO_* at import time.
from tts_engine import generate_tts  # noqa: E402

TEXT = "Testing the CodeToCost pipeline: n8n versus Make dot com on a Docker VPS."

OUT_DIR = CORE.parent.parent / "projects" / "001-n8n-vs-make-scale" / "03_audio"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def run(voice: str, filename: str) -> bool:
    out = OUT_DIR / filename
    print(f"\n── voice={voice} → {out.name} " + "─" * 40)
    print(f"   text: {TEXT!r}")
    t0 = time.perf_counter()
    try:
        result = generate_tts(TEXT, str(out), speaker=voice)
    except Exception as exc:
        print(f"   FAILED: {type(exc).__name__}: {exc}")
        return False
    elapsed = time.perf_counter() - t0

    if not out.exists():
        print("   FAILED: no WAV file written")
        return False

    size_kb = out.stat().st_size / 1024
    print(f"   OK  wav={out}")
    print(f"       sr={result.sample_rate}Hz  duration={result.duration_sec:.2f}s  "
          f"size={size_kb:.1f}KB  chunks={result.chunk_count}")
    print(f"       model={result.model_id}  engine_wall_time={elapsed:.1f}s")
    return True


if __name__ == "__main__":
    print("HF_HOME =", os.environ.get("HF_HOME") or "(default ~/.cache/huggingface)")
    voices = sys.argv[1:] or ["af_heart", "am_adam"]
    results = {v: run(v, f"test_en_{v}.wav") for v in voices}

    # The canonical file the task asked for: test_en.wav (first voice).
    canonical = OUT_DIR / "test_en.wav"
    if voices and results.get(voices[0]):
        canonical.write_bytes((OUT_DIR / f"test_en_{voices[0]}.wav").read_bytes())

    print("\n" + "=" * 62)
    for v, ok in results.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {v}")
    print(f"  {'PASS' if canonical.exists() else 'FAIL'}  test_en.wav "
          f"({canonical.stat().st_size / 1024:.1f}KB)" if canonical.exists()
          else f"  FAIL  test_en.wav")
    sys.exit(0 if all(results.values()) and canonical.exists() else 1)
