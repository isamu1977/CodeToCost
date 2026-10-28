#!/usr/bin/env python3
"""Red Sun Japan — Pipeline State Verifier.

Runs at the start of every session that touches the RSJ pipeline.
Fails LOUDLY if it detects drift between the live codebase/skills/memory
and PIPELINE_STATE.md (the single source of truth).

History
-------
- 2026-09-15: recalibrated after ComfyUI removal. ComfyUI is no longer in
  the live pipeline. References are allowed in:
    1. PIPELINE_STATE.md itself (canonical explanation)
    2. ARCHIVE / HISTORICAL documentation (record of past work)
    3. LEGACY completed project folders (record of past productions)
    4. Skills that explicitly mark ComfyUI as REMOVED / REMOVIDO
  Active mentions of ComfyUI in live code or active skills = drift.

Exit codes:
  0 = all checks passed
  1 = drift detected (must investigate before continuing)
  2 = missing PIPELINE_STATE.md (catastrophic — restore from git)

Usage:
  python verify_pipeline.py            # full check
  python verify_pipeline.py --quiet    # only print failures
  python verify_pipeline.py --strict   # also warn on comments referencing ComfyUI
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# Configuration — sourced from PIPELINE_STATE.md
# ─────────────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent
STATE_DOC = ROOT / "PIPELINE_STATE.md"


# ─────────────────────────────────────────────────────────────────────────────
# Allowlists — ComfyUI mentions in these are intentional / historical.
#
# Policy (2026-09-15): ComfyUI removed from the live pipeline. References
# survive only as historical record. Anything outside these allowlists is
# drift that needs to be cleaned.
# ─────────────────────────────────────────────────────────────────────────────

# Canonical docs that explicitly mention ComfyUI as removed/historical.
DOCS_ALLOWED = {
    ROOT / "PIPELINE_STATE.md",
    ROOT / "ARCHITECTURE_AUDIT.md",
    ROOT / "PRODUCTION_VALIDATION_REPORT.md",
    ROOT / "PRODUCTION_VALIDATION_REPORT_V2.md",
    ROOT / "README.md",
    ROOT / "docs" / "baseline-wire-production.md",
}

# Openspec archive folders (record of past proposals / changes).
ARCHIVE_DIRS = [
    ROOT / "openspec" / "changes" / "archive",
]

# Completed legacy projects (ComfyUI was used there historically).
LEGACY_PROJECT_DIRS = [
    ROOT / "projects" / "hoichi_the_earless",
    ROOT / "projects" / "botan-doro",
    ROOT / "projects" / "gemini-assistant-fixture-v0",
]

# Hermes skills where a ComfyUI mention means "active dependency" → drift if found.
HERMES_SKILLS_DIR = Path.home() / ".hermes" / "skills"

# Markers that indicate a skill / doc explicitly marks ComfyUI as REMOVED.
# A skill passing ANY of these is treated as compliant.
REMOVAL_MARKERS = (
    "REMOVED", "REMOVED ", "REMOVIDO",
    "no longer uses ComfyUI",
    "DEPRECATED", "DEPRECATED ",
    "LEGACY", "DESCONTINUADO", "DISCONTINUED",
)

# ─────────────────────────────────────────────────────────────────────────────
# Patterns — active-dependency detectors
# ─────────────────────────────────────────────────────────────────────────────

# Forbidden patterns that signal ComfyUI is being treated as active dependency.
# Comments and strings about "REMOVED", "DEPRECATED", "LEGACY", "DESCONTINUADO"
# are filtered out and are not flagged below — see helpers below.
ACTIVE_DEPENDENCY_PATTERNS = [
    # Hardcoded ComfyUI ports — only valid if filtered out as REMOVED/DEPRECATED
    (r"127\.0\.0\.1:8188", "ComfyUI port 8188 hardcoded"),
    (r"127\.0\.0\.1:8002", "ComfyUI port 8002 hardcoded"),
    # Imports / calls into the deleted module
    (r"\bfrom\s+comfy_client\s+import\b", "imports deleted comfy_client"),
    (r"\bimport\s+comfy_client\b", "imports deleted comfy_client"),
    # Auto-detect / phase calls
    (r"_detect_comfyui\s*\(", "calls ComfyUI auto-detect"),
    (r"phase_check_comfyui\s*\(", "calls phase_check_comfyui (removed)"),
    # Removed CLI flags
    (r"--comfy-auto-start", "uses removed --comfy-auto-start flag"),
    (r"--no-comfy-auto-start", "uses removed --no-comfy-auto-start flag"),
]

# Warnings (not failures): strings that MENTION ComfyUI but in deprecated context.
DEPRECATED_MENTION_PATTERNS = [
    (r"[Cc]omfyUI", "mentions ComfyUI"),
]


def _is_allowed_path(path: Path) -> bool:
    """Return True if ``path`` is in any allowlist (canonical doc / archive /
    legacy project)."""
    if path in DOCS_ALLOWED:
        return True
    for archive_dir in ARCHIVE_DIRS:
        try:
            path.relative_to(archive_dir)
            return True
        except ValueError:
            pass
    for legacy_dir in LEGACY_PROJECT_DIRS:
        try:
            path.relative_to(legacy_dir)
            return True
        except ValueError:
            pass
    return False


def _strip_marked_lines(content: str) -> str:
    """Drop any line whose raw text contains a removal/deprecation marker.

    These lines are explicitly declaring the code (or docstring / comment) as
    not-in-use, so they should NOT trigger a drift failure.
    """
    kept: list[str] = []
    for line in content.splitlines():
        if any(marker in line for marker in REMOVAL_MARKERS):
            continue
        kept.append(line)
    return "\n".join(kept)


# ─────────────────────────────────────────────────────────────────────────────
# Reporter
# ─────────────────────────────────────────────────────────────────────────────

class Reporter:
    def __init__(self, quiet: bool = False, strict: bool = False) -> None:
        self.quiet = quiet
        self.strict = strict
        self.failures: list[str] = []
        self.warnings: list[str] = []
        self.successes: list[str] = []

    def fail(self, msg: str) -> None:
        self.failures.append(msg)
        if not self.quiet:
            print(f"  ❌ FAIL: {msg}")

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)
        if not self.quiet:
            print(f"  ⚠️  WARN: {msg}")

    def ok(self, msg: str) -> None:
        self.successes.append(msg)
        if not self.quiet:
            print(f"  ✅ OK:   {msg}")

    def summary(self) -> int:
        print()
        print("=" * 70)
        if self.failures:
            print(f"❌ DRIFT DETECTED — {len(self.failures)} failure(s), "
                  f"{len(self.warnings)} warning(s)")
            print("=" * 70)
            print()
            print("Pipeline state has drifted from PIPELINE_STATE.md.")
            print("Read PIPELINE_STATE.md, fix the offending files, re-run this script.")
            return 1
        if self.warnings:
            print(f"⚠️  PASSED WITH WARNINGS — {len(self.warnings)} warning(s)")
            print("=" * 70)
            return 0
        print(f"✅ ALL CHECKS PASSED — {len(self.successes)} OK")
        print("=" * 70)
        return 0


# ─────────────────────────────────────────────────────────────────────────────
# Checks
# ─────────────────────────────────────────────────────────────────────────────

def check_state_doc_exists(r: Reporter) -> bool:
    """Catastrophic failure if the source-of-truth doc is missing."""
    if not STATE_DOC.exists():
        r.fail(f"PIPELINE_STATE.md missing at {STATE_DOC}")
        print("  This is catastrophic. Restore from git: "
              "git checkout HEAD -- PIPELINE_STATE.md")
        return False
    r.ok("PIPELINE_STATE.md exists")
    return True


def check_code_drift(r: Reporter) -> None:
    """Scan all .py files in motor-python/ for active ComfyUI dependencies.

    Excludes:
      - .venv/ (third-party deps)
      - Binary files
      - Files in DOCS_ALLOWED / ARCHIVE_DIRS / LEGACY_PROJECT_DIRS

    Lines that are explicitly marked REMOVED / DEPRECATED / DESCONTINUADO are
    filtered out before matching — declaring code as not-in-use is always OK.
    """
    SKIP_DIRS = {".venv", "__pycache__", "node_modules", ".git"}
    py_files: list[Path] = []
    for f in (ROOT / "motor-python").rglob("*.py"):
        rel = f.relative_to(ROOT)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        py_files.append(f)

    for f in py_files:
        if _is_allowed_path(f):
            r.ok(f"ALLOWED (historical): {f.relative_to(ROOT)}")
            continue
        try:
            content = f.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # Binary or non-UTF-8 file — skip silently
            continue
        except Exception as e:
            r.warn(f"Could not read {f}: {e}")
            continue

        # Drop lines that explicitly mark code as removed/deprecated
        active_content = _strip_marked_lines(content)

        # Also drop pure comment lines (prevents docstring mentions from firing)
        non_comment_lines = [
            line for line in active_content.splitlines()
            if not line.strip().startswith("#")
        ]
        active_content = "\n".join(non_comment_lines)

        for pattern, desc in ACTIVE_DEPENDENCY_PATTERNS:
            matches = re.findall(pattern, active_content)
            if matches:
                r.fail(
                    f"{f.relative_to(ROOT)}: {desc} "
                    f"({len(matches)} occurrence(s))"
                )

        if r.strict:
            for pattern, desc in DEPRECATED_MENTION_PATTERNS:
                if re.search(pattern, active_content):
                    r.warn(
                        f"{f.relative_to(ROOT)}: {desc} "
                        f"(verify it's marked removed)"
                    )


def check_skills_drift(r: Reporter) -> None:
    """Scan ~/.hermes/skills/ for any skill claiming ComfyUI as active method."""
    if not HERMES_SKILLS_DIR.exists():
        r.ok(f"No Hermes skills dir at {HERMES_SKILLS_DIR} (skipped)")
        return

    for skill_dir in HERMES_SKILLS_DIR.iterdir():
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue
        try:
            content = skill_md.read_text(encoding="utf-8")
        except Exception:
            continue

        # Skip skills that explicitly mark ComfyUI as REMOVED/DEPRECATED.
        if any(marker in content for marker in REMOVAL_MARKERS):
            r.ok(f"Skill (marked removed): {skill_dir.name}")
            continue

        # Drop marked lines before scanning
        active_content = _strip_marked_lines(content)

        # Look for active dependencies
        for pattern, desc in ACTIVE_DEPENDENCY_PATTERNS:
            if re.search(pattern, active_content):
                r.fail(f"Skill {skill_dir.name}/SKILL.md: {desc}")

        # Warnings for any mention
        if r.strict:
            for pattern, desc in DEPRECATED_MENTION_PATTERNS:
                if re.search(pattern, active_content):
                    r.warn(
                        f"Skill {skill_dir.name}/SKILL.md: {desc} "
                        f"(verify context is removed)"
                    )


def check_workspace_exists(r: Reporter) -> None:
    """Verify the gemini-assistant workspace directory exists."""
    workspace = (
        Path.home()
        / "Desktop"
        / "JingJing-stuff"
        / "jingjing-workspace"
        / "gemini-assistant"
    )
    if not workspace.exists():
        r.warn(
            f"gemini-assistant workspace missing at {workspace} "
            f"(may be normal if first session — Isamu to confirm)"
        )
        return
    n_jobs = sum(1 for _ in workspace.iterdir() if _.is_dir())
    r.ok(f"gemini-assistant workspace exists ({n_jobs} job(s))")


def check_storage_layout(r: Reporter) -> None:
    """Verify projects/ is the canonical storage root (not motor-python/storage/)."""
    canonical_runtime = Path("/Users/isamumatsuyama/Documents/red-sun-japan-runtime/projects")
    projects_dir = canonical_runtime if canonical_runtime.exists() else ROOT / "projects"
    legacy_storage = ROOT / "motor-python" / "storage"
    if not projects_dir.exists():
        r.fail(f"projects/ missing — RSJ output has nowhere to land")
    else:
        n_projects = sum(1 for _ in projects_dir.iterdir() if _.is_dir())
        r.ok(f"projects/ exists ({n_projects} project(s))")

    if legacy_storage.exists():
        # Legacy is OK as long as PIPELINE_STATE.md acknowledges it
        state_text = STATE_DOC.read_text(encoding="utf-8")
        if "LEGACY" in state_text and "motor-python/storage/" in state_text:
            r.ok("motor-python/storage/ exists but marked LEGACY in PIPELINE_STATE.md")
        else:
            r.fail(
                "motor-python/storage/ exists but is not marked LEGACY in "
                "PIPELINE_STATE.md (drift risk)"
            )


def check_run_pipeline_phase_order(r: Reporter) -> None:
    """Verify run_pipeline_complete.py uses gate_image_handoff, not generate_images auto."""
    runner = ROOT / "motor-python" / "run_pipeline_complete.py"
    if not runner.exists():
        r.fail(f"{runner.relative_to(ROOT)} missing")
        return
    content = runner.read_text(encoding="utf-8")
    if "gate_image_handoff" in content:
        r.ok("run_pipeline_complete.py references gate_image_handoff (new flow)")
    elif "phase_generate_images" in content or "phase_check_comfyui" in content:
        r.fail(
            "run_pipeline_complete.py still references deprecated phases "
            "(phase_check_comfyui / phase_generate_images) — drift detected"
        )
    else:
        r.warn("run_pipeline_complete.py has no obvious image-phase marker — manual review needed")


def check_gemini_contract_compliance(r: Reporter) -> None:
    """Enforce CONTRACT.md C1: every Gemini prompt must use the literal master prefix.

    Validates every project.json found under the gemini-assistant workspace:
      - prompt starts with the first 50 chars of MASTER_VISUAL_PROMPT_V1.txt
      - contains 'STYLE EXCLUSIONS:'
      - contains 'Image scale --ar 16:9'
      - contains 'SCENE:'
      - has none of the banned terms (ukiyo-e, sumi-e, woodblock)

    Run this BEFORE handing a job to the Gemini extension. Fails loudly on any
    violation. See CONTRACT.md §C1 and docs/GEMINI_IMAGE_PROMPT_RULES.md §7.
    """
    import json

    master_path = ROOT / "visual_bible" / "MASTER_VISUAL_PROMPT_V1.txt"
    if not master_path.exists():
        r.warn(
            f"{master_path.relative_to(ROOT)} missing — "
            "Gemini contract checks skipped"
        )
        return
    master_text = master_path.read_text(encoding="utf-8").strip()
    master_prefix = master_text[:50]  # enough to identify the literal copy

    workspace = (
        Path.home()
        / "Desktop"
        / "JingJing-stuff"
        / "jingjing-workspace"
        / "gemini-assistant"
    )
    if not workspace.exists():
        r.ok("No gemini-assistant workspace — nothing to validate")
        return

    project_files = list(workspace.rglob("project.json"))
    if not project_files:
        r.ok("gemini-assistant workspace has no project.json files yet")
        return

    banned = ["ukiyo-e", "ukiyo e", "sumi-e", "sumi e", "woodblock"]
    required = ["STYLE EXCLUSIONS:", "Image scale --ar 16:9", "SCENE:"]

    for project_path in project_files:
        try:
            proj = json.loads(project_path.read_text(encoding="utf-8"))
        except Exception as e:
            r.fail(f"{project_path}: cannot parse JSON ({e})")
            continue

        tasks = proj.get("tasks", [])
        if not tasks:
            continue

        n_violations = 0
        for task in tasks:
            prompt = task.get("prompt", "")
            tid = task.get("id", "?")

            if not prompt.startswith(master_prefix):
                r.fail(
                    f"{project_path.relative_to(Path.home())}::{tid}: "
                    f"prompt does not start with literal MASTER_VISUAL_PROMPT_V1.txt prefix"
                )
                n_violations += 1
                continue

            for token in required:
                if token not in prompt:
                    r.fail(
                        f"{project_path.relative_to(Path.home())}::{tid}: "
                        f"missing required token '{token}' (see CONTRACT.md §C1)"
                    )
                    n_violations += 1

            prompt_lower = prompt.lower()
            for b in banned:
                if b.lower() in prompt_lower:
                    r.fail(
                        f"{project_path.relative_to(Path.home())}::{tid}: "
                        f"contains banned term '{b}' (see CONTRACT.md §C1)"
                    )
                    n_violations += 1

        if n_violations == 0:
            r.ok(
                f"{project_path.relative_to(Path.home())}: "
                f"{len(tasks)} tasks pass CONTRACT.md §C1"
            )


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="Verify RSJ pipeline state alignment")
    ap.add_argument("--quiet", action="store_true", help="only print failures")
    ap.add_argument("--strict", action="store_true",
                    help="also warn on ComfyUI mentions in code/skills")
    args = ap.parse_args()

    r = Reporter(quiet=args.quiet, strict=args.strict)

    print("🔍 Red Sun Japan — Pipeline State Verification")
    print(f"   Root: {ROOT}")
    print(f"   Strict: {args.strict}  Quiet: {args.quiet}")
    print()

    if not check_state_doc_exists(r):
        # Catastrophic — bail
        print()
        print("=" * 70)
        print("❌ CATASTROPHIC: PIPELINE_STATE.md is missing.")
        print("=" * 70)
        return 2

    check_code_drift(r)
    check_skills_drift(r)
    check_workspace_exists(r)
    check_storage_layout(r)
    check_run_pipeline_phase_order(r)
    check_gemini_contract_compliance(r)

    return r.summary()


if __name__ == "__main__":
    sys.exit(main())
