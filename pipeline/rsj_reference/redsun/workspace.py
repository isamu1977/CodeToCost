"""ProjectWorkspace — single source of project-local paths.

Every project-local artifact MUST be resolved through a
ProjectWorkspace instance. No phase composes paths manually.

Canonical root is `projects/<technical_slug>/` under the repo root.
Legacy paths under `motor-python/storage/<id>/` and
`motor-python/storage/<slug>/` are read-only and probed once on
construction.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .exceptions import WorkspaceError


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RUNTIME_ROOT = Path(
    os.environ.get(
        "REDSUN_RUNTIME_ROOT",
        "/Users/isamumatsuyama/Documents/red-sun-japan-runtime/projects",
    )
)
_PROJECTS_ROOT = RUNTIME_ROOT
_MOTOR_STORAGE_ROOT = Path(__file__).resolve().parent.parent / "storage"

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def get_project_folder_name(project_id: int, slug: str) -> str:
    """Canonical folder name format: {id:03d}_{slug}."""
    return f"{project_id:03d}_{slug}"


def resolve_project_dir(
    slug_or_folder: str,
    project_id: Optional[int] = None,
    runtime_root: Optional[Path] = None,
) -> Path:
    """Resolve physical folder Path under runtime_root.

    Resolution order:
    1. If slug_or_folder starts with 3 digits and underscore (001_slug), return runtime_root / slug_or_folder.
    2. If project_id is provided, check if runtime_root / f"{project_id:03d}_{slug}" exists.
    3. Check on disk if any numbered folder exists matching f"[0-9][0-9][0-9]_{slug}".
    4. Fallback compatibility: check if unnumbered folder runtime_root / slug exists.
    5. If neither exists on disk (before creation):
       - If project_id is provided, return runtime_root / f"{project_id:03d}_{slug}".
       - Try looking up project_id from SQLite DB; if found, return runtime_root / f"{pid:03d}_{slug}".
       - Fallback: return runtime_root / slug.
    """
    root = runtime_root or _PROJECTS_ROOT
    s = str(slug_or_folder).strip()

    if re.match(r"^\d{3}_", s):
        return root / s

    clean_slug = s.strip("/\\")

    if project_id is not None:
        cand = root / get_project_folder_name(project_id, clean_slug)
        if cand.exists():
            return cand

    try:
        matches = sorted(root.glob(f"[0-9][0-9][0-9]_{clean_slug}"))
        for m in matches:
            if m.is_dir():
                return m
    except Exception:
        pass

    legacy = root / clean_slug
    if legacy.exists():
        return legacy

    if project_id is not None:
        return root / get_project_folder_name(project_id, clean_slug)

    try:
        from database import SessionLocal, VideoProject
        db = SessionLocal()
        row = db.query(VideoProject).filter(VideoProject.slug == clean_slug).first()
        if row and row.id is not None:
            pid = row.id
            db.close()
            return root / get_project_folder_name(pid, clean_slug)
        db.close()
    except Exception:
        pass

    return root / clean_slug


def slugify_title(title: str) -> str:
    """Generic title -> technical_slug derivation.

    Lowercase, strip non `[a-z0-9-]`, collapse dashes, trim leading/trailing
    dashes, cap at 80 chars.
    """
    if not title:
        raise WorkspaceError("title is required to derive technical_slug")
    s = _SLUG_STRIP.sub("-", title.lower()).strip("-")
    if not s:
        raise WorkspaceError(f"title {title!r} produces an empty slug")
    return s[:80]


@dataclass
class ProjectWorkspace:
    """Canonical path layout for a single project.

    Construction is purely declarative; call `create()` to materialize
    the directory tree. Properties are typed `Path`s and always
    resolve under `projects/<folder_name>/`.

    Note: not `frozen=True` because tests need to redirect
    `projects_root` to a tmp directory. The `technical_slug` field
    is NOT reassigned in production code; tests assert this via
    `FrozenInstanceError` when constructing a new field.
    """

    technical_slug: str
    projects_root: Path = field(default=_PROJECTS_ROOT)
    legacy_storage_root: Path = field(default=_MOTOR_STORAGE_ROOT)
    legacy_probe_done: bool = field(default=False, repr=False)
    _legacy_paths: dict = field(default_factory=dict, repr=False)
    project_id: Optional[int] = field(default=None)
    folder_name: Optional[str] = field(default=None)

    @property
    def root(self) -> Path:
        if self.folder_name:
            return self.projects_root / self.folder_name
        return resolve_project_dir(
            self.technical_slug,
            project_id=self.project_id,
            runtime_root=self.projects_root,
        )

    # ── top-level artifacts ────────────────────────────────────────────
    @property
    def project_json_path(self) -> Path:
        return self.root / "project.json"

    @property
    def script_path(self) -> Path:
        return self.root / "script.md"

    @property
    def script_reviewed_path(self) -> Path:
        return self.root / "script_reviewed.md"

    @property
    def narration_path(self) -> Path:
        return self.root / "narration.txt"

    @property
    def transcript_path(self) -> Path:
        return self.root / "transcript.json"

    @property
    def visual_plan_path(self) -> Path:
        return self.root / "visual_plan.json"

    @property
    def manifest_path(self) -> Path:
        return self.root / "manifest.json"

    @property
    def video_main_path(self) -> Path:
        return self.root / "video" / f"{self.technical_slug}_main.mp4"

    @property
    def video_final_path(self) -> Path:
        return self.root / "video" / f"{self.technical_slug}_final.mp4"

    @property
    def audio_main_path(self) -> Path:
        return self.root / "audio" / "main.wav"

    @property
    def text_dir(self) -> Path:
        return self.root / "text"

    @property
    def audio_dir(self) -> Path:
        return self.root / "audio"

    @property
    def audio_chunks_dir(self) -> Path:
        return self.audio_dir / "chunks"

    def audio_chunk_path(self, chunk_index: int) -> Path:
        return self.audio_chunks_dir / f"chunk_{chunk_index:03d}.wav"

    @property
    def images_dir(self) -> Path:
        return self.root / "images"

    @property
    def images_prompts_dir(self) -> Path:
        return self.images_dir / "prompts"

    @property
    def images_references_dir(self) -> Path:
        return self.images_dir / "references"

    @property
    def images_final_dir(self) -> Path:
        return self.images_dir / "final"

    @property
    def references_json_path(self) -> Path:
        return self.images_prompts_dir / "01_references.json"

    @property
    def final_scenes_json_path(self) -> Path:
        return self.images_prompts_dir / "02_final_scenes.json"

    def image_path(self, beat_index: int, seed: int) -> Path:
        return self.images_final_dir / f"scene_{beat_index:03d}_seed{seed}.png"

    @property
    def checkpoints_dir(self) -> Path:
        return self.root / "checkpoints"

    def checkpoint_path(self, phase: str) -> Path:
        return self.checkpoints_dir / f"{phase}.json"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def pipeline_log_path(self) -> Path:
        return self.logs_dir / "pipeline.log"

    @property
    def pipeline_summary_path(self) -> Path:
        return self.root / "pipeline_summary.json"

    @property
    def blog_dir(self) -> Path:
        return self.root / "blog"

    @property
    def shorts_dir(self) -> Path:
        return self.root / "video" / "shorts"

    @property
    def post_kit_dir(self) -> Path:
        return self.root / "post_kit"

    @property
    def dist_dir(self) -> Path:
        return self.root / "distribution"

    @property
    def distribution_dir(self) -> Path:
        return self.root / "distribution"

    @property
    def gemini_assistant_dir(self) -> Path:
        return self.root / "gemini-assistant"

    # ── Canonical Unified Gemini Assistant Structure ───────────────────────
    # gemini-assistant/
    # ├── project.json      # Phase B: Scenes with references: ["references/..."]
    # ├── job.json          # Phase B job state
    # ├── references/       # ONLY location for approved reference images
    # ├── output/           # ONLY location for final scene images
    # └── ref-job/          # Isolated subfolder for Phase A
    #     └── project.json  # Phase A: Reference generation contract

    @property
    def gemini_project_json(self) -> Path:
        """Phase B (Scenes) contract at gemini-assistant/project.json."""
        return self.gemini_assistant_dir / "project.json"

    @property
    def gemini_job_json(self) -> Path:
        """Phase B job state at gemini-assistant/job.json."""
        return self.gemini_assistant_dir / "job.json"

    @property
    def gemini_references_dir(self) -> Path:
        """Canonical location for approved reference images."""
        return self.gemini_assistant_dir / "references"

    @property
    def gemini_output_dir(self) -> Path:
        """Canonical location for final scene images."""
        return self.gemini_assistant_dir / "output"

    @property
    def gemini_ref_job_dir(self) -> Path:
        """Phase A isolated directory at gemini-assistant/ref-job/."""
        return self.gemini_assistant_dir / "ref-job"

    @property
    def gemini_ref_job_project_json(self) -> Path:
        """Phase A reference generation contract at gemini-assistant/ref-job/project.json."""
        return self.gemini_ref_job_dir / "project.json"

    @property
    def gemini_ref_job_job_json(self) -> Path:
        """Phase A job state at gemini-assistant/ref-job/job.json."""
        return self.gemini_ref_job_dir / "job.json"

    # Backward-compatible accessors
    @property
    def gemini_references_output_dir(self) -> Path:
        return self.gemini_references_dir

    @property
    def gemini_references_project_json(self) -> Path:
        return self.gemini_ref_job_project_json

    @property
    def gemini_references_job_json(self) -> Path:
        return self.gemini_ref_job_job_json

    @property
    def gemini_ref_gen_dir(self) -> Path:
        return self.gemini_ref_job_dir

    @property
    def gemini_ref_gen_output_dir(self) -> Path:
        return self.gemini_references_dir

    @property
    def gemini_ref_gen_references_dir(self) -> Path:
        return self.gemini_references_dir

    @property
    def gemini_ref_gen_project_json(self) -> Path:
        return self.gemini_ref_job_project_json

    @property
    def gemini_ref_gen_job_json(self) -> Path:
        return self.gemini_ref_job_job_json

    @property
    def gemini_scenes_dir(self) -> Path:
        return self.gemini_assistant_dir

    @property
    def gemini_scenes_references_dir(self) -> Path:
        return self.gemini_references_dir

    @property
    def gemini_scenes_output_dir(self) -> Path:
        return self.gemini_output_dir

    @property
    def gemini_scenes_project_json(self) -> Path:
        return self.gemini_project_json

    @property
    def gemini_scenes_job_json(self) -> Path:
        return self.gemini_job_json

    @property
    def video_dir(self) -> Path:
        return self.root / "video"

    @property
    def book_dir(self) -> Path:
        return self.root / "book"

    @property
    def book_chapter_path(self) -> Path:
        return self.book_dir / "chapter.md"

    @property
    def book_audiobook_wav_path(self) -> Path:
        return self.book_dir / "chapter_audiobook.wav"

    @property
    def book_audiobook_mp3_path(self) -> Path:
        return self.book_dir / "chapter_audiobook.mp3"

    # ── construction ──────────────────────────────────────────────────

    @classmethod
    def from_slug(
        cls,
        slug: str,
        project_id: Optional[int] = None,
        projects_root: Optional[Path] = None,
    ) -> "ProjectWorkspace":
        if not slug:
            raise WorkspaceError("slug is required")
        root_dir = projects_root or _PROJECTS_ROOT

        m = re.match(r"^(\d{3})_(.+)$", str(slug).strip())
        if m:
            inferred_id = int(m.group(1))
            pure_slug = m.group(2)
            return cls(
                technical_slug=pure_slug,
                project_id=project_id or inferred_id,
                folder_name=slug,
                projects_root=root_dir,
            )

        return cls(
            technical_slug=slug,
            project_id=project_id,
            projects_root=root_dir,
        )

    @classmethod
    def from_title(cls, title: str) -> "ProjectWorkspace":
        return cls(technical_slug=slugify_title(title))

    @classmethod
    def from_project_id(
        cls,
        project_id: int,
        slug_resolver: Optional[callable] = None,
        projects_root: Optional[Path] = None,
    ) -> "ProjectWorkspace":
        root_dir = projects_root or _PROJECTS_ROOT

        try:
            candidates = sorted(root_dir.glob(f"{project_id:03d}_*"))
            for cand in candidates:
                if cand.is_dir():
                    m = re.match(r"^\d{3}_(.+)$", cand.name)
                    pure_slug = m.group(1) if m else cand.name
                    return cls(
                        technical_slug=pure_slug,
                        project_id=project_id,
                        folder_name=cand.name,
                        projects_root=root_dir,
                    )
        except Exception:
            pass

        if slug_resolver is not None:
            slug = slug_resolver(project_id)
            return cls(
                technical_slug=slug,
                project_id=project_id,
                projects_root=root_dir,
            )

        try:
            from database import SessionLocal, VideoProject
            db = SessionLocal()
            row = db.query(VideoProject).filter(VideoProject.id == project_id).first()
            if row and row.slug:
                pure_slug = row.slug
                db.close()
                return cls(
                    technical_slug=pure_slug,
                    project_id=project_id,
                    projects_root=root_dir,
                )
            db.close()
        except Exception:
            pass

        raise WorkspaceError(
            f"from_project_id requires a slug_resolver or existing project for ID {project_id}"
        )

    @classmethod
    def from_legacy(cls, project_id: int, slug: str) -> "ProjectWorkspace":
        ws = cls(technical_slug=slug)
        ws._probe_legacy(project_id, slug)
        return ws

    def create(self) -> None:
        """Idempotent: never overwrites existing files."""
        for d in (
            self.root,
            self.text_dir,
            self.audio_dir,
            self.audio_chunks_dir,
            self.images_dir,
            self.images_prompts_dir,
            self.gemini_assistant_dir,
            self.gemini_references_dir,
            self.gemini_output_dir,
            self.gemini_ref_job_dir,
            self.post_kit_dir,
            self.video_dir,
            self.checkpoints_dir,
            self.logs_dir,
            self.blog_dir,
            self.shorts_dir,
            self.book_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)

        # Ensure canonical image tree materialization (symlinks or verified mirror)
        self.materialize_canonical_image_tree()

    def materialize_canonical_image_tree(self) -> dict[str, str]:
        """Ensures that projects/{slug}/images/final and projects/{slug}/images/references
        deterministically point to ../gemini-assistant/output and ../gemini-assistant/references.
        Removes stale empty directories or invalid symlinks.
        If symlinks fail or are unsupported, performs verified mirror copy so images/final/ is never empty."""
        import os
        import shutil

        images_dir = self.images_dir
        images_dir.mkdir(parents=True, exist_ok=True)

        target_output = self.gemini_assistant_dir / "output"
        target_refs = self.gemini_assistant_dir / "references"
        target_output.mkdir(parents=True, exist_ok=True)
        target_refs.mkdir(parents=True, exist_ok=True)

        # Clean circular symlinks if present (e.g. gemini-assistant/output/output)
        circular_output = target_output / "output"
        if circular_output.is_symlink():
            try:
                circular_output.unlink()
            except OSError:
                pass

        results = {}
        for link_dir, target_dir, rel_path, key in [
            (self.images_final_dir, target_output, "../gemini-assistant/output", "final"),
            (self.images_references_dir, target_refs, "../gemini-assistant/references", "references"),
        ]:
            # If link_dir is a symlink: verify target
            if link_dir.is_symlink():
                try:
                    target_str = os.readlink(str(link_dir))
                    if target_str == rel_path or Path(target_str).resolve() == target_dir.resolve():
                        results[key] = "symlink_ok"
                        continue
                    link_dir.unlink()
                except OSError:
                    link_dir.unlink()

            # If link_dir is a physical directory
            if link_dir.exists() and link_dir.is_dir() and not link_dir.is_symlink():
                files = [f for f in link_dir.iterdir() if not f.name.startswith(".")]
                if not files:
                    try:
                        shutil.rmtree(str(link_dir))
                    except Exception:
                        pass
                else:
                    for f in files:
                        dst = target_dir / f.name
                        if not dst.exists():
                            try:
                                shutil.copy2(str(f), str(dst))
                            except Exception:
                                pass
                    try:
                        shutil.rmtree(str(link_dir))
                    except Exception:
                        pass

            # Create symlink
            symlink_created = False
            try:
                os.symlink(rel_path, str(link_dir))
                symlink_created = True
                results[key] = "symlinked"
            except Exception as exc:
                logger.warning("Could not create symlink %s -> %s: %s", link_dir, rel_path, exc)

            if not symlink_created:
                # Fallback: verified mirror copy so images/final/ is never empty (0 bytes)
                link_dir.mkdir(parents=True, exist_ok=True)
                copied = 0
                for item in target_dir.iterdir():
                    if item.is_file() and not item.name.startswith("."):
                        dst_file = link_dir / item.name
                        if not dst_file.exists() or dst_file.stat().st_size != item.stat().st_size:
                            try:
                                shutil.copy2(str(item), str(dst_file))
                                copied += 1
                            except Exception:
                                pass
                results[key] = f"mirrored_{copied}_files"

        return results

    @classmethod
    def open_or_create(
        cls,
        slug: str,
        *,
        project_id: Optional[int] = None,
        slug_resolver: Optional[callable] = None,
        projects_root: Optional[Path] = None,
    ) -> tuple["ProjectWorkspace", str]:
        """Resolve the workspace for `slug` idempotently.

        Returns `(workspace, mode)` where mode is one of:
        - `"open_existing"` — a matching project was found in
          `projects/<slug>/` or in the legacy DB. The existing
          workspace is reused; no DB row is created.
        - `"created_new"` — no matching project existed. The
          canonical directory tree is materialized via `create()`
          and the caller is responsible for inserting the DB row.

        Raises:
            WorkspaceError: if the slug matches an existing workspace
                or DB row but the identity fields (title, language)
                conflict with the existing record.

        The `slug_resolver` is an optional callable `Optional[int] -> Optional[dict]`
        that maps a project id to a row dict with at least
        `{"technical_slug", "title", "output_language"}`. When the
        slug is absent from `projects/<slug>/`, the resolver is
        consulted to detect a legacy DB row that the slug collides
        with. When both `projects/<slug>/` and the DB row exist and
        match, `mode == "open_existing"`.
        """
        if not slug:
            raise WorkspaceError("slug is required for open_or_create")

        projects_root = projects_root or _PROJECTS_ROOT
        ws = cls.from_slug(slug, project_id=project_id, projects_root=projects_root)

        # Case B: the canonical workspace already exists on disk.
        # Treat the workspace as existing if `project.json` is present OR
        # if the canonical root already has the expected directory tree.
        canonical_project_json = ws.project_json_path
        if canonical_project_json.exists() or ws.root.exists():
            # Verify identity against the persisted project.json if any.
            if canonical_project_json.exists():
                try:
                    import json as _json

                    existing = _json.loads(
                        canonical_project_json.read_text(encoding="utf-8")
                    )
                except Exception:
                    existing = None
                # Identity check: a different slug on disk is a hard conflict.
                existing_slug = (existing or {}).get("technical_slug")
                if existing_slug and existing_slug != slug:
                    raise WorkspaceError(
                        f"slug identity conflict: projects/<{slug}>/ contains a "
                        f"different technical_slug={existing_slug!r}"
                    )
            return ws, "open_existing"

        # Case A or C: nothing on disk. Probe legacy DB if a resolver is given.
        if slug_resolver is not None:
            # The resolver is keyed by slug → row dict. We use a small
            # helper convention: the resolver is called with `None` and
            # must return all rows (or just the rows that match the slug).
            try:
                rows = slug_resolver(slug) or []
            except Exception:
                rows = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                row_slug = row.get("technical_slug") or row.get("slug")
                if row_slug != slug:
                    continue
                # Legacy DB row with the same slug exists. Reuse.
                return ws, "open_existing"

        # Case A: nothing on disk, no legacy match.
        ws.create()
        return ws, "created_new"

    # ── legacy fallback (read-only) ───────────────────────────────────

    def _probe_legacy(self, project_id: int, slug: str) -> None:
        if self.legacy_probe_done:
            return
        candidates = [
            self.legacy_storage_root / slug / "checkpoints",
            self.legacy_storage_root / str(project_id) / "checkpoints",
            self.legacy_storage_root / slug,
            self.legacy_storage_root / str(project_id),
        ]
        for c in candidates:
            self._legacy_paths[str(c)] = c if c.exists() else None
        self.legacy_probe_done = True

    def legacy_root(self, project_id: Optional[int] = None) -> Optional[Path]:
        if not self.legacy_probe_done:
            if project_id is None:
                raise WorkspaceError(
                    "legacy_root requires from_legacy(project_id, slug) or explicit probe"
                )
            self._probe_legacy(project_id, self.technical_slug)
        for key, val in self._legacy_paths.items():
            if val is None:
                continue
            p = Path(key)
            if p.name == "checkpoints":
                continue
            return val
        return None

    def legacy_audio(self, project_id: int) -> Optional[Path]:
        for slug_key in (self.technical_slug, str(project_id)):
            candidate = (
                self.legacy_storage_root / slug_key / f"audio_{self.technical_slug}.wav"
            )
            if candidate.exists():
                return candidate
        return None