"""Phase 1 / task 2.5: ProjectWorkspace.open_or_create contract.

Covers the three documented cases:
- Case A: new slug → `mode == "created_new"`, directory exists.
- Case B: matching slug → `mode == "open_existing"`, no duplicate work.
- Case C: identity conflict → raises `WorkspaceError`.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from redsun.workspace import ProjectWorkspace, WorkspaceError


def test_case_a_new_slug_created(tmp_path):
    ws, mode = ProjectWorkspace.open_or_create("wanyudo", projects_root=tmp_path)
    assert mode == "created_new"
    assert ws.root == tmp_path / "wanyudo"
    assert ws.root.exists()
    assert ws.checkpoints_dir.exists()
    assert ws.images_dir.exists()


def test_case_b_matching_slug_opened(tmp_path):
    # First create it.
    ws_a, mode_a = ProjectWorkspace.open_or_create("kappa", projects_root=tmp_path)
    assert mode_a == "created_new"

    # Second call must return open_existing with the same workspace.
    ws_b, mode_b = ProjectWorkspace.open_or_create("kappa", projects_root=tmp_path)
    assert mode_b == "open_existing"
    assert ws_b.root == ws_a.root
    assert ws_b.projects_root == tmp_path


def test_case_c_identity_conflict_raises(tmp_path):
    # Manually write a project.json with a different technical_slug under
    # the canonical path. This is the identity-collision case.
    ws_dir = tmp_path / "wanyudo"
    ws_dir.mkdir(parents=True)
    (ws_dir / "project.json").write_text(
        json.dumps({"technical_slug": "something-else"}), encoding="utf-8"
    )

    with pytest.raises(WorkspaceError, match="slug identity conflict"):
        ProjectWorkspace.open_or_create("wanyudo", projects_root=tmp_path)


def test_open_or_create_uses_slug_resolver_for_legacy_db(tmp_path):
    """If a legacy DB row matches the slug, mode == open_existing without materializing on disk."""

    # Build a resolver that says "this slug already exists in the DB".
    seen: list[str] = []

    def resolver(slug: str):
        seen.append(slug)
        return [{"technical_slug": slug, "title": "Legacy", "output_language": "en"}]

    ws, mode = ProjectWorkspace.open_or_create(
        "kappa",
        slug_resolver=resolver,
        projects_root=tmp_path,
    )
    assert mode == "open_existing"
    assert seen == ["kappa"]
    # No canonical directory should have been created (resolver signaled "already there").
    assert not ws.root.exists()


def test_open_or_create_with_resolver_no_match_creates_new(tmp_path):
    seen: list[str] = []

    def resolver(slug: str):
        seen.append(slug)
        return []  # no legacy match

    ws, mode = ProjectWorkspace.open_or_create(
        "newslug",
        slug_resolver=resolver,
        projects_root=tmp_path,
    )
    assert mode == "created_new"
    assert ws.root.exists()
    assert seen == ["newslug"]


def test_empty_slug_raises(tmp_path):
    with pytest.raises(WorkspaceError):
        ProjectWorkspace.open_or_create("", projects_root=tmp_path)
