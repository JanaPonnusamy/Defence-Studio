"""Re-export of the single resolver in ``tools/project_paths.py``."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_RESOLVER = Path(__file__).resolve().parents[2] / "tools" / "project_paths.py"
_SPEC = importlib.util.spec_from_file_location("defence_era_project_paths", _RESOLVER)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError(f"missing path resolver: {_RESOLVER}")
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules["defence_era_project_paths"] = _MODULE
_SPEC.loader.exec_module(_MODULE)

PROJECT_ROOT = _MODULE.PROJECT_ROOT
ProjectPathError = _MODULE.ProjectPathError
configure_project_logging = _MODULE.configure_project_logging
contained_dir = _MODULE.contained_dir
ensure_dir = _MODULE.ensure_dir
is_inside = _MODULE.is_inside
logs_dir = _MODULE.logs_dir
pytest_temp_root = _MODULE.pytest_temp_root
release_task_directory = _MODULE.release_task_directory
require_inside_root = _MODULE.require_inside_root
runtime_temp_root = _MODULE.runtime_temp_root
session_workspace = _MODULE.session_workspace
task_directory = _MODULE.task_directory
temporary_directory = _MODULE.temporary_directory

__all__ = [
    "PROJECT_ROOT",
    "ProjectPathError",
    "configure_project_logging",
    "contained_dir",
    "ensure_dir",
    "is_inside",
    "logs_dir",
    "pytest_temp_root",
    "release_task_directory",
    "require_inside_root",
    "runtime_temp_root",
    "session_workspace",
    "task_directory",
    "temporary_directory",
]
