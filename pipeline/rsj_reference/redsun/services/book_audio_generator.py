"""Book Audiobook Generator Service for Red Sun Japan.

Synthesizes unabridged literary chapters into a master audiobook using StepFun StepAudio
(stepaudio-2.5-tts), featuring paragraph-aware chunking, dramatic pause injection,
and 192kbps MP3 export with archival ID3 metadata tags.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import soundfile as sf

from database import SessionLocal, VideoProject
from redsun.services.book_generator import _resolve_project
from redsun.services.pronunciation import apply_pronunciation_map
from redsun.services.resilient_tts import (
    synthesize_chunk_waveform_with_fallback,
    synthesize_single_engine_chunk,
    waveform_to_wav_bytes,
)
from redsun.services.stepfun_tts import StepFunContentBlockedError
from redsun.workspace import ProjectWorkspace, RUNTIME_ROOT

logger = logging.getLogger("redsun.book_audio_generator")


def _sanitize_chapter_text(raw_markdown: str) -> List[Tuple[str, bool]]:
    """Clean markdown text for TTS synthesis and categorize chunks by section depth.
    
    Returns:
        List of tuples: (cleaned_paragraph_text, is_major_section_header)
    """
    lines = raw_markdown.splitlines()
    paragraphs: List[Tuple[str, bool]] = []
    current_buf: List[str] = []
    is_major = False

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped == "---":
            if current_buf:
                p_text = " ".join(current_buf).strip()
                if p_text:
                    paragraphs.append((p_text, is_major))
                current_buf = []
                is_major = False
            continue

        # Detect headers
        if stripped.startswith("#"):
            if current_buf:
                p_text = " ".join(current_buf).strip()
                if p_text:
                    paragraphs.append((p_text, is_major))
                current_buf = []

            # Major sections (# or ###) get distinct dramatic pauses
            is_major = True
            header_text = re.sub(r"^#+\s*", "", stripped).strip()
            # Clean roman numerals formatting if needed
            header_text = re.sub(r"\[(.*?)\]", r"\1", header_text)
            current_buf.append(header_text)
            continue

        # Clean bullet lists and glossaries
        if stripped.startswith(("-", "*", "•")) and not stripped.startswith(("***", "**")):
            clean_item = re.sub(r"^[-*•]\s*", "", stripped)
            current_buf.append(clean_item)
            continue

        current_buf.append(stripped)

    if current_buf:
        p_text = " ".join(current_buf).strip()
        if p_text:
            paragraphs.append((p_text, is_major))

    # Second pass: strip formatting characters (*, _, `, [^...])
    cleaned_paragraphs: List[Tuple[str, bool]] = []
    for p_text, major_flag in paragraphs:
        # Remove bold and italic markers
        t = re.sub(r"\*{1,3}(.*?)\*{1,3}", r"\1", p_text)
        t = re.sub(r"_{1,3}(.*?)_{1,3}", r"\1", t)
        t = re.sub(r"`(.*?)`", r"\1", t)
        # Remove markdown links [text](url) -> text
        t = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", t)
        # Remove footnote markers [^1]
        t = re.sub(r"\[\^?\d+\]", "", t)
        # Normalize multiple spaces
        t = re.sub(r"\s+", " ", t).strip()
        if t:
            cleaned_paragraphs.append((t, major_flag))

    return cleaned_paragraphs


def _build_audio_chunks(
    paragraphs: List[Tuple[str, bool]], max_chars: int = 950
) -> List[Tuple[str, float]]:
    """Group paragraphs into safe request blocks with trailing silence duration.
    
    Returns:
        List of tuples: (chunk_text, trailing_silence_seconds)
    """
    chunks: List[Tuple[str, float]] = []
    current_chunk = ""
    current_silence = 0.60

    for p_text, is_major in paragraphs:
        silence_after = 1.50 if is_major else 0.60
        
        # If single paragraph exceeds max_chars, split by sentences
        if len(p_text) > max_chars:
            if current_chunk:
                chunks.append((current_chunk.strip(), current_silence))
                current_chunk = ""
            
            # Sentence split
            sentences = re.split(r"(?<=[.!?])\s+", p_text)
            sub_chunk = ""
            for s in sentences:
                if len(sub_chunk) + len(s) + 1 <= max_chars:
                    sub_chunk = f"{sub_chunk} {s}".strip() if sub_chunk else s
                else:
                    if sub_chunk:
                        chunks.append((sub_chunk.strip(), 0.60))
                    sub_chunk = s
            if sub_chunk:
                chunks.append((sub_chunk.strip(), silence_after))
            continue

        # Normal grouping
        projected_len = len(current_chunk) + len(p_text) + 2 if current_chunk else len(p_text)
        if projected_len <= max_chars:
            current_chunk = f"{current_chunk}\n\n{p_text}".strip() if current_chunk else p_text
            current_silence = max(current_silence, silence_after)
        else:
            chunks.append((current_chunk.strip(), current_silence))
            current_chunk = p_text
            current_silence = silence_after

    if current_chunk:
        chunks.append((current_chunk.strip(), current_silence))

    return chunks


def generate_book_audiobook(
    slug: str,
    provider: str = "stepfun",
    voice: str = "magnetic-voiced-male",
    speed: float = 0.95,
) -> Dict[str, Any]:
    """Generate unabridged audiobook WAV and MP3 from projects/<slug>/book/chapter.md.
    
    Args:
        slug: Project slug or alias.
        provider: TTS provider ('stepfun' with Edge-TTS 451 fallback, or 'edgetts' for pure Edge-TTS).
        voice: StepFun voice ID (default: 'magnetic-voiced-male').
        speed: Narration rate multiplier (default: 0.95).
        
    Returns:
        Dictionary with deliverable paths, duration and file metrics.
    """
    logger.info(
        "Starting audiobook generation for project '%s' (provider=%s, voice=%s, speed=%.2f)...",
        slug,
        provider,
        voice,
        speed,
    )
    db = SessionLocal()
    canonical_slug = slug
    project: Optional[VideoProject] = None

    try:
        project, canonical_slug = _resolve_project(slug, db)
        ws = ProjectWorkspace.from_slug(canonical_slug, project_id=project.id if project else None)
        book_dir = ws.book_dir
        chapter_path = ws.book_chapter_path

        if not chapter_path.exists():
            raise FileNotFoundError(
                f"Chapter file not found: {chapter_path}. "
                "Generate the chapter first using POST /projects/{slug}/book/chapter."
            )

        raw_chapter = chapter_path.read_text(encoding="utf-8")
        if not raw_chapter.strip():
            raise ValueError(f"Chapter file is empty at {chapter_path}")

        # 1. Clean markdown & extract paragraphs
        paragraphs = _sanitize_chapter_text(raw_chapter)
        if not paragraphs:
            raise ValueError("No readable paragraphs could be extracted from chapter.md")

        # 2. Build chunks with dramatic pause annotations
        chunks = _build_audio_chunks(paragraphs, max_chars=950)
        total_chunks = len(chunks)
        logger.info("Divided chapter into %d TTS chunks with dynamic dramatic pauses", total_chunks)

        # 3. Apply Job-Level Failover synthesis across all chunks
        # Voice Homogeneity Policy: 100% of chapter is narrated by one single voice.
        target_provider = (provider or "stepfun").lower().strip()
        if target_provider in ("stepfun", "stepaudio", "step"):
            candidate_engines = ["stepfun", "fish_audio", "edgetts"]
        elif target_provider in ("fish_audio", "fishaudio", "fish-audio", "fish"):
            candidate_engines = ["fish_audio", "edgetts"]
        elif target_provider in ("edgetts", "edge-tts", "edge_tts", "edge"):
            candidate_engines = ["edgetts"]
        else:
            candidate_engines = ["stepfun", "fish_audio", "edgetts"]

        all_waves: List[np.ndarray] = []
        sample_rate = 24000
        t0_total = time.perf_counter()
        final_engine_used: Optional[str] = None
        fallback_reason: Optional[str] = None

        for engine in candidate_engines:
            # Tier 2 Fish Audio: check if key is available before starting
            if engine == "fish_audio":
                fish_api_key = os.environ.get("FISH_AUDIO_API_KEY", "").strip().strip('"').strip("'")
                if not fish_api_key:
                    logger.warning(
                        "[TTS Failover] Fish Audio indisponível. Reiniciando capítulo inteiro do Bloco 1 via Tier 3 (Edge-TTS)..."
                    )
                    if not fallback_reason:
                        fallback_reason = "Fish Audio key missing"
                    continue

            engine_waves: List[np.ndarray] = []
            chunk_failed = False
            fail_exc: Optional[Exception] = None
            failed_chunk_idx = 0

            logger.info(
                "Iniciando síntese homogênea do capítulo via %s (%d chunks)...",
                engine.upper(),
                total_chunks,
            )

            for idx, (chunk_text, silence_sec) in enumerate(chunks, start=1):
                print(
                    f"[{engine.upper()}] [Chunk {idx:02d}/{total_chunks:02d}] Processando {len(chunk_text)} caracteres...",
                    flush=True,
                )
                t0 = time.perf_counter()
                try:
                    waveform, sr = synthesize_single_engine_chunk(
                        text=chunk_text,
                        engine=engine,
                        voice=voice if engine == "stepfun" else None,
                        speed=speed,
                        slug=canonical_slug,
                    )
                    gen_s = time.perf_counter() - t0
                    sample_rate = sr
                    engine_waves.append(waveform)

                    # Inject trailing silence buffer
                    if idx < total_chunks:
                        silence_samples = int(sr * silence_sec)
                        if silence_samples > 0:
                            engine_waves.append(np.zeros(silence_samples, dtype=np.float32))

                    logger.info(
                        "Audiobook chunk %d/%d sintetizado via %s (audio=%.1fs, elapsed=%.1fs, trailing_pause=%.2fs)",
                        idx,
                        total_chunks,
                        engine,
                        len(waveform) / sr,
                        gen_s,
                        silence_sec,
                    )

                    # Safety rate-limit pause between StepFun HTTP requests (1.5s) if StepFun was used
                    if idx < total_chunks and engine == "stepfun":
                        time.sleep(1.5)

                except Exception as exc:
                    chunk_failed = True
                    fail_exc = exc
                    failed_chunk_idx = idx
                    break

            if not chunk_failed and engine_waves:
                # 100% of chapter succeeded with THIS engine!
                all_waves = engine_waves
                final_engine_used = engine
                logger.info(
                    "Capítulo literário 100%% sintetizado com sucesso via voz homogênea: %s",
                    engine.upper(),
                )
                break
            else:
                # Gatilho de Aborto: Descarte todos os buffers gerados até aquele momento!
                engine_waves.clear()
                del engine_waves

                exc_str = str(fail_exc)
                is_451 = (
                    isinstance(fail_exc, StepFunContentBlockedError)
                    or "451" in exc_str
                    or "content_policy" in exc_str.lower()
                    or "blocked" in exc_str.lower()
                )

                if engine == "stepfun":
                    fallback_reason = (
                        f"StepFun HTTP 451 on paragraph {failed_chunk_idx}"
                        if is_451
                        else f"StepFun error on paragraph {failed_chunk_idx}: {fail_exc}"
                    )
                    logger.warning(
                        "[TTS Failover] StepFun rejeitou o conteúdo (HTTP 451). Descartando lote parcial e reiniciando o capítulo inteiro do Bloco 1 via Tier 2 (Fish Audio)..."
                    )
                elif engine == "fish_audio":
                    fallback_reason = fallback_reason or f"Fish Audio error on paragraph {failed_chunk_idx}: {fail_exc}"
                    logger.warning(
                        "[TTS Failover] Fish Audio indisponível. Reiniciando capítulo inteiro do Bloco 1 via Tier 3 (Edge-TTS)..."
                    )
                else:
                    logger.error("[TTS Failover] Edge-TTS falhou no chunk %d: %s", failed_chunk_idx, fail_exc)
                    fallback_reason = fallback_reason or f"Edge-TTS error on paragraph {failed_chunk_idx}: {fail_exc}"

        if not all_waves or not final_engine_used:
            raise RuntimeError(f"Todos os tiers de TTS falharam para o audiolivro: {fallback_reason}")

        full_wave = np.concatenate(all_waves) if len(all_waves) > 1 else all_waves[0]
        total_duration_sec = len(full_wave) / sample_rate

        # 4. Save Master WAV
        wav_path = book_dir / "chapter_audiobook.wav"
        sf.write(str(wav_path), full_wave, sample_rate, subtype="PCM_16")
        wav_size = wav_path.stat().st_size
        logger.info("WAV Audiobook saved: %s (%.2f MB, %.1fs)", wav_path, wav_size / (1024 * 1024), total_duration_sec)

        # 5. Convert to 192kbps MP3 with Archival ID3 Metadata
        mp3_path = book_dir / "chapter_audiobook.mp3"

        # Extract title from chapter.md or project
        first_line = raw_chapter.strip().split("\n")[0] if raw_chapter else ""
        if first_line.startswith("# Chapter"):
            book_title = re.sub(r"\s*\([^)]*\)", "", first_line.replace("#", "")).strip()
        elif project and project.title:
            book_title = project.title
        else:
            book_title = canonical_slug.replace("-", " ").title()
        
        ffmpeg_cmd = [
            "ffmpeg",
            "-y",
            "-i", str(wav_path),
            "-b:a", "192k",
            "-metadata", f"title={book_title}",
            "-metadata", "artist=Red Sun Japan Archives",
            "-metadata", "album=Chronicles of the Red Sun, Vol. I",
            "-metadata", "genre=Audiobook / Japanese Historical Kaidan",
            "-metadata", "comment=Official Red Sun Japan Unabridged Archival Audiobook Edition",
            str(mp3_path),
        ]
        
        logger.info("Encoding MP3 audiobook via ffmpeg: %s", " ".join(ffmpeg_cmd))
        proc = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            logger.error("ffmpeg MP3 encoding failed: %s", proc.stderr[:500])
            raise RuntimeError(f"ffmpeg encoding error: {proc.stderr[:300]}")

        mp3_size = mp3_path.stat().st_size
        elapsed_total = time.perf_counter() - t0_total
        logger.info("MP3 Audiobook saved: %s (%.2f MB)", mp3_path, mp3_size / (1024 * 1024))

        # 6. Update SQLite DB & metadata
        if project:
            try:
                project.has_book_audiobook = True
                if hasattr(project, "book_audiobook_engine"):
                    project.book_audiobook_engine = final_engine_used
                if hasattr(project, "book_audiobook_fallback_reason"):
                    project.book_audiobook_fallback_reason = fallback_reason
                db.commit()
                logger.info(
                    "Updated video_projects.has_book_audiobook = True, engine = %s for project ID %d",
                    final_engine_used,
                    project.id,
                )
            except Exception as db_exc:
                logger.warning("Could not update has_book_audiobook flag in DB: %s", db_exc)
                db.rollback()

        proj_info_path = ws.root / "project_info.json"
        if proj_info_path.exists():
            try:
                p_data = json.loads(proj_info_path.read_text(encoding="utf-8"))
                p_data["has_book_audiobook"] = True
                p_data["audiobook_duration_sec"] = total_duration_sec
                p_data["audiobook_updated_at"] = mp3_path.stat().st_mtime
                p_data["audiobook_provider"] = final_engine_used
                p_data["audiobook_engine_used"] = final_engine_used
                p_data["audiobook_fallback_reason"] = fallback_reason
                p_data["fallback_chunks_count"] = 0
                proj_info_path.write_text(json.dumps(p_data, indent=2, ensure_ascii=False), encoding="utf-8")
            except Exception as p_exc:
                logger.warning("Could not update project_info.json: %s", p_exc)

        # Mirror deliverables to alias dir if alias was passed
        if slug != canonical_slug:
            alias_book_dir = Path(RUNTIME_ROOT) / slug / "book"
            if alias_book_dir.exists() and alias_book_dir.is_dir():
                try:
                    if not (alias_book_dir / "chapter_audiobook.wav").exists():
                        os.link(wav_path, alias_book_dir / "chapter_audiobook.wav")
                    if not (alias_book_dir / "chapter_audiobook.mp3").exists():
                        os.link(mp3_path, alias_book_dir / "chapter_audiobook.mp3")
                except Exception:
                    pass

        fallbacks_triggered = {
            "fish_audio": 1 if final_engine_used == "fish_audio" else 0,
            "edgetts": 1 if final_engine_used == "edgetts" else 0,
        }

        # 6.5 Run automated reverse audio QA audit (Text fidelity, DSP, WPM)
        qa_report = None
        try:
            from redsun.services.audio_qa_service import audit_audiobook
            logger.info("Executing automated reverse audio QA linter for %s...", canonical_slug)
            qa_report = audit_audiobook(canonical_slug)
        except Exception as qa_exc:
            logger.warning("Automated audio QA failed for %s: %s", canonical_slug, qa_exc)

        effective_voice = (
            voice if final_engine_used == "stepfun"
            else ("s2.1-pro" if final_engine_used == "fish_audio" else "en-US-ChristopherNeural")
        )

        return {
            "status": "success",
            "slug": canonical_slug,
            "title": book_title,
            "engine_used": final_engine_used,
            "fallback_reason": fallback_reason,
            "qa_report": qa_report,
            "primary_engine": target_provider,
            "provider": final_engine_used,
            "voice": effective_voice,
            "fallbacks_triggered": fallbacks_triggered,
            "fallback_chunks_count": 0,
            "engine_breakdown": {final_engine_used: total_chunks},
            "wav_path": str(wav_path.resolve()),
            "mp3_path": str(mp3_path.resolve()),
            "wav_size_bytes": wav_size,
            "mp3_size_bytes": mp3_size,
            "wav_size_mb": round(wav_size / (1024 * 1024), 2),
            "mp3_size_mb": round(mp3_size / (1024 * 1024), 2),
            "duration_seconds": round(total_duration_sec, 2),
            "duration_minutes": round(total_duration_sec / 60.0, 2),
            "audiobook_duration_seconds": round(total_duration_sec, 2),
            "audiobook_duration_minutes": round(total_duration_sec / 60.0, 2),
            "chunk_count": total_chunks,
            "elapsed_seconds": round(elapsed_total, 2),
        }

    finally:
        db.close()
