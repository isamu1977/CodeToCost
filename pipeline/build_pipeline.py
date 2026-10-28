#!/usr/bin/env python3
"""Deterministic build orchestrator for the CodeToCost video pipeline.

This replaces the Red Sun Japan monolith (``pipeline/rsj_reference/main.py``)
with a thin, linear orchestrator that only reuses the reusable engines that
already live in ``pipeline/core/``:

    1. read    -> ``projects/<project>/02_spec/video_spec.json``
    2. tts     -> ``pipeline/core/tts_engine.py``  (one WAV per scene)
    3. whisper -> ``pipeline/core/whisper_engine.py`` (word-level timestamps)
    4. timeline-> ``projects/<project>/timeline.json`` (frames + component props)
    5. render  -> ``npx remotion render`` (optional, behind ``--render``)

Design rules
------------
* **Deterministic**: the same spec + the same engines always produce the same
  timeline. No hidden state, no database, no network access outside the TTS
  provider.
* **Cacheable**: TTS is the only expensive/networked step. A scene is only
  re-synthesised when its *normalized text* (plus the voice/engine parameters)
  changes. Timestamps are re-used while the WAV bytes are unchanged.
* **Scoped**: no framework, no global mutable state, explicit types.

Usage
-----
    python pipeline/build_pipeline.py --project 001-n8n-vs-make-scale
    python pipeline/build_pipeline.py --project 001-n8n-vs-make-scale --render

Notes
-----
* ``pipeline/core/.env`` is loaded before the engines are imported (the engines
  read their configuration at import time).
* The Remotion composition id used by the render step is ``MainComposition``
  (see ``remotion-app/src/Root.tsx``). Scene WAVs are synced into
  ``remotion-app/public/<project>/`` right before rendering so the composition
  can reach them through ``staticFile()``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ─────────────────────────────────────────────────────────────────────────────
# Paths — resolved from this file, never from the caller's working directory
# ─────────────────────────────────────────────────────────────────────────────
_PIPELINE_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _PIPELINE_DIR.parent
_CORE_DIR = _PIPELINE_DIR / "core"
_PROJECTS_DIR = _REPO_ROOT / "projects"
_REMOTION_DIR = _REPO_ROOT / "remotion-app"

#: Render defaults for the CodeToCost channel (1920x1080 @ 30fps).
DEFAULT_FPS = 30
DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 1080

#: Remotion composition rendered by the ``--render`` step.
RENDER_COMPOSITION_ID = "MainComposition"

logger = logging.getLogger("codetocost.pipeline")


# ─────────────────────────────────────────────────────────────────────────────
# Environment / runtime bootstrap
# ─────────────────────────────────────────────────────────────────────────────
def _load_env_file() -> None:
    """Load ``pipeline/core/.env`` into ``os.environ`` (existing values win)."""
    env_path = _CORE_DIR / ".env"
    if not env_path.exists():
        return

    try:
        from dotenv import load_dotenv

        load_dotenv(env_path, override=False)
        return
    except ImportError:
        logger.debug("python-dotenv not available, using the built-in .env parser")

    # Minimal fallback parser (KEY=VALUE, # comments, optional quotes).
    try:
        raw_env = env_path.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover - unreadable .env
        logger.warning("Could not read %s: %s", env_path, exc)
        return

    for line in raw_env.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def _ensure_core_runtime() -> None:
    """Re-exec inside ``pipeline/core/.venv`` when the deps live only there.

    The engines (numpy, soundfile, faster-whisper) are installed in the core
    virtualenv. If the interpreter running this script cannot import them and
    that virtualenv exists, we hand over to its Python instead of failing with
    an opaque ``ModuleNotFoundError``.
    """
    venv_python = _CORE_DIR / ".venv" / "bin" / "python"
    if not venv_python.exists():
        return
    if Path(sys.prefix).resolve() == venv_python.parent.parent.resolve():
        return  # already running from the virtualenv
    if os.environ.get("CODETOCOST_REEXEC") == "1":
        return  # guard against an exec loop

    try:
        import numpy  # noqa: F401
        import soundfile  # noqa: F401

        return  # the current interpreter already has the deps
    except ImportError:
        pass

    env = dict(os.environ, CODETOCOST_REEXEC="1")
    logger.info("Re-executing with %s", venv_python)
    argv = [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]]
    os.execve(str(venv_python), argv, env)


def _import_engines() -> tuple[Any, Any, Any]:
    """Import the core engines after the environment is loaded.

    Returns ``(tts_engine, whisper_engine, apply_pronunciation_map)``.
    """
    if str(_CORE_DIR) not in sys.path:
        sys.path.insert(0, str(_CORE_DIR))

    import tts_engine
    import whisper_engine
    from pronunciation import apply_pronunciation_map

    return tts_engine, whisper_engine, apply_pronunciation_map


# ─────────────────────────────────────────────────────────────────────────────
# Small helpers
# ─────────────────────────────────────────────────────────────────────────────
def _fingerprint(*parts: str) -> str:
    """Stable sha256 over the given parts (NUL separated)."""
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


def _sha256_file(path: Path) -> str:
    """sha256 of a file's bytes."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def _scene_files(project_dir: Path, index: int) -> tuple[Path, Path, Path]:
    """Return ``(wav, timestamps, meta)`` paths for a 1-based scene index."""
    stem = f"scene_{index:02d}"
    audio_dir = project_dir / "03_audio"
    return (
        audio_dir / f"{stem}.wav",
        audio_dir / f"{stem}.timestamps.json",
        audio_dir / f"{stem}.meta.json",
    )


