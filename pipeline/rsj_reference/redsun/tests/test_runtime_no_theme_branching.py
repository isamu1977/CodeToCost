"""Phase 12B: runtime hardcode audit.

This test enforces the rule "production runtime MUST NOT branch
on concrete theme names or slugs". It walks the runtime source
files of the `redsan/` package and asserts that no forbidden
branching construct appears.

Forbidden:
- `if slug == \"kappa\":` (or any concrete slug)
- `slug in {\"yuki_onna\", \"mujina\"}` (concrete entity in set)
- `title.lower().contains(\"yuki-onna\")` (concrete entity in title check)

Allowed:
- the catalog data file (`themes/_catalog_data.py`) contains
  theme literals as DATA.
- the `tests/` directory may contain theme names.
- docstrings and comments may mention theme names.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


# Slugs and entity names that MUST NOT appear as runtime branch keys.
# These are the entities that historically exist in this project
# (kappa, yuki_onna, mujina, taira_no_masakado, ronin_47, umibouzu,
# tsukumogami, onikiri). A new theme MUST NOT be added here; the
# whole point is that the runtime is generic.
FORBIDDEN_THEME_LITERALS = (
    "kappa",
    "yuki_onna",
    "mujina",
    "taira_no_masakado",
    "umibouzu",
    "ronin_47",
    "tsukumogami",
    "onikiri",
)


def _runtime_root() -> Path:
    return Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("literal", FORBIDDEN_THEME_LITERALS)
def test_runtime_no_branch_on_slug_equality(literal):
    """`if slug == \"<literal>\"` MUST NOT appear in runtime code."""
    runtime_root = _runtime_root()
    pattern = re.compile(rf"if\s+(self\.)?slug\s*==\s*[\"']{literal}[\"']")
    for path in runtime_root.rglob("*.py"):
        if "__pycache__" in path.parts or "tests" in path.parts:
            continue
        if path.name == "_catalog_data.py":
            continue
        text = path.read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            line_start = text.rfind("\n", 0, match.start()) + 1
            line_end = text.find("\n", match.end())
            line = text[line_start:line_end if line_end != -1 else None]
            # Allow docstrings/comments.
            stripped = line.lstrip()
            if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                continue
            pytest.fail(
                f"{path}: runtime branch on slug==\"{literal}\": {line.strip()!r}"
            )


@pytest.mark.parametrize("literal", FORBIDDEN_THEME_LITERALS)
def test_runtime_no_branch_on_slug_in_set(literal):
    """`slug in {\"<literal>\", ...}` MUST NOT appear in runtime code."""
    runtime_root = _runtime_root()
    pattern = re.compile(
        rf"slug\s+in\s*\{{[^\}}]*?[\"']{literal}[\"'][^\}}]*?\}}"
    )
    for path in runtime_root.rglob("*.py"):
        if "__pycache__" in path.parts or "tests" in path.parts:
            continue
        if path.name == "_catalog_data.py":
            continue
        text = path.read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            line_start = text.rfind("\n", 0, match.start()) + 1
            line_end = text.find("\n", match.end())
            line = text[line_start:line_end if line_end != -1 else None]
            stripped = line.lstrip()
            if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                continue
            pytest.fail(
                f"{path}: runtime `slug in {{...}}` contains literal "
                f"{literal!r}: {line.strip()!r}"
            )


def test_runtime_no_title_contains_concrete_theme():
    """`title.lower().contains(\"<literal>\")` MUST NOT appear in runtime."""
    runtime_root = _runtime_root()
    pattern = re.compile(
        r"title\.(?:lower|upper)\(\)\.(?:contains|__contains__|find)\s*\(\s*"
        rf"[\"']({ '|'.join(FORBIDDEN_THEME_LITERALS) })[\"']"
    )
    for path in runtime_root.rglob("*.py"):
        if "__pycache__" in path.parts or "tests" in path.parts:
            continue
        if path.name == "_catalog_data.py":
            continue
        text = path.read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            line_start = text.rfind("\n", 0, match.start()) + 1
            line_end = text.find("\n", match.end())
            line = text[line_start:line_end if line_end != -1 else None]
            stripped = line.lstrip()
            if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                continue
            pytest.fail(
                f"{path}: title check on concrete literal: {line.strip()!r}"
            )


def test_workspace_filename_templates_no_hardcoded_theme():
    """`workspace.py` MUST NOT contain a hardcoded theme literal in
    a filename template. Catalog data file is excluded.
    """
    runtime_root = _runtime_root()
    ws_path = runtime_root / "workspace.py"
    text = ws_path.read_text(encoding="utf-8")
    for literal in FORBIDDEN_THEME_LITERALS:
        # A hardcoded filename template would look like:
        #   f"{literal}_...mp4"
        #   "{literal}_...wav"
        #   "{literal}_...png"
        for pat in (
            rf'f?["\']{literal}_',
            rf'_{literal}\.mp4',
            rf'_{literal}\.wav',
            rf'_{literal}\.png',
        ):
            for match in re.finditer(pat, text):
                pytest.fail(
                    f"workspace.py contains hardcoded literal "
                    f"{literal!r} in filename template"
                )