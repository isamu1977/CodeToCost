#!/usr/bin/env python3
"""Migrate project physical folders to standardized 3-digit numbered format (001_slug).

Ensures Finder and terminal displays projects in chronological creation order.
Updates SQLite translation variant paths and validates internal/external symlinks.

Usage:
  python3 scripts/migrate_project_folders_to_numbered.py --dry-run
  python3 scripts/migrate_project_folders_to_numbered.py --run
  python3 scripts/migrate_project_folders_to_numbered.py --run --slug hoichi-the-earless-...
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Optional

# Add motor-python root to sys.path
SCRIPT_DIR = Path(__file__).resolve().parent
MOTOR_PYTHON_ROOT = SCRIPT_DIR.parent
if str(MOTOR_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(MOTOR_PYTHON_ROOT))

# Ensure REDSUN_DATABASE_URL points to canonical SQLite DB
db_candidate = MOTOR_PYTHON_ROOT / "redsunjapan.db"
if db_candidate.exists() and "REDSUN_DATABASE_URL" not in os.environ:
    os.environ["REDSUN_DATABASE_URL"] = f"sqlite:///{db_candidate.resolve()}"

from database import SessionLocal, VideoProject, TranslationVariant, get_project_folder_name
from redsun.workspace import ProjectWorkspace, RUNTIME_ROOT

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("redsun.migrate_folders")


def validate_internal_symlinks(project_dir: Path) -> dict[str, str]:
    """Validate or repair canonical relative symlinks inside the project directory."""
    results = {}
    images_dir = project_dir / "images"
    gemini_dir = project_dir / "gemini-assistant"
    
    if not gemini_dir.exists():
        return {"status": "no_gemini_dir"}

    target_output = gemini_dir / "output"
    target_refs = gemini_dir / "references"
    target_output.mkdir(parents=True, exist_ok=True)
    target_refs.mkdir(parents=True, exist_ok=True)

    images_dir.mkdir(parents=True, exist_ok=True)

    for link_name, rel_target in [
        ("final", "../gemini-assistant/output"),
        ("references", "../gemini-assistant/references"),
    ]:
        link_path = images_dir / link_name
        if link_path.is_symlink():
            try:
                dest = os.readlink(str(link_path))
                if dest == rel_target or (images_dir / dest).resolve().exists():
                    results[link_name] = "valid"
                    continue
                link_path.unlink()
            except OSError:
                link_path.unlink()
        elif link_path.exists() and link_path.is_dir():
            # Already physical dir; keep it
            results[link_name] = "physical_dir"
            continue

        try:
            os.symlink(rel_target, str(link_path))
            results[link_name] = "repaired_symlink"
        except Exception as exc:
            results[link_name] = f"error: {exc}"

    return results


def migrate_projects(dry_run: bool = True, target_slug: Optional[str] = None) -> list[dict[str, Any]]:
    """Execute migration of project directories to numbered prefix format."""
    db = SessionLocal()
    runtime_root = Path(RUNTIME_ROOT)

    query = db.query(VideoProject).order_by(VideoProject.id.asc())
    if target_slug:
        query = query.filter(VideoProject.slug == target_slug)

    projects = query.all()
    results: list[dict[str, Any]] = []

    print("\n" + "=" * 80)
    mode_label = "DRY-RUN (SIMULATION — NO DISK MODIFICATIONS)" if dry_run else "LIVE MIGRATION (APPLYING CHANGES TO DISK & DB)"
    print(f" RED SUN JAPAN — PROJECT FOLDER STANDARDIZATION")
    print(f" Runtime Root: {runtime_root}")
    print(f" Mode: {mode_label}")
    print("=" * 80 + "\n")

    for p in projects:
        pid = p.id
        slug = p.slug or f"project-{pid}"
        numbered_name = get_project_folder_name(pid, slug)
        numbered_path = runtime_root / numbered_name
        pure_path = runtime_root / slug

        res = {
            "id": pid,
            "slug": slug,
            "title": p.title,
            "numbered_folder": numbered_name,
            "status": "pending",
            "from_path": None,
            "to_path": str(numbered_path),
            "db_updated": False,
        }

        # Check if numbered folder already exists and is not empty
        if numbered_path.exists() and numbered_path.is_dir() and not pure_path.exists():
            res["status"] = "ALREADY_MIGRATED"
            results.append(res)
            print(f"✓ [{pid:03d}] {numbered_name} -> JÁ MIGRADAS")
            continue

        # Determine source path
        source_path: Optional[Path] = None
        is_symlink_source = False
        resolved_symlink_target: Optional[Path] = None

        if pure_path.exists():
            if pure_path.is_symlink():
                is_symlink_source = True
                link_dest = Path(os.readlink(str(pure_path)))
                if not link_dest.is_absolute():
                    resolved_symlink_target = (pure_path.parent / link_dest).resolve()
                else:
                    resolved_symlink_target = link_dest.resolve()
                source_path = resolved_symlink_target if resolved_symlink_target.exists() else pure_path
            else:
                source_path = pure_path
        else:
            # Special check: check if any unnumbered directory matches slug
            # e.g., the-zuufura-ghost for ID 35
            if pid == 35:
                alt = runtime_root / "the-zuufura-ghost"
                if alt.exists():
                    source_path = alt

        if not source_path or not source_path.exists():
            res["status"] = "NOT_FOUND_ON_DISK"
            results.append(res)
            print(f"! [{pid:03d}] {slug} -> NÃO ENCONTRADO NO DISCO ({pure_path})")
            continue

        res["from_path"] = str(source_path)

        if dry_run:
            res["status"] = "PLANNED"
            results.append(res)
            print(f"→ [{pid:03d}] PLANEJADO:")
            print(f"       De:   {source_path}")
            print(f"       Para: {numbered_path}")
            continue

        # LIVE MIGRATION EXECUTION
        try:
            logger.info("Migrating #%03d: %s -> %s", pid, source_path.name, numbered_name)
            
            # If pure_path was a symlink to source_path, remove the symlink first
            if is_symlink_source and pure_path.is_symlink():
                try:
                    pure_path.unlink()
                except OSError:
                    pass

            # Move physical folder to numbered_path
            shutil.move(str(source_path), str(numbered_path))

            # Maintain compatibility symlink: pure_path -> numbered_path
            # (Allows old references, scripts or tools to continue resolving seamlessly)
            try:
                if not pure_path.exists():
                    os.symlink(numbered_name, str(pure_path))
                    logger.info("Created backward-compatible symlink: %s -> %s", pure_path.name, numbered_name)
            except Exception as sym_err:
                logger.warning("Could not create compatibility symlink %s: %s", pure_path.name, sym_err)

            # Update SQLite TranslationVariant paths
            variants = db.query(TranslationVariant).filter(TranslationVariant.project_id == pid).all()
            db_changed = False
            for v in variants:
                for col in ("audio_path", "timestamps_path", "output_video_path"):
                    val = getattr(v, col, None)
                    if val and (str(source_path) in val or str(pure_path) in val or "the-zuufura-ghost" in val):
                        new_val = val.replace(str(source_path), str(numbered_path)).replace(str(pure_path), str(numbered_path)).replace("the-zuufura-ghost", numbered_name)
                        setattr(v, col, new_val)
                        db_changed = True
                        logger.info("Updated DB %s for variant #%d -> %s", col, v.id, new_val)

            if db_changed:
                db.commit()
                res["db_updated"] = True

            # Revalidate internal symlinks (images/final, images/references)
            symlink_status = validate_internal_symlinks(numbered_path)
            res["symlinks"] = symlink_status

            res["status"] = "SUCCESS"
            results.append(res)
            print(f"✓ [{pid:03d}] {source_path.name} -> {numbered_name} [OK]")

        except Exception as exc:
            logger.exception("Failed to migrate project #%d '%s': %s", pid, slug, exc)
            res["status"] = f"ERROR: {exc}"
            results.append(res)
            print(f"✗ [{pid:03d}] ERRO AO MIGRAR: {exc}")

    # Revalidate external alias symlinks (like miminashi-hoichi-the-earless-musician)
    miminashi_link = runtime_root / "miminashi-hoichi-the-earless-musician"
    hoichi_target = runtime_root / "037_hoichi-the-earless-the-blind-biwa-priest-of-akamagaseki"
    if hoichi_target.exists():
        if not dry_run:
            try:
                if miminashi_link.is_symlink() or miminashi_link.exists():
                    miminashi_link.unlink()
                os.symlink(hoichi_target.name, str(miminashi_link))
                logger.info("Re-pointed alias symlink: %s -> %s", miminashi_link.name, hoichi_target.name)
            except Exception as exc:
                logger.warning("Could not update alias symlink: %s", exc)

    db.close()
    return results


def print_summary_table(results: list[dict[str, Any]], dry_run: bool) -> None:
    """Print structured summary report in chronological ID order."""
    print("\n" + "=" * 90)
    print(f" RELATÓRIO DE MIGRAÇÃO CHRONOLÓGICA DAS PASTAS FÍSICAS")
    print("=" * 90)
    print(f"{'ID':<6} {'STATUS':<20} {'PASTA ANTERIOR':<35} {'PASTA CANÔNICA NO DISCO'}")
    print("-" * 90)
    
    success_count = 0
    already_count = 0
    planned_count = 0
    error_count = 0

    for r in results:
        pid = f"#{r['id']:03d}"
        status = r["status"]
        from_name = os.path.basename(r["from_path"]) if r["from_path"] else "-"
        to_name = r["numbered_folder"]

        if status == "SUCCESS":
            success_count += 1
        elif status == "ALREADY_MIGRATED":
            already_count += 1
        elif status == "PLANNED":
            planned_count += 1
        else:
            error_count += 1

        print(f"{pid:<6} {status:<20} {from_name[:33]:<35} {to_name}")

    print("-" * 90)
    print(f"Total Projetos: {len(results)} | Planejados: {planned_count} | Sucesso: {success_count} | Já Migrados: {already_count} | Erros: {error_count}")
    print("=" * 90 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate Red Sun Japan project folders to standardized 3-digit numbered format (001_slug)."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate the migration without modifying files or database records.",
    )
    group.add_argument(
        "--run",
        action="store_true",
        help="Execute the live folder migration and update database records.",
    )
    parser.add_argument(
        "--slug",
        type=str,
        default=None,
        help="Migrate only a specific project slug.",
    )

    args = parser.parse_args()
    dry_run = not args.run

    results = migrate_projects(dry_run=dry_run, target_slug=args.slug)
    print_summary_table(results, dry_run=dry_run)


if __name__ == "__main__":
    main()