def _language_code(spec: dict[str, Any]) -> str:
    """Map a spec language tag (``en-US``) to a Whisper language code (``en``)."""
    raw = str(spec.get("language") or spec.get("language_code") or "en")
    return raw.split("-")[0].split("_")[0].strip().lower() or "en"


# ─────────────────────────────────────────────────────────────────────────────
# Spec
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Scene:
    """A single scene of the video spec."""

    index: int
    scene_id: str
    spoken_text: str
    display_subtitle: str
    visual_component: str
    component_props: dict[str, Any]
    voice: str | None
    duration_hint: float | None


def load_spec(project_dir: Path) -> dict[str, Any]:
    """Read and validate ``02_spec/video_spec.json``."""
    spec_path = project_dir / "02_spec" / "video_spec.json"
    if not spec_path.exists():
        raise FileNotFoundError(
            f"Video spec not found: {spec_path}\n"
            "The spec is the pipeline's only input — create it before running the build."
        )

    spec = _read_json(spec_path)
    if not isinstance(spec, dict):
        raise ValueError(f"{spec_path} must contain a JSON object")
    if not isinstance(spec.get("scenes"), list) or not spec["scenes"]:
        raise ValueError(f"{spec_path} has no 'scenes' array")
    return spec


def parse_scenes(spec: dict[str, Any]) -> list[Scene]:
    """Normalize the raw ``scenes`` array into :class:`Scene` objects."""
    scenes: list[Scene] = []
    for position, raw in enumerate(spec["scenes"], start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"scene #{position} is not a JSON object")

        spoken = str(raw.get("spoken_text") or "").strip()
        duration_hint = raw.get("duration_sec")
        if not spoken and duration_hint is None:
            raise ValueError(
                f"scene #{position} ({raw.get('scene_id', '?')}) has neither "
                "'spoken_text' nor 'duration_sec' — the pipeline cannot time it."
            )

        props = raw.get("component_props")
        scenes.append(
            Scene(
                index=position,
                scene_id=str(raw.get("scene_id") or f"scene_{position:02d}"),
                spoken_text=spoken,
                display_subtitle=str(raw.get("display_subtitle") or ""),
                visual_component=str(raw.get("visual_component") or ""),
                component_props=props if isinstance(props, dict) else {},
                voice=(str(raw["voice"]) if raw.get("voice") else None),
                duration_hint=(float(duration_hint) if duration_hint is not None else None),
            )
        )
    return scenes


