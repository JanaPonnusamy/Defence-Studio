"""Single path resolver for Defence Era.

Every project-owned path is derived from this file. Do not hardcode the
project root, and do not send project temp, log, or artifact files to the
process temporary directory, the user profile, or another drive.

The root is the directory that contains this ``tools`` package. It is not
read from a hardcoded drive letter.
"""
from __future__ import annotations

import atexit
import logging
import os
import re
import shutil
import uuid
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
_SESSIONS: list[Path] = []
_CLEANUP_REGISTERED = False


class ProjectPathError(ValueError):
    """A project-owned path resolved outside the canonical root."""


def is_inside(path: Path, root: Path | None = None) -> bool:
    base = (root or PROJECT_ROOT).resolve()
    try:
        Path(path).resolve().relative_to(base)
    except ValueError:
        return False
    return True


def require_inside_root(path: Path, *, label: str = "path") -> Path:
    """Return ``path`` resolved, or raise if it is not inside the project root.

    ``resolve`` follows symlinks and Windows junctions. A link inside the
    root whose target is outside is rejected. An unusable path string fails
    the same way; it is not replaced with another location.
    """
    try:
        resolved = Path(path).resolve()
    except (OSError, ValueError) as exc:
        raise ProjectPathError(
            f"{label} is not a usable path inside the project root {PROJECT_ROOT}: {path}"
        ) from exc
    if not is_inside(resolved):
        raise ProjectPathError(
            f"{label} must stay inside the project root {PROJECT_ROOT}: {resolved}"
        )
    return resolved


def _safe_component(name: str) -> str:
    cleaned = _SAFE.sub("-", (name or "").strip())[:60].strip(".-")
    if not cleaned or cleaned in {".", ".."}:
        raise ProjectPathError("path name must contain a letter or digit")
    return cleaned


def ensure_dir(path: Path) -> Path:
    resolved = require_inside_root(path)
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def contained_dir(*parts: str) -> Path:
    """A directory under the project root, created if needed.

    Each part is one path segment. ``..`` and separators are refused so a
    caller cannot climb out of the root.
    """
    if not parts:
        raise ProjectPathError("contained_dir needs at least one segment")
    safe = [_safe_component(part) for part in parts]
    return ensure_dir(PROJECT_ROOT.joinpath(*safe))


def logs_dir() -> Path:
    return contained_dir("logs")


def runtime_temp_root() -> Path:
    return contained_dir("tmp", "runtime")


def pytest_temp_root() -> Path:
    return contained_dir("tmp", "pytest")


def _register_cleanup() -> None:
    global _CLEANUP_REGISTERED
    if not _CLEANUP_REGISTERED:
        atexit.register(_cleanup_sessions)
        _CLEANUP_REGISTERED = True


def _cleanup_sessions() -> None:
    """Delete only the session directories this process created."""
    root = pytest_temp_root().resolve()
    for directory in list(_SESSIONS):
        resolved = directory.resolve()
        if resolved.parent == root and resolved.is_dir():
            shutil.rmtree(resolved, ignore_errors=True)


def task_directory(prefix: str) -> Path:
    """Unique directory under ``tmp/runtime``. Callers delete only this path."""
    root = runtime_temp_root()
    directory = root / f"{_safe_component(prefix)}-{os.getpid()}-{uuid.uuid4().hex[:12]}"
    directory.mkdir(parents=True, exist_ok=False)
    return directory.resolve()


def release_task_directory(directory: Path, *, ignore_errors: bool = False) -> None:
    """Delete one task directory. Refuses the temp root and any sibling."""
    resolved = Path(directory).resolve()
    root = runtime_temp_root().resolve()
    if resolved.parent != root:
        raise ProjectPathError(
            f"refusing to delete {resolved}; it is not a single task directory under {root}"
        )
    if not resolved.is_dir():
        return
    shutil.rmtree(resolved, ignore_errors=ignore_errors)


def session_workspace(prefix: str) -> Path:
    """Unique directory under ``tmp/pytest`` for this process.

    Removed at process exit. Cleanup never deletes a sibling session.
    """
    root = pytest_temp_root()
    directory = root / f"{_safe_component(prefix)}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    directory.mkdir(parents=True, exist_ok=False)
    resolved = directory.resolve()
    _SESSIONS.append(resolved)
    _register_cleanup()
    return resolved


@contextmanager
def temporary_directory(prefix: str):
    """Project-owned temporary directory. Cleanup deletes only this directory."""
    directory = task_directory(prefix)
    try:
        yield directory
    finally:
        release_task_directory(directory, ignore_errors=True)


def configure_project_logging(
    logger_name: str = "defence_studio",
    *,
    directory: Path | None = None,
    max_bytes: int = 5_000_000,
    backup_count: int = 5,
) -> Path:
    """Attach a rotating file handler under ``logs/`` (or ``directory``).

    Calling this twice for the same logger and file does not add a second
    handler. Backup files beyond ``backup_count`` are dropped by the handler.
    """
    if max_bytes < 1 or backup_count < 1:
        raise ProjectPathError("log rotation needs a positive size and backup count")
    log_dir = logs_dir() if directory is None else ensure_dir(directory)
    log_path = require_inside_root(log_dir / f"{_safe_component(logger_name)}.log")
    logger = logging.getLogger(logger_name)
    for handler in logger.handlers:
        existing = getattr(handler, "baseFilename", None)
        if isinstance(handler, RotatingFileHandler) and existing and Path(existing).resolve() == log_path:
            return log_path
    handler = RotatingFileHandler(
        log_path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger.addHandler(handler)
    if logger.level == logging.NOTSET:
        logger.setLevel(logging.INFO)
    return log_path


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
