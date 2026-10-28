"""Automated Reverse Audio QA Linter for Red Sun Japan audiobooks and narrations.

Performs 100% local, offline acoustic and textual auditing:
- Layer 1: Reverse transcription via Faster-Whisper (CPU int8).
- Layer 2: Text fidelity linter vs chapter.md (SequenceMatcher / token similarity).
- Layer 3: Acoustic anomaly detection via FFmpeg (DSP silences > 2.8s and clipping).
- Layer 4: Pacing & cadence check (WPM).
- Layer 5: Report generation and persistence in book/audio_qa_report.json.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from redsun.workspace import resolve_project_dir

logger = logging.getLogger("redsun.audio_qa")

# Cached WhisperModel instance for fast subsequent runs
_cached_whisper_model = None


def _get_whisper_model(model_size: str = "base.en"):
    """Instantiate or retrieve cached Faster-Whisper model on CPU with int8."""
    global _cached_whisper_model
    if _cached_whisper_model is None:
        from faster_whisper import WhisperModel
        logger.info("Initializing faster-whisper %s on CPU (compute_type=int8, cpu_threads=8)...", model_size)
        t0 = time.perf_counter()
        _cached_whisper_model = WhisperModel(
            model_size,
            device="cpu",
            compute_type="int8",
            cpu_threads=8,
            num_workers=1,
        )
        logger.info("faster-whisper %s loaded in %.2fs", model_size, time.perf_counter() - t0)
    return _cached_whisper_model


def clean_text_for_comparison(text: str) -> str:
    """Normalize markdown and prose for reliable textual comparison."""
    import unicodedata
    # Remove kanji annotations in parentheses (e.g. (壇ノ浦の戦い, 1185) or (般若心経))
    text = re.sub(r'\([^\)]*[\u3000-\u9fff][^\)]*\)', ' ', text)
    # Normalize unicode accents/macrons (ō -> o, ū -> u, etc.)
    text = unicodedata.normalize('NFKD', text)
    text = ''.join(c for c in text if not unicodedata.combining(c))
    # Remove markdown links [text](url) -> text
    t = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
    # Remove footnotes [^1] or [1]
    t = re.sub(r'\[\^?[^\]]+\]', '', t)
    # Remove headers, blockquotes, horizontal rules
    t = re.sub(r'^#{1,6}\s+', '', t, flags=re.MULTILINE)
    t = re.sub(r'^>\s+', '', t, flags=re.MULTILINE)
    t = re.sub(r'^-{3,}\s*$', '', t, flags=re.MULTILINE)
    # Remove markdown formatting bold/italic/code
    t = re.sub(r'[*_`~]', '', t)
    # Remove punctuation
    t = re.sub(r'[^\w\s]', ' ', t)
    # Lowercase and collapse whitespace
    t = re.sub(r'\s+', ' ', t).strip().lower()
    return t


def detect_acoustic_anomalies(audio_path: Path) -> Dict[str, Any]:
    """Analyze audio for abnormally long silences (> 2.8s) and volume clipping via FFmpeg."""
    cmd = [
        "ffmpeg",
        "-nostdin",
        "-i", str(audio_path),
        "-af", "silencedetect=noise=-45dB:d=2.8,volumedetect",
        "-f", "null",
        "-"
    ]

    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        output = proc.stderr
    except Exception as exc:
        logger.warning("FFmpeg DSP analysis failed: %s", exc)
        return {
            "long_silences_detected": 0,
            "silence_events": [],
            "clipping_detected": False,
            "max_volume_db": -1.0,
            "mean_volume_db": -20.0,
        }

    # 1. Parse silences
    silence_events = []
    # e.g.: [silencedetect @ 0x...] silence_start: 125.4
    # e.g.: [silencedetect @ 0x...] silence_end: 128.9 | silence_duration: 3.5
    silence_starts = [float(m.group(1)) for m in re.finditer(r"silence_start:\s*([0-9.]+)", output)]
    silence_ends = [
        (float(m.group(1)), float(m.group(2)))
        for m in re.finditer(r"silence_end:\s*([0-9.]+)\s*\|\s*silence_duration:\s*([0-9.]+)", output)
    ]

    for i, (end_t, dur) in enumerate(silence_ends):
        start_t = silence_starts[i] if i < len(silence_starts) else max(0.0, end_t - dur)
        if dur >= 2.8:
            silence_events.append({
                "start": round(start_t, 1),
                "end": round(end_t, 1),
                "duration": round(dur, 1),
                "timestamp_formatted": f"{int(start_t // 60):02d}:{int(start_t % 60):02d}",
            })

    # 2. Parse volume levels
    max_vol_match = re.search(r"max_volume:\s*([-0-9.]+)\s*dB", output)
    mean_vol_match = re.search(r"mean_volume:\s*([-0-9.]+)\s*dB", output)

    max_vol = float(max_vol_match.group(1)) if max_vol_match else -1.0
    mean_vol = float(mean_vol_match.group(1)) if mean_vol_match else -20.0
    clipping = max_vol >= 0.0

    return {
        "long_silences_detected": len(silence_events),
        "silence_events": silence_events,
        "clipping_detected": clipping,
        "max_volume_db": max_vol,
        "mean_volume_db": mean_vol,
    }


def audit_audiobook(slug: str, model_size: str = "base.en") -> Dict[str, Any]:
    """Execute complete automated QA audit for an audiobook.

    Args:
        slug: The canonical or folder slug of the project.
        model_size: Faster-whisper model size ("base.en" or "small.en").

    Returns:
        Structured audit report dictionary.
    """
    start_time = time.perf_counter()
    proj_dir = resolve_project_dir(slug)
    if not proj_dir.exists():
        raise FileNotFoundError(f"Project directory not found: {proj_dir}")

    # 1. Locate text and audio files
    book_dir = proj_dir / "book"
    chapter_path = book_dir / "chapter.md"
    if not chapter_path.exists():
        # Fallback to text/script_en.md or script.md
        alt_paths = [proj_dir / "text" / "script_en.md", proj_dir / "script.md"]
        for alt in alt_paths:
            if alt.exists():
                chapter_path = alt
                break
        if not chapter_path.exists():
            raise FileNotFoundError(f"Chapter text not found at {chapter_path}")

    audio_candidates = [
        book_dir / "chapter_audiobook.mp3",
        book_dir / "chapter_audiobook.wav",
        proj_dir / "audio" / "audio_main.wav",
        proj_dir / "audio" / "audio_main.mp3",
    ]
    audio_path = None
    for cand in audio_candidates:
        if cand.exists() and cand.stat().st_size > 1000:
            audio_path = cand
            break

    if not audio_path:
        raise FileNotFoundError(
            f"Audiobook file not found in {book_dir}. Expected chapter_audiobook.mp3 or .wav"
        )

    expected_raw = chapter_path.read_text(encoding="utf-8")
    expected_clean = clean_text_for_comparison(expected_raw)
    expected_tokens = expected_clean.split()
    total_words_expected = len(expected_tokens)

    # 2. Faster-Whisper Reverse Transcription
    logger.info("Starting faster-whisper transcription of %s...", audio_path.name)
    model = _get_whisper_model(model_size)
    segments_gen, info = model.transcribe(
        str(audio_path),
        language="en",
        beam_size=1,
        best_of=1,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=400),
    )

    segments_data: List[Dict[str, Any]] = []
    actual_texts: List[str] = []
    for s in segments_gen:
        txt = s.text.strip()
        if txt:
            segments_data.append({
                "start": round(s.start, 2),
                "end": round(s.end, 2),
                "text": txt,
            })
            actual_texts.append(txt)

    actual_raw = " ".join(actual_texts)
    actual_clean = clean_text_for_comparison(actual_raw)
    actual_tokens = actual_clean.split()

    duration_seconds = round(info.duration if info and info.duration else 0.0, 1)
    if duration_seconds <= 0 and segments_data:
        duration_seconds = segments_data[-1]["end"]

    # 3. Text Fidelity Matching (SequenceMatcher)
    matcher = SequenceMatcher(None, expected_tokens, actual_tokens)
    similarity_ratio = matcher.ratio()
    text_match_percentage = round(similarity_ratio * 100, 2)

    # Find anomalies / missing word blocks
    anomalies: List[Dict[str, Any]] = []
    opcodes = matcher.get_opcodes()
    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "delete" and (i2 - i1) >= 4:
            # Significant text missing from audio
            missing_text = " ".join(expected_tokens[i1:i2])
            # Approximate timestamp from nearest actual token
            approx_time = 0.0
            time_formatted = "00:00"
            if segments_data:
                # Approximate position by token ratio
                ratio = min(1.0, max(0.0, j1 / max(1, len(actual_tokens))))
                approx_time = round(ratio * duration_seconds, 1)
                m = int(approx_time // 60)
                sec = int(approx_time % 60)
                time_formatted = f"{m:02d}:{sec:02d}"

            anomalies.append({
                "type": "missing_text_cut",
                "severity": "high",
                "description": f"Missing passage ({i2 - i1} words): \"{missing_text[:70]}...\"",
                "timestamp_seconds": approx_time,
                "timestamp_formatted": time_formatted,
            })

    # 4. DSP Acoustic Analysis (FFmpeg)
    dsp_results = detect_acoustic_anomalies(audio_path)
    for sil in dsp_results.get("silence_events", []):
        anomalies.append({
            "type": "abnormal_silence",
            "severity": "medium",
            "description": f"Prolonged silence pause of {sil['duration']}s detected",
            "timestamp_seconds": sil["start"],
            "timestamp_formatted": sil["timestamp_formatted"],
        })

    if dsp_results.get("clipping_detected"):
        anomalies.append({
            "type": "audio_clipping",
            "severity": "medium",
            "description": f"Digital clipping risk (Peak: {dsp_results['max_volume_db']} dB)",
            "timestamp_seconds": 0.0,
            "timestamp_formatted": "00:00",
        })

    # 5. Pacing & Cadence (WPM)
    duration_min = max(0.1, duration_seconds / 60.0)
    wpm = round(total_words_expected / duration_min, 1)
    if wpm < 125.0:
        anomalies.append({
            "type": "pacing_slow",
            "severity": "low",
            "description": f"Narration pacing is unusually slow ({wpm} WPM vs standard 130-155 WPM)",
            "timestamp_seconds": 0.0,
            "timestamp_formatted": "00:00",
        })
    elif wpm > 165.0:
        anomalies.append({
            "type": "pacing_fast",
            "severity": "low",
            "description": f"Narration pacing is unusually fast ({wpm} WPM vs standard 130-155 WPM)",
            "timestamp_seconds": 0.0,
            "timestamp_formatted": "00:00",
        })

    # 6. Verdict Determination
    has_severe_cuts = any(a["type"] == "missing_text_cut" for a in anomalies)
    if text_match_percentage >= 98.0 and not has_severe_cuts and dsp_results["long_silences_detected"] == 0:
        verdict = "PASSED"
    elif text_match_percentage >= 95.0 and not has_severe_cuts:
        verdict = "WARNING"
    else:
        verdict = "FAILED"

    # Formatted duration
    dur_m = int(duration_seconds // 60)
    dur_s = int(duration_seconds % 60)
    duration_formatted = f"{dur_m}m {dur_s:02d}s"

    # Build primary anomaly timestamp if any
    primary_anomaly_time = None
    primary_anomaly_formatted = None
    if anomalies:
        primary_anomaly_time = anomalies[0].get("timestamp_seconds", 0.0)
        primary_anomaly_formatted = anomalies[0].get("timestamp_formatted", "00:00")

    elapsed_time = round(time.perf_counter() - start_time, 2)

    summary_text = (
        f"Audio QA: {verdict}. Text match {text_match_percentage}% ({total_words_expected} words, {wpm} WPM). "
        f"Long silences: {dsp_results['long_silences_detected']}. Clipping: {'Yes' if dsp_results['clipping_detected'] else 'No'}. "
        f"Elapsed scan time: {elapsed_time}s."
    )

    report: Dict[str, Any] = {
        "slug": slug,
        "audit_timestamp": datetime.utcnow().isoformat(),
        "verdict": verdict,
        "duration_seconds": duration_seconds,
        "duration_formatted": duration_formatted,
        "total_words_expected": total_words_expected,
        "total_words_transcribed": len(actual_tokens),
        "wpm": wpm,
        "text_match_percentage": text_match_percentage,
        "long_silences_detected": dsp_results["long_silences_detected"],
        "clipping_detected": dsp_results["clipping_detected"],
        "max_volume_db": dsp_results["max_volume_db"],
        "mean_volume_db": dsp_results["mean_volume_db"],
        "primary_anomaly_seconds": primary_anomaly_time,
        "primary_anomaly_formatted": primary_anomaly_formatted,
        "anomalies": anomalies,
        "summary": summary_text,
        "elapsed_seconds": elapsed_time,
    }

    # 7. Persist to book/audio_qa_report.json
    report_file = book_dir / "audio_qa_report.json"
    try:
        with open(report_file, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        logger.info("Persisted Audio QA report to %s", report_file)
    except Exception as save_err:
        logger.warning("Could not persist audio_qa_report.json: %s", save_err)

    return report


def get_existing_audio_qa_report(slug: str) -> Dict[str, Any]:
    """Retrieve existing audio QA report if present on disk."""
    proj_dir = resolve_project_dir(slug)
    report_file = proj_dir / "book" / "audio_qa_report.json"
    if report_file.exists():
        try:
            with open(report_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                data["has_qa"] = True
                return data
        except Exception as e:
            logger.warning("Failed to read audio_qa_report.json: %s", e)

    return {"has_qa": False, "slug": slug}