# ─────────────────────────────────────────────────────────────────────────────
# Step 2 — TTS
# ─────────────────────────────────────────────────────────────────────────────
#: Target integrated loudness (LUFS) of the narration. YouTube normalizes
#: playback to -14 LUFS; the channel ships -16 LUFS so the mix keeps
#: headroom for a future music bed.
LOUDNESS_TARGET_LUFS = float(os.environ.get("TTS_LOUDNESS_LUFS", "-16"))
#: Maximum true peak (dBTP) after normalization — prevents inter-sample clipping.
LOUDNESS_TRUE_PEAK_DB = float(os.environ.get("TTS_LOUDNESS_TP", "-1.5"))
#: Loudness range (LU) tolerated by the normalizer.
LOUDNESS_RANGE_LU = float(os.environ.get("TTS_LOUDNESS_LRA", "11"))


@dataclass
class TtsParams:
    """Effective TTS parameters — part of the cache key."""

    provider: str
    voice: str
    model: str
    speed: str

    def fingerprint(self, text: str) -> str:
        return _fingerprint(
            text,
            self.provider,
            self.voice,
            self.model,
            self.speed,
            # The loudness target is part of the key: changing it must
            # invalidate the cached WAV, otherwise the old, un-normalized
            # audio would be reused.
            f"loudness={LOUDNESS_TARGET_LUFS}:{LOUDNESS_TRUE_PEAK_DB}",
        )


def resolve_tts_params(tts_engine: Any, voice_override: str | None) -> TtsParams:
    """Resolve provider/voice/model/speed for the active engine."""
    provider = (
        os.environ.get("TTS_PROVIDER")
        or os.environ.get("TTS_ENGINE")
        or getattr(tts_engine, "TTS_PROVIDER", "stepfun")
    ).lower().strip()

    if provider == "kokoro":
        voice = voice_override or getattr(tts_engine, "KOKORO_DEFAULT_VOICE", "")
        model = getattr(tts_engine, "KOKORO_REPO_ID", "")
        speed = "1.0"
    elif provider == "minimax":
        voice = voice_override or getattr(tts_engine, "MINIMAX_DEFAULT_VOICE", "")
        model = getattr(tts_engine, "MINIMAX_MODEL", "")
        speed = str(getattr(tts_engine, "MINIMAX_SPEED", 1.0))
    elif provider == "qwen":
        voice = voice_override or getattr(tts_engine, "QWEN_DEFAULT_SPEAKER", "")
        model = getattr(tts_engine, "QWEN_MODEL_ID", "")
        speed = "1.0"
    else:  # stepfun (default)
        provider = "stepfun"
        voice = voice_override or getattr(tts_engine, "STEPFUN_DEFAULT_VOICE", "")
        model = getattr(tts_engine, "STEPFUN_MODEL", "")
        speed = str(getattr(tts_engine, "STEPFUN_SPEED", 1.0))

    return TtsParams(provider=provider, voice=voice, model=model, speed=speed)


