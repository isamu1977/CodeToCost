#!/usr/bin/env python3
"""Unified CLI for Red Sun Japan Editorial Branch (Amazon KDP + Audiobooks).

Allows generating unabridged book chapters (chapter.md) via Claude Sonnet 5
and master audiobooks (WAV + MP3) via StepFun StepAudio.

Usage:
    python3 scripts/generate_book_assets.py --slug miminashi-hoichi-the-earless-musician --mode text
    python3 scripts/generate_book_assets.py --slug hoichi-the-earless-the-blind-biwa-priest-of-akamagaseki --mode audio
    python3 scripts/generate_book_assets.py --all --mode all
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

# Add motor-python root to sys.path
_THIS_DIR = Path(__file__).resolve().parent.parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

# Load .env
from dotenv import load_dotenv
load_dotenv(_THIS_DIR / ".env")

from database import SessionLocal, VideoProject, init_schema
from redsun.services.book_generator import generate_book_chapter, _resolve_project
from redsun.services.book_audio_generator import generate_book_audiobook

# Setup colorful logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("redsun.book_cli")


def process_project(
    slug_input: str,
    mode: str = "all",
    voice: str = "magnetic-voiced-male",
    speed: float = 0.95,
) -> dict:
    """Process a single project by slug or alias."""
    logger.info("━" * 70)
    logger.info("📚 Processando projeto: '%s' [Modo: %s]", slug_input, mode.upper())
    logger.info("━" * 70)

    db = SessionLocal()
    try:
        project, canonical_slug = _resolve_project(slug_input, db)
        p_title = project.title if project else canonical_slug.replace("-", " ").title()
        logger.info("Título: %s | Slug Canônico: %s", p_title, canonical_slug)
    finally:
        db.close()

    result = {
        "slug_input": slug_input,
        "canonical_slug": canonical_slug,
        "title": p_title,
        "chapter_generated": False,
        "audiobook_generated": False,
        "chapter_path": None,
        "wav_path": None,
        "mp3_path": None,
        "duration_minutes": 0.0,
        "error": None,
    }

    t0 = time.perf_counter()

    # Step 1: Text Chapter
    if mode in ("text", "all"):
        try:
            logger.info("⏳ [1/2] Gerando capítulo literário não-condensado (chapter.md)...")
            chap_path = generate_book_chapter(slug_input)
            result["chapter_generated"] = True
            result["chapter_path"] = chap_path
            
            c_words = len(Path(chap_path).read_text(encoding="utf-8").split())
            logger.info("✔ [1/2] Capítulo gerado com sucesso: %s (%d palavras)", chap_path, c_words)
        except Exception as exc:
            logger.error("❌ [1/2] Falha na geração do capítulo para '%s': %s", slug_input, exc)
            result["error"] = f"chapter_error: {exc}"
            if mode == "all":
                logger.warning("Pulando etapa de áudio devido à falha no capítulo.")
                return result

    # Step 2: Audiobook
    if mode in ("audio", "all"):
        try:
            logger.info("🎙️ [2/2] Sintetizando audiolivro master via StepFun StepAudio (%s, speed=%.2f)...", voice, speed)
            audio_res = generate_book_audiobook(slug_input, voice=voice, speed=speed)
            result["audiobook_generated"] = True
            result["wav_path"] = audio_res.get("wav_path")
            result["mp3_path"] = audio_res.get("mp3_path")
            result["duration_minutes"] = audio_res.get("duration_minutes", 0.0)
            logger.info(
                "✔ [2/2] Audiolivro concluído com sucesso: WAV=%.2f MB | MP3=%.2f MB | Duração=%.2f min",
                audio_res.get("wav_size_mb", 0.0),
                audio_res.get("mp3_size_mb", 0.0),
                audio_res.get("duration_minutes", 0.0),
            )
        except Exception as exc:
            logger.error("❌ [2/2] Falha na síntese do audiolivro para '%s': %s", slug_input, exc)
            if not result["error"]:
                result["error"] = f"audio_error: {exc}"

    elapsed = time.perf_counter() - t0
    logger.info("Tempo decorrido para '%s': %.1fs", slug_input, elapsed)
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Red Sun Japan — CLI Editorial (Livro KDP & Audiolivros StepFun)"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--slug", type=str, help="Slug do projeto ou alias (ex: miminashi-hoichi-the-earless-musician)")
    group.add_argument("--all", action="store_true", help="Processar todos os projetos cadastrados no banco")

    parser.add_argument(
        "--mode",
        choices=["text", "audio", "all"],
        default="all",
        help="Modo de execução: text (apenas capítulo), audio (apenas audiolivro) ou all (ambos)",
    )
    parser.add_argument(
        "--voice",
        type=str,
        default="magnetic-voiced-male",
        help="Voz neural da StepFun (padrão: magnetic-voiced-male)",
    )
    parser.add_argument(
        "--speed",
        type=float,
        default=0.95,
        help="Velocidade de locução (padrão: 0.95)",
    )

    args = parser.parse_args()

    # Ensure schema migrations are up to date
    try:
        init_schema()
    except Exception as exc:
        logger.warning("init_schema warning: %s", exc)

    targets: List[str] = []
    if args.slug:
        targets.append(args.slug)
    elif args.all:
        db = SessionLocal()
        try:
            projects = db.query(VideoProject).filter(VideoProject.source_text != None).all()
            for p in projects:
                if p.slug and (p.source_text or "").strip():
                    targets.append(p.slug)
        finally:
            db.close()
        logger.info("Encontrados %d projetos elegíveis com texto-fonte japonês.", len(targets))

    if not targets:
        logger.error("Nenhum projeto selecionado ou elegível para execução.")
        sys.exit(1)

    summary_results = []
    success_count = 0

    for target_slug in targets:
        res = process_project(
            slug_input=target_slug,
            mode=args.mode,
            voice=args.voice,
            speed=args.speed,
        )
        summary_results.append(res)
        if not res["error"]:
            success_count += 1

    # Print Final Summary Table
    print("\n" + "=" * 80)
    print("📋 RELATÓRIO CONSOLIDADO — ESTEIRA EDITORIAL RED SUN JAPAN")
    print("=" * 80)
    print(f"{'SLUG':<35} | {'CAPÍTULO':<10} | {'AUDIOLIVRO':<12} | {'STATUS':<15}")
    print("-" * 80)
    for r in summary_results:
        chap_stat = "✔ OK" if r["chapter_generated"] else ("- " if args.mode == "audio" else "❌ FALHA")
        audio_stat = f"✔ {r['duration_minutes']:.1f}m" if r["audiobook_generated"] else ("- " if args.mode == "text" else "❌ FALHA")
        overall = "SUCESSO" if not r["error"] else "ERRO"
        print(f"{r['canonical_slug'][:35]:<35} | {chap_stat:<10} | {audio_stat:<12} | {overall:<15}")
    print("=" * 80)
    print(f"Total: {len(summary_results)} | Sucesso: {success_count} | Falhas: {len(summary_results) - success_count}\n")


if __name__ == "__main__":
    main()
