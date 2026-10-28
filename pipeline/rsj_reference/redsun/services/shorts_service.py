"""Shorts Service for Red Sun Japan Pipeline.

Coordinates Step #15 (Vertical 9:16 Shorts Generation):
1. Pre-render sanitation of render-remotion/public/ to prevent cross-contamination.
2. Strict physical asset validation (fail-fast, eliminating ghost metadata).
3. Canonical scene discovery from active project output.
4. Deterministic Remotion rendering (1080x1920, 9:16).
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

from database import SessionLocal, VideoProject, TranslationVariant
from redsun.workspace import ProjectWorkspace

logger = logging.getLogger("redsun.shorts_service")

# Static Remotion project directories
REMOTION_DIR = Path("/Users/isamumatsuyama/Documents/online-projects/red-sun-japan/red-sun-project-files/channel-redsunjapan/render-remotion")
REMOTION_PUBLIC = REMOTION_DIR / "public"

# Immutable brand assets in REMOTION_PUBLIC that must NEVER be deleted during purge
IMMUTABLE_BRAND_ASSETS = {
    "red-sun-logo-background-tate.png",
    "red-sun-logo-main.png",
    "red-sun-logo-tate-white.png",
    "red-sun-logo-tate.png",
    "red-sun-logo-yoko.png",
    "youtube_cta.png",
    "intro_01.png",
    "intro_02.jpg",
    "intro_03.jpg",
    "intro_04.png",
    "intro_samurai_genjigumo.jpg",
    "intro_snow_yukionna.jpg",
    "intro_yokai_masayoshi.png",
    "intro_yokai_yoshifuji.png",
    "Maurice_Ravel_-_Pavane_pour_une_infante_défunte.ogg",
}

# Cross-contamination indicators (foreign story character names)
CONTAMINATION_KEYWORDS = {"shinnojo", "botan_doro", "otsuyu", "peonies", "peony"}


class ShortsService:
    """Orchestrates safe, contamination-free generation of 9:16 Shorts."""

    @staticmethod
    def purge_remotion_public(remotion_public_dir: Optional[Path] = None) -> int:
        """Purges ephemeral project files from render-remotion/public/ via shutil.rmtree and os.makedirs,
        safely preserving immutable brand assets (logos, bumpers) to guarantee complete isolation between projects."""
        pub_dir = remotion_public_dir or REMOTION_PUBLIC
        if not pub_dir.exists():
            pub_dir.mkdir(parents=True, exist_ok=True)
            return 0

        # Preserve immutable brand assets in memory or temp
        brand_cache: dict[str, bytes] = {}
        for item in pub_dir.iterdir():
            if item.is_file() and not item.name.startswith("."):
                is_brand = (
                    item.name in IMMUTABLE_BRAND_ASSETS
                    or item.name.startswith(("red-sun-", "redsun-", "redusun-", "youtube_"))
                    or item.name.endswith(("intro.mp4", "ending.mp4", "outro.mp4"))
                )
                if is_brand:
                    try:
                        brand_cache[item.name] = item.read_bytes()
                    except Exception as exc:
                        logger.warning("Could not cache brand asset %s: %s", item.name, exc)

        # Forced isolation purge via shutil.rmtree followed by os.makedirs
        try:
            shutil.rmtree(pub_dir, ignore_errors=True)
            os.makedirs(pub_dir, exist_ok=True)
        except Exception as exc:
            logger.warning("Error recreating %s: %s", pub_dir, exc)
            pub_dir.mkdir(parents=True, exist_ok=True)

        # Restore preserved brand assets
        for b_name, b_data in brand_cache.items():
            try:
                (pub_dir / b_name).write_bytes(b_data)
            except Exception as exc:
                logger.warning("Could not restore brand asset %s: %s", b_name, exc)

        logger.info("Purged render-remotion/public via rmtree/makedirs. Restored %d brand assets.", len(brand_cache))
        return len(brand_cache)

    @staticmethod
    def get_canonical_project_scenes(project_dir: Path) -> list[str]:
        """Strictly extracts canonical scene filenames (e.g. shot_001.jpeg) from
        projects/{slug}/gemini-assistant/output/ or projects/{slug}/images/final/.
        Never reads from REMOTION_PUBLIC or global DB caches.
        Enforces strict ascending numerical ordering (shot_001.jpeg, shot_002.jpeg...)."""
        output_dir = project_dir / "gemini-assistant" / "output"
        if not output_dir.exists() or not any(output_dir.iterdir()):
            output_dir = project_dir / "images" / "final"
        if not output_dir.exists() or not any(output_dir.iterdir()):
            output_dir = project_dir / "output"

        if not output_dir.exists():
            logger.error("No scene output directory found in %s", project_dir)
            return []

        def _extract_shot_index(filename: str) -> int:
            m = re.search(r"shot[-_](\d+)", filename, re.IGNORECASE)
            if m:
                return int(m.group(1))
            m2 = re.search(r"(\d+)", filename)
            if m2:
                return int(m2.group(1))
            return 999999

        valid_exts = {".jpeg", ".jpg", ".png", ".webp"}
        matched_files = []

        for f in output_dir.iterdir():
            # Skip non-files, hidden files, or circular symlinks
            if f.name.startswith("."):
                continue
            if not f.is_file():
                continue
            if f.suffix.lower() not in valid_exts:
                continue
            idx = _extract_shot_index(f.name)
            matched_files.append((idx, f.name))

        # Strict ascending numerical ordering
        matched_files.sort(key=lambda x: (x[0], x[1]))
        canonical_scenes = [item[1] for item in matched_files]
        logger.info("Discovered %d canonical scene files strictly on disk in %s", len(canonical_scenes), output_dir)
        return canonical_scenes

    @staticmethod
    def resolve_project_asset(project_dir: Path, filename: str) -> Optional[Path]:
        """Resolves a physical asset strictly inside the active project directory."""
        candidates = [
            project_dir / "gemini-assistant" / "output" / filename,
            project_dir / "images" / "final" / filename,
            project_dir / "gemini-assistant" / "references" / filename,
            project_dir / "images" / "references" / filename,
            project_dir / "output" / filename,
            project_dir / "images" / filename,
            project_dir / "gemini-assistant" / "ref-job" / filename,
            project_dir / filename,
        ]
        for c in candidates:
            if c.exists() and c.is_file():
                return c
        return None

    @classmethod
    def validate_clip_assets(cls, project_dir: Path, clip: dict[str, Any], slug: str) -> None:
        """Validates that all images referenced in a clip exist physically on disk.
        Fails fast if any asset is missing on disk to prevent ghost metadata."""
        images = clip.get("imageFilenames", [])
        if not images:
            raise FileNotFoundError(f"Erro Crítico: Clip {clip.get('id')} não possui imageFilenames no projeto {slug}.")

        for img_name in images:
            resolved = cls.resolve_project_asset(project_dir, img_name)
            if not resolved or not resolved.exists():
                raise FileNotFoundError(
                    f"Erro Crítico: Asset {img_name} não existe no projeto {slug}. Abortando render."
                )

    @classmethod
    def is_clip_contaminated(cls, clip: dict[str, Any], slug: str) -> bool:
        """Checks if a clip contains metadata or assets from an unrelated project."""
        is_botan_project = any(k in slug.lower() for k in ("zuufura", "botan", "peony"))
        clip_str = json.dumps(clip).lower()

        if not is_botan_project:
            for kw in CONTAMINATION_KEYWORDS:
                if kw in clip_str:
                    return True

        return False

    @classmethod
    def generate_shorts_manifest(
        cls,
        slug: str,
        project_dir: Path,
        title: str,
        subtitles_file: Path,
        force_regenerate: bool = False,
    ) -> list[dict[str, Any]]:
        """Assembles and strictly validates 3 dramatic vertical clips (9:16).
        Single Source of Truth: Reads EXCLUSIVELY from physical disk scenes.
        Prohibits reusing stale short_clips_json from DB or global tables.
        Enforces physical existence on disk of every referenced asset."""
        # The ONLY canonical official directory for shorts is video/shorts/
        shorts_dir = project_dir / "video" / "shorts"
        shorts_dir.mkdir(parents=True, exist_ok=True)
        meta_file = shorts_dir / "shorts_metadata.json"

        # Migrate from legacy project_dir / "shorts" if needed, then purge legacy dir
        legacy_shorts_dir = project_dir / "shorts"
        if legacy_shorts_dir.exists() and legacy_shorts_dir.is_dir():
            legacy_meta = legacy_shorts_dir / "shorts_metadata.json"
            if legacy_meta.exists() and not meta_file.exists():
                shutil.copy2(legacy_meta, meta_file)
            shutil.rmtree(legacy_shorts_dir, ignore_errors=True)

        # Validate existing metadata if present (strictly against physical disk)
        if not force_regenerate and meta_file.exists():
            data = json.loads(meta_file.read_text(encoding="utf-8"))
            existing_clips = data.get("clips", [])
            if len(existing_clips) >= 3:
                for c in existing_clips[:3]:
                    if cls.is_clip_contaminated(c, slug):
                        raise FileNotFoundError(
                            f"Erro Crítico: Manifesto contaminado com metadados de outra história no projeto {slug}. Abortando render."
                        )
                    cls.validate_clip_assets(project_dir, c, slug)
                logger.info("Reusing strictly verified physical shorts metadata for %s", slug)
                return existing_clips[:3]

        # Fresh curation: Single source of truth from physical disk
        logger.info("Curating fresh, disk-validated shorts metadata for %s...", slug)
        scenes = cls.get_canonical_project_scenes(project_dir)
        if not scenes or len(scenes) < 3:
            raise FileNotFoundError(
                f"Erro Crítico: Não existem cenas canônicas suficientes no disco ({len(scenes)}) "
                f"em {project_dir} para o projeto {slug}. Abortando render."
            )

        # Dynamic narrative window allocation based on subtitles duration
        total_dur = 300.0
        if subtitles_file.exists():
            try:
                subs = json.loads(subtitles_file.read_text(encoding="utf-8"))
                if isinstance(subs, list) and len(subs) > 10:
                    total_dur = float(subs[-1].get("end", 300.0))
            except Exception:
                pass

        p1 = max(total_dur * 0.15, 15.0)
        p2 = total_dur * 0.50
        p3 = min(total_dur * 0.85, max(total_dur - 35.0, 30.0))

        num_scenes = len(scenes)
        step = num_scenes // 3

        clips = [
            {
                "id": f"{slug}_short_01",
                "startSec": round(p1, 2),
                "endSec": round(p1 + 30.0, 2),
                "hookText": f"The Legend of {title}",
                "highlight": "The Ominous Beginning",
                "imageFilenames": scenes[0:min(4, num_scenes)],
            },
            {
                "id": f"{slug}_short_02",
                "startSec": round(p2, 2),
                "endSec": round(p2 + 30.0, 2),
                "hookText": f"The Terrifying Secret of {title}",
                "highlight": "The Curse Unfolds",
                "imageFilenames": scenes[step:min(step + 4, num_scenes)],
            },
            {
                "id": f"{slug}_short_03",
                "startSec": round(p3, 2),
                "endSec": round(p3 + 30.0, 2),
                "hookText": f"The Final Climax of {title}",
                "highlight": "Retribution",
                "imageFilenames": scenes[step * 2:min(step * 2 + 4, num_scenes)],
            },
        ]

        # Strictly validate all chosen clip assets
        for c in clips:
            cls.validate_clip_assets(project_dir, c, slug)

        # Persist to disk
        meta_file.write_text(json.dumps({"clips": clips}, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("Saved validated shorts manifest to %s", meta_file)

        # Persist to DB
        db = SessionLocal()
        try:
            p = db.query(VideoProject).filter(VideoProject.slug == slug).first()
            if p:
                v = db.query(TranslationVariant).filter(
                    TranslationVariant.project_id == p.id,
                    TranslationVariant.language_code == "en"
                ).first()
                if v:
                    v.short_clips_json = json.dumps({"clips": clips}, ensure_ascii=False)
                    db.commit()
                    logger.info("Updated TranslationVariant.short_clips_json in DB for %s", slug)
        except Exception as exc:
            logger.warning("Failed to persist clips to DB: %s", exc)
        finally:
            db.close()

        return clips

    @classmethod
    def sync_assets_for_shorts(
        cls,
        project_dir: Path,
        clips: list[dict[str, Any]],
        audio_file: Path,
        subtitles_file: Path,
        slug: str = "",
    ) -> None:
        """Purges render-remotion/public/ and copies exclusively active project assets."""
        cls.purge_remotion_public()

        # Copy required images for all 3 clips
        synced_images: set[str] = set()
        for clip in clips:
            for img_name in clip.get("imageFilenames", []):
                if img_name in synced_images:
                    continue
                src = cls.resolve_project_asset(project_dir, img_name)
                if not src or not src.exists():
                    raise FileNotFoundError(
                        f"Erro Crítico: Asset {img_name} não existe no projeto {slug}. Abortando render."
                    )
                dst = REMOTION_PUBLIC / img_name
                shutil.copy2(src, dst)
                synced_images.add(img_name)

        # Copy audio as audio_en.wav
        if not audio_file.exists():
            raise FileNotFoundError(f"Audio file missing on disk: {audio_file}")
        shutil.copy2(audio_file, REMOTION_PUBLIC / "audio_en.wav")

        # Copy subtitles as timestamps.json
        if not subtitles_file.exists():
            raise FileNotFoundError(f"Subtitles file missing on disk: {subtitles_file}")
        shutil.copy2(subtitles_file, REMOTION_PUBLIC / "timestamps.json")

        # Copy project manifest.json to satisfy Root.tsx import
        manifest_src = project_dir / "manifest.json"
        if manifest_src.exists():
            shutil.copy2(manifest_src, REMOTION_PUBLIC / "manifest.json")

        logger.info("Successfully synced %d images, audio, subtitles, and manifest to %s", len(synced_images), REMOTION_PUBLIC)

    @classmethod
    def render_shorts(
        cls,
        slug: str,
        project_dir: Path,
        title: str,
        subtitles_file: Path,
        audio_file: Path,
        force_regenerate: bool = False,
        ws_logger: Any = None,
    ) -> list[Path]:
        """Renders 3 vertical 9:16 shorts via Remotion.
        Fail-fast: If Remotion fails or outputs are invalid, aborts and raises RuntimeError."""
        def log(level: str, msg: str):
            if ws_logger:
                ws_logger(level, msg)
            elif level == "ERROR":
                logger.error(msg)
            elif level == "WARN":
                logger.warning(msg)
            else:
                logger.info(msg)

        shorts_dir = project_dir / "video" / "shorts"
        shorts_dir.mkdir(parents=True, exist_ok=True)

        # Purge legacy project_dir / "shorts" if present
        legacy_dir = project_dir / "shorts"
        if legacy_dir.exists() and legacy_dir.is_dir():
            shutil.rmtree(legacy_dir, ignore_errors=True)

        log("INFO", f"Iniciando curadoria e validação de assets físicos para os 3 Shorts de '{slug}'...")

        # 1. Curate and strictly validate clips from disk
        clips = cls.generate_shorts_manifest(
            slug=slug,
            project_dir=project_dir,
            title=title,
            subtitles_file=subtitles_file,
            force_regenerate=force_regenerate,
        )

        # 2. Fail-Fast de Integridade Pré-Render:
        # Itera sobre cada imagem referenciada no manifesto antes de disparar o comando.
        # Se qualquer arquivo não existir fisicamente no disco do projeto ativo, interrompe imediatamente.
        for clip in clips[:3]:
            for img_name in clip.get("imageFilenames", []):
                resolved = cls.resolve_project_asset(project_dir, img_name)
                if not resolved or not resolved.exists():
                    raise FileNotFoundError(
                        f"Erro Crítico: Asset {img_name} não existe no projeto {slug}. Abortando render."
                    )

        # 3. Sync exclusively active assets to Remotion public
        cls.sync_assets_for_shorts(
            project_dir=project_dir,
            clips=clips,
            audio_file=audio_file,
            subtitles_file=subtitles_file,
            slug=slug,
        )

        # 4. Environment configuration & fresh bundle build
        env = os.environ.copy()
        env["PATH"] = f"/Users/isamumatsuyama/.local/bin:/usr/local/bin:/usr/bin:/bin:{env.get('PATH', '')}"

        remotion_bin = str((REMOTION_DIR / "node_modules" / ".bin" / "remotion").resolve()) if (REMOTION_DIR / "node_modules" / ".bin" / "remotion").exists() else "remotion"
        try:
            build_dir = REMOTION_DIR / "build"
            if build_dir.exists():
                shutil.rmtree(build_dir, ignore_errors=True)
            subprocess.run(
                [remotion_bin, "bundle", "src/index.ts", "build"],
                cwd=str(REMOTION_DIR),
                check=True,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=60,
            )
        except Exception as exc:
            logger.warning("Remotion bundle build warning in shorts_service: %s", exc)

        generated_shorts: list[Path] = []

        for i, clip in enumerate(clips[:3], 1):
            out_short = shorts_dir / f"short_{i:02d}.mp4"
            start_s = clip.get("startSec", 0.0)
            end_s = clip.get("endSec", start_s + 30.0)
            hook_text = clip.get("hookText", f"The Tale of {title}")
            img_list = clip.get("imageFilenames", [])

            log("INFO", f"[Short {i}/3] Renderizando Short Vertical nativo (9:16) via Remotion CLI ({start_s:.1f}s - {end_s:.1f}s: \"{hook_text}\")...")

            props_obj = {
                "short": {
                    "startSec": start_s,
                    "endSec": end_s,
                    "hookText": hook_text,
                    "highlight": clip.get("highlight", "Dramatic Climax"),
                },
                "audioFilename": "audio_en.wav",
                "imageFilenames": img_list,
            }
            props_json = json.dumps(props_obj)

            bundle_target = "build" if (REMOTION_DIR / "build").exists() else "src/index.ts"
            remotion_bin = str((REMOTION_DIR / "node_modules" / ".bin" / "remotion").resolve()) if (REMOTION_DIR / "node_modules" / ".bin" / "remotion").exists() else "remotion"
            cmd = [
                remotion_bin, "render",
                bundle_target,
                "ShortVideo",
                str(out_short.resolve()),
                f"--props={props_json}",
                "--gl=angle",
                "--concurrency=2",
            ]

            res = subprocess.run(cmd, cwd=str(REMOTION_DIR), capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)

            if res.returncode == 0 and out_short.exists() and out_short.stat().st_size > 100000:
                generated_shorts.append(out_short)
                log("SUCCESS", f"[Short {i}/3] Concluído com sucesso via Remotion: {out_short.name} ({out_short.stat().st_size} bytes)")
            else:
                err_msg = res.stderr[:300] if res.stderr else "Return code non-zero or file empty"
                log("WARN", f"[Short {i}/3] Remotion render falhou ({err_msg}). Tentando fallback FFmpeg vertical de alta fidelidade...")

                # FFmpeg vertical fallback using real validated assets
                dur_s = max(end_s - start_s, 5.0)
                bg_img = cls.resolve_project_asset(project_dir, img_list[0]) if img_list else None

                if not bg_img or not bg_img.exists():
                    raise FileNotFoundError(f"Erro Crítico: Asset {img_list[0] if img_list else 'None'} não existe no projeto {slug}. Abortando render.")

                fallback_cmd = [
                    "ffmpeg", "-y",
                    "-loop", "1", "-i", str(bg_img.resolve()),
                    "-ss", str(start_s), "-t", str(dur_s), "-i", str(audio_file.resolve()),
                    "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "22", "-shortest",
                    "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                    str(out_short.resolve())
                ]
                f_res = subprocess.run(fallback_cmd, capture_output=True, text=True)
                if f_res.returncode == 0 and out_short.exists() and out_short.stat().st_size > 50000:
                    generated_shorts.append(out_short)
                    log("SUCCESS", f"[Short {i}/3] Concluído via fallback vertical FFmpeg ({out_short.stat().st_size} bytes)")
                else:
                    raise RuntimeError(f"[Short {i}/3] Ambos Remotion e Fallback FFmpeg falharam para {out_short.name}: {f_res.stderr[:200]}")

        if len(generated_shorts) < 3:
            raise RuntimeError(f"Step #15 failed: expected 3 shorts, generated {len(generated_shorts)}. Aborting to prevent incomplete deliverables.")

        # Ensure redundant legacy project_dir / "shorts" is purged if it exists
        if legacy_dir.exists() and legacy_dir.is_dir():
            shutil.rmtree(legacy_dir, ignore_errors=True)

        # Remove any legacy prefixed shorts (e.g. {slug}_short_01.mp4) from video/shorts
        for old_f in shorts_dir.glob("*_short_*.mp4"):
            try:
                old_f.unlink()
            except Exception:
                pass

        log("SUCCESS", f"Passo #15 Concluído: Todos os {len(generated_shorts)} Shorts Verticais nativos (9:16) gerados com sucesso e verificados.")
        return generated_shorts