def synthesize_scene_audio(
    *,
    tts_engine: Any,
    apply_pronunciation_map: Any,
    scene: Scene,
    project_name: str,
    wav_path: Path,
    meta_path: Path,
    params: TtsParams,
    force: bool,
) -> tuple[str, bool]:
    """Generate the scene WAV (cache-aware).

    Returns ``(normalized_text, from_cache)``.
    """
    # Phonetic normalization happens *before* synthesis so the cache key
    # describes what is actually spoken.
    normalized = apply_pronunciation_map(scene.spoken_text, slug=project_name)
    text_hash = params.fingerprint(normalized)

    meta: dict[str, Any] = {}
    if meta_path.exists():
        try:
            loaded = _read_json(meta_path)
            if isinstance(loaded, dict):
                meta = loaded
        except (OSError, json.JSONDecodeError):
            meta = {}

    if not force and wav_path.exists() and meta.get("text_hash") == text_hash:
        if meta.get("audio_sha256") == _sha256_file(wav_path):
            logger.info("[%s] TTS cache hit — reusing %s", scene.scene_id, wav_path.name)
            return normalized, True
        logger.info("[%s] cache stale (audio changed) — regenerating", scene.scene_id)

    logger.info("[%s] TTS via %s (voice=%s)", scene.scene_id, params.provider, params.voice)
    result = tts_engine.generate_tts(
        text=normalized,
        output_path=str(wav_path),
        speaker=params.voice or None,
        slug=project_name,
    )

    # Normalize the narration before it reaches Remotion — the raw StepFun
    # output sits around -18 LUFS, which is too quiet for the channel.
    loudness = normalize_loudness(wav_path)
    duration_sec, _sr = measure_audio(wav_path)

    _write_json(
        meta_path,
        {
            "scene_id": scene.scene_id,
            "text_hash": text_hash,
            "normalized_text": normalized,
            "provider": params.provider,
            "voice": params.voice,
            "model": params.model,
            "speed": params.speed,
            # `instruct` carries the engine internals (model/speed/providers),
            # e.g. "providers=stepfun" or "providers=edgetts" when StepFun
            # failed and the Edge-TTS failsafe took over.
            "engine_details": result.instruct,
            "sample_rate": result.sample_rate,
            "duration_sec": round(duration_sec, 4),
            "loudness": loudness,
            # Hash of the *normalized* file — the artifact on disk.
            "audio_sha256": _sha256_file(wav_path),
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
    )
    logger.info(
        "[%s] audio written: %s (%.2fs)", scene.scene_id, wav_path.name, duration_sec
    )
    return normalized, False


def normalize_loudness(wav_path: Path) -> dict[str, Any]:
    """Normalize a narration WAV to the channel loudness target, in place.

    Uses ``ffmpeg``'s two-pass ``loudnorm`` (EBU R128). When ``ffmpeg`` is not
    available, falls back to a peak normalization in numpy so the build never
    breaks because of a missing binary.

    Returns a small report (target/measured values) stored in the scene meta.
    """
    target_i = LOUDNESS_TARGET_LUFS
    target_tp = LOUDNESS_TRUE_PEAK_DB
    target_lra = LOUDNESS_RANGE_LU

    try:
        measured = _loudnorm_measure(wav_path, target_i, target_tp, target_lra)
    except Exception as exc:  # pragma: no cover - ffmpeg missing/broken
        logger.warning("Loudness measurement failed (%s) — using peak fallback", exc)
        return _peak_normalize_fallback(wav_path)

    measured_i = measured.get("input_i")
    measured_tp = measured.get("input_tp")
    measured_lra = measured.get("input_lra")
    measured_thresh = measured.get("input_thresh")
    offset = measured.get("target_offset")

    if measured_i is None:
        return _peak_normalize_fallback(wav_path)

    filter_str = (
        f"loudnorm=I={target_i}:TP={target_tp}:LRA={target_lra}"
        f":measured_I={measured_i}:measured_TP={measured_tp}:measured_LRA={measured_lra}"
        f":measured_thresh={measured_thresh}:offset={offset}:linear=true:print_format=summary"
    )

    tmp_path = wav_path.with_name(wav_path.stem + ".norm.wav")
    command = [
        "ffmpeg", "-hide_banner", "-nostdin", "-y",
        "-i", str(wav_path),
        "-af", filter_str,
        "-ar", str(int(measured.get("sample_rate") or 24000)),
        "-c:a", "pcm_s16le",
        str(tmp_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0 or not tmp_path.exists():
        logger.warning("ffmpeg loudnorm failed (%s) — using peak fallback", result.stderr[-400:])
        tmp_path.unlink(missing_ok=True)
        return _peak_normalize_fallback(wav_path)

    tmp_path.replace(wav_path)
    logger.info(
        "Loudness normalized: %.1f LUFS -> %.1f LUFS (TP %.1f dBTP)",
        measured_i, target_i, target_tp,
    )
    return {
        "method": "ffmpeg-loudnorm-2pass",
        "target_lufs": target_i,
        "target_true_peak_db": target_tp,
        "measured_lufs": measured_i,
        "measured_true_peak_db": measured_tp,
    }


def _loudnorm_measure(
    wav_path: Path, target_i: float, target_tp: float, target_lra: float
) -> dict[str, Any]:
    """Run the ``loudnorm`` analysis pass and parse its JSON report."""
    command = [
        "ffmpeg", "-hide_banner", "-nostdin",
        "-i", str(wav_path),
        "-af", f"loudnorm=I={target_i}:TP={target_tp}:LRA={target_lra}:print_format=json",
        "-f", "null", "-",
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg exited with {result.returncode}: {result.stderr[-400:]}")

    # The JSON block is the last {...} in stderr.
    start = result.stderr.rfind("{")
    end = result.stderr.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise RuntimeError("ffmpeg loudnorm did not emit a JSON report")
    report = json.loads(result.stderr[start : end + 1])

    def _num(key: str) -> float | None:
        value = report.get(key)
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    return {
        "input_i": _num("input_i"),
        "input_tp": _num("input_tp"),
        "input_lra": _num("input_lra"),
        "input_thresh": _num("input_thresh"),
        "target_offset": _num("target_offset") or 0.0,
        "sample_rate": _num("input_sample_rate"),
    }


def _peak_normalize_fallback(wav_path: Path) -> dict[str, Any]:
    """Peak-normalize the WAV to -1 dBFS using numpy (ffmpeg-less fallback)."""
    import numpy as np
    import soundfile as sf

    data, sample_rate = sf.read(str(wav_path), dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)
    peak = float(np.max(np.abs(data))) if data.size else 0.0
    if peak <= 0:
        return {"method": "none", "reason": "silent audio"}

    gain = (10 ** (-1.0 / 20)) / peak
    sf.write(str(wav_path), np.clip(data * gain, -1.0, 1.0), sample_rate, subtype="PCM_16")
    logger.info("Peak-normalized to -1 dBFS (gain %.2f dB)", 20 * np.log10(gain))
    return {
        "method": "peak-fallback",
        "target_true_peak_db": -1.0,
        "gain_db": round(20 * float(np.log10(gain)), 2),
    }


def measure_audio(wav_path: Path) -> tuple[float, int]:
    """Return ``(duration_sec, sample_rate)`` of a WAV file."""
    import soundfile as sf

    info = sf.info(str(wav_path))
    if info.samplerate <= 0:
        raise ValueError(f"Invalid sample rate for {wav_path}: {info.samplerate}")
    return info.frames / info.samplerate, int(info.samplerate)


# ─────────────────────────────────────────────────────────────────────────────
# Step 3 — Whisper
# ─────────────────────────────────────────────────────────────────────────────
def transcribe_scene(
    *,
    whisper_engine: Any,
    scene: Scene,
    wav_path: Path,
    timestamps_path: Path,
    meta_path: Path,
    language: str,
    force: bool,
) -> list[dict[str, Any]]:
    """Transcribe a scene WAV to word-level timestamps (cache-aware)."""
    audio_sha = _sha256_file(wav_path)

    meta: dict[str, Any] = {}
    if meta_path.exists():
        try:
            loaded = _read_json(meta_path)
            if isinstance(loaded, dict):
                meta = loaded
        except (OSError, json.JSONDecodeError):
            meta = {}

    if (
        not force
        and timestamps_path.exists()
        and meta.get("timestamps_audio_sha256") == audio_sha
    ):
        logger.info("[%s] Whisper cache hit — reusing %s", scene.scene_id, timestamps_path.name)
        words = _read_json(timestamps_path)
        return words if isinstance(words, list) else []

    logger.info("[%s] transcribing with faster-whisper (language=%s)", scene.scene_id, language)
    whisper_engine.transcribe_audio(
        str(wav_path),
        str(timestamps_path),
        language=language,
    )
    words = _read_json(timestamps_path)
    if not isinstance(words, list):
        raise ValueError(f"Unexpected timestamps payload in {timestamps_path}")

    meta["timestamps_audio_sha256"] = audio_sha
    meta["timestamps_path"] = timestamps_path.name
    meta["word_count"] = len(words)
    meta["transcribed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _write_json(meta_path, meta)

    logger.info("[%s] %d word timestamps", scene.scene_id, len(words))
    return words


# ─────────────────────────────────────────────────────────────────────────────
# Step 4 — Timeline compiler
# ─────────────────────────────────────────────────────────────────────────────
def compile_timeline(
    *,
    spec: dict[str, Any],
    scenes: list[Scene],
    scene_data: list[dict[str, Any]],
    fps: int,
    project_name: str,
) -> dict[str, Any]:
    """Merge scene audio/timestamps with the visual props into a Remotion timeline.

    The output follows the ``Timeline`` contract declared in
    ``remotion-app/src/types.ts`` (camelCase: ``scenes[].id`` / ``startFrame`` /
    ``durationInFrames`` / ``props``), so the very same file can be handed to
    ``npx remotion render --props`` without a translation step.

    ``scene_data`` holds one entry per scene (same order as ``scenes``) with
    the keys ``duration_sec``, ``words`` and ``audio_file``.
    """
    timeline_scenes: list[dict[str, Any]] = []
    global_words: list[dict[str, Any]] = []

    cursor_frame = 0
    for scene, data in zip(scenes, scene_data, strict=True):
        duration_sec = float(data["duration_sec"])
        duration_frames = max(1, round(duration_sec * fps))
        start_frame = cursor_frame
        start_sec = start_frame / fps

        for word in data.get("words") or []:
            if not isinstance(word, dict):
                continue
            local_start = float(word.get("start", 0.0))
            local_end = float(word.get("end", local_start))
            global_words.append(
                {
                    "word": str(word.get("word", "")),
                    "start": round(start_sec + local_start, 3),
                    "end": round(start_sec + local_end, 3),
                }
            )

        # The component props carry the discriminated `kind` consumed by the
        # Remotion `SceneRenderer`; `visual_component` is the fallback.
        props = dict(scene.component_props)
        kind = str(props.get("kind") or scene.visual_component or "title")
        props["kind"] = kind

        # The narration WAV is served by Remotion through `staticFile()`, which
        # only resolves files under `remotion-app/public/`. The pipeline syncs
        # the scene WAVs to `public/<project>/`, hence the `<project>/` prefix.
        audio_file = data.get("audio_file")
        scene_audio = f"{project_name}/{Path(audio_file).name}" if audio_file else None

        timeline_scenes.append(
            {
                "id": scene.scene_id,
                "startFrame": start_frame,
                "durationInFrames": duration_frames,
                "type": kind,
                "props": props,
                **({"audioFile": scene_audio} if scene_audio else {}),
            }
        )

        cursor_frame = start_frame + duration_frames

    total_frames = cursor_frame
    brand = spec.get("brand") if isinstance(spec.get("brand"), dict) else {}
    return {
        "version": 1,
        "videoId": str(spec.get("video_id") or spec.get("slug") or project_name),
        "title": str(spec.get("title") or ""),
        "language": str(spec.get("language") or spec.get("language_code") or "en"),
        "fps": fps,
        "width": DEFAULT_WIDTH,
        "height": DEFAULT_HEIGHT,
        "durationInFrames": total_frames,
        "durationSeconds": round(total_frames / fps, 3),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "brand": brand,
        "scenes": timeline_scenes,
        "words": global_words,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Step 5 — Render
# ─────────────────────────────────────────────────────────────────────────────
def sync_render_assets(project_name: str, project_dir: Path) -> None:
    """Copy the scene WAVs into ``remotion-app/public/<project>/``.

    Remotion's ``staticFile()`` only serves ``remotion-app/public/``, so the
    narration has to live there for the composition to play it.
    """
    target_dir = _REMOTION_DIR / "public" / project_name
    target_dir.mkdir(parents=True, exist_ok=True)
    for wav in sorted((project_dir / "03_audio").glob("scene_*.wav")):
        shutil.copy2(wav, target_dir / wav.name)
    logger.info("Synced scene audio to %s", target_dir)


def write_render_props(project_dir: Path, timeline: dict[str, Any]) -> Path:
    """Write the Remotion props file for a compiled timeline.

    The composition declares ``defaultProps={{ timeline }}``, so the input
    props must carry the timeline under the ``timeline`` key — a bare
    timeline object would be merged *next to* the mock instead of replacing
    it, and the render would silently use the mock scenes (no audio).
    """
    props_path = project_dir / "render_props.json"
    _write_json(props_path, {"timeline": timeline})
    return props_path


def render_video(project_name: str, output_name: str = "video.mp4") -> int:
    """Run ``npx remotion render`` for the compiled timeline."""
    if not _REMOTION_DIR.exists():
        logger.error("Remotion app not found at %s", _REMOTION_DIR)
        return 1

    project_dir = _PROJECTS_DIR / project_name
    output_dir = project_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    sync_render_assets(project_name, project_dir)

    timeline_path = project_dir / "timeline.json"
    if not timeline_path.exists():
        logger.error("Timeline not found: %s", timeline_path)
        return 1
    timeline = _read_json(timeline_path)
    props_path = write_render_props(project_dir, timeline)

    command = [
        "npx",
        "remotion",
        "render",
        "src/index.ts",
        RENDER_COMPOSITION_ID,
        f"../projects/{project_name}/output/{output_name}",
        f"--props=../projects/{project_name}/{props_path.name}",
    ]
    logger.info("Rendering: %s", " ".join(command))
    result = subprocess.run(command, cwd=str(_REMOTION_DIR), check=False)
    if result.returncode != 0:
        logger.error("Remotion render failed with exit code %d", result.returncode)
        return result.returncode
    logger.info("Render finished: %s", output_dir / output_name)
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# Orchestration
# ─────────────────────────────────────────────────────────────────────────────
def build_project(
    project_name: str,
    *,
    fps: int = DEFAULT_FPS,
    voice: str | None = None,
    force: bool = False,
    render: bool = False,
    output_name: str = "video.mp4",
) -> dict[str, Any]:
    """Run the full pipeline for one project. Returns the compiled timeline."""
    project_dir = _PROJECTS_DIR / project_name
    if not project_dir.is_dir():
        raise FileNotFoundError(f"Project directory not found: {project_dir}")

    tts_engine, whisper_engine, apply_pronunciation_map = _import_engines()

    # ── Step 1: spec ────────────────────────────────────────────────────────
    logger.info("Step 1/5 — loading spec for '%s'", project_name)
    spec = load_spec(project_dir)
    scenes = parse_scenes(spec)
    language = _language_code(spec)
    logger.info("Loaded %d scene(s), language=%s", len(scenes), language)

    base_params = resolve_tts_params(tts_engine, voice)
    scene_data: list[dict[str, Any]] = []

    for scene in scenes:
        wav_path, timestamps_path, meta_path = _scene_files(project_dir, scene.index)

        if not scene.spoken_text:
            # Visual-only scene: no narration, no audio, duration comes from
            # the spec (validated in parse_scenes).
            logger.info("[%s] visual-only scene (%ss)", scene.scene_id, scene.duration_hint)
            scene_data.append(
                {
                    "duration_sec": float(scene.duration_hint or 0.0),
                    "normalized_text": "",
                    "words": [],
                    "audio_file": None,
                    "timestamps_file": None,
                }
            )
            continue

        # ── Step 2: TTS ─────────────────────────────────────────────────────
        params = base_params
        if scene.voice:
            params = resolve_tts_params(tts_engine, scene.voice)
        normalized_text, _from_cache = synthesize_scene_audio(
            tts_engine=tts_engine,
            apply_pronunciation_map=apply_pronunciation_map,
            scene=scene,
            project_name=project_name,
            wav_path=wav_path,
            meta_path=meta_path,
            params=params,
            force=force,
        )
        duration_sec, _sample_rate = measure_audio(wav_path)

        # ── Step 3: Whisper ────────────────────────────────────────────────
        words = transcribe_scene(
            whisper_engine=whisper_engine,
            scene=scene,
            wav_path=wav_path,
            timestamps_path=timestamps_path,
            meta_path=meta_path,
            language=language,
            force=force,
        )

        scene_data.append(
            {
                "duration_sec": duration_sec,
                "normalized_text": normalized_text,
                "words": words,
                "audio_file": str(wav_path.relative_to(project_dir)),
                "timestamps_file": str(timestamps_path.relative_to(project_dir)),
            }
        )

    # ── Step 4: timeline ────────────────────────────────────────────────────
    logger.info("Step 4/5 — compiling timeline at %d fps", fps)
    timeline = compile_timeline(
        spec=spec,
        scenes=scenes,
        scene_data=scene_data,
        fps=fps,
        project_name=project_name,
    )
    timeline_path = project_dir / "timeline.json"
    _write_json(timeline_path, timeline)
    logger.info(
        "Timeline written: %s (%d frames / %.2fs)",
        timeline_path,
        timeline["durationInFrames"],
        timeline["durationSeconds"],
    )

    # ── Step 5: render (optional) ──────────────────────────────────────────
    if render:
        logger.info("Step 5/5 — rendering with Remotion")
        exit_code = render_video(project_name, output_name=output_name)
        if exit_code != 0:
            raise RuntimeError(f"Remotion render failed (exit code {exit_code})")
    else:
        logger.info("Step 5/5 — skipped (pass --render to render the video)")

    return timeline


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="build_pipeline.py",
        description="Build a CodeToCost video: spec -> TTS -> Whisper -> timeline -> render.",
    )
    parser.add_argument(
        "--project",
        required=True,
        metavar="NAME",
        help="Project folder name under projects/ (e.g. 001-n8n-vs-make-scale).",
    )
    parser.add_argument(
        "--render",
        action="store_true",
        help="Render the MP4 with Remotion after compiling the timeline.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore the TTS/Whisper caches and regenerate everything.",
    )
    parser.add_argument(
        "--voice",
        default=None,
        help="Override the TTS voice id for this build.",
    )
    parser.add_argument(
        "--fps",
        type=int,
        default=DEFAULT_FPS,
        help=f"Frames per second for the timeline (default: {DEFAULT_FPS}).",
    )
    parser.add_argument(
        "--output",
        default="video.mp4",
        metavar="FILE",
        help="Output file name for the render (written to projects/<project>/output/).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Console log level (default: INFO).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    _load_env_file()
    _ensure_core_runtime()

    try:
        build_project(
            args.project,
            fps=args.fps,
            voice=args.voice,
            force=args.force,
            render=args.render,
            output_name=args.output,
        )
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        logger.error("%s", exc)
        return 1

    logger.info("Build complete for '%s'", args.project)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
