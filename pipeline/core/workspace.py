"""Proxy module to redsun.workspace for direct imports from motor-python."""
from redsun.workspace import (
    ProjectWorkspace,
    WorkspaceError,
    RUNTIME_ROOT,
    slugify_title,
    get_project_folder_name,
    resolve_project_dir,
)

__all__ = [
    "ProjectWorkspace",
    "WorkspaceError",
    "RUNTIME_ROOT",
    "slugify_title",
    "get_project_folder_name",
    "resolve_project_dir",
]
