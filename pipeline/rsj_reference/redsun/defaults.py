"""Centralized defaults for the pipeline.

Every default value that used to be a hardcoded literal scattered
across `main.py`, `run_pipeline_complete.py`, `tts_engine.py` and
`generate_video.py` MUST live here.
"""
from __future__ import annotations


# ── LLM ─────────────────────────────────────────────────────────────────
LLM_DEFAULT_MODEL = "deepseek/deepseek-v4-flash"
LLM_DEFAULT_TEMPERATURE = 0.7
LLM_DEFAULT_MAX_TOKENS = 4000
LLM_DEFAULT_BASE_URL = "http://127.0.0.1:8771"

# ── TTS ─────────────────────────────────────────────────────────────────
TTS_DEFAULT_ENGINE = "kokoro"
TTS_DEFAULT_KOKORO_VOICE = "am_michael"
TTS_DEFAULT_KOKORO_LANG = "a"
TTS_DEFAULT_KOKORO_REPO = "hexgrad/Kokoro-82M"
TTS_DEFAULT_QWEN_SPEAKER = "Vivian"
TTS_DEFAULT_QWEN_INSTRUCT = (
    " speak in a deep, calm, documentary narrator voice with slow pacing"
).lstrip()
TTS_DEFAULT_QWEN_LANGUAGE = "English"
TTS_DEFAULT_QWEN_MODEL = "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"
TTS_DEFAULT_QWEN_DEVICE = "mps"
TTS_CHUNK_MAX_CHARS = 900
TTS_CHUNK_TARGET_CHARS = 700

# ── Whisper ─────────────────────────────────────────────────────────────
WHISPER_DEFAULT_MODEL_SIZE = "base"
WHISPER_DEFAULT_DEVICE = "cpu"
WHISPER_DEFAULT_COMPUTE_TYPE = "int8"
WHISPER_DEFAULT_LANGUAGE = "en"
WHISPER_VAD_FILTER = True
WHISPER_VAD_MIN_SILENCE_MS = 300

# ── Image generation ────────────────────────────────────────────────────
# ComfyUI removed 2026-09-15. Images now come from the gemini-assistant
# Chrome extension (operated manually by Isamu). The wired pipeline uses
# redsun.services.image.ImageService as a **handoff gate** that verifies
# the asset already exists on disk — see PIPELINE_STATE.md for details.
COMFY_DEFAULT_MODEL = "gemini-assistant"
COMFY_DEFAULT_HOST = "127.0.0.1"
COMFY_DEFAULT_PORT = 8002
COMFY_DEFAULT_BASE_SEED = 42
COMFY_CANDIDATE_PORTS = (8002, 8003, 8004, 8188, 8189)

# ── Render ──────────────────────────────────────────────────────────────
RENDER_FPS = 30
RENDER_WIDTH = 1920
RENDER_HEIGHT = 1080
RENDER_CHUNK_SECONDS = 30
RENDER_CHUNKED = True

# ── Visual density ──────────────────────────────────────────────────────
TARGET_IMAGE_SECONDS = 22.0
MAX_IMAGE_SECONDS = 30.0
MIN_VISUAL_BEATS = 12

# ── Quality gates ──────────────────────────────────────────────────────
MIN_AUDIO_DURATION_SEC = 30
# Audio duration validation: calibrated empirically for Kokoro `am_michael`.
# Observed effective cps on Botan Doro = 15.58 (846.27s for 13182 chars).
# 18 gives ~15% headroom above the observed rate while still rejecting
# genuinely truncated audio (a real truncation lands well below 12 cps).
CHARS_PER_SECOND = 18
SILENT_TAIL_THRESHOLD_SEC = 2.0
MIN_COVERAGE_RATIO = 0.85
MIN_IMAGE_BYTES = 10_000

# ── Generic theme defaults ─────────────────────────────────────────────
# This is intentionally non-specific. Operators MAY override via
# ProjectContext.theme_metadata (or via a future ProjectContext
# override). The pipeline MUST NOT assume Japanese content.
DEFAULT_ATMOSPHERE = "historical documentary"