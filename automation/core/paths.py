"""Repository path resolution.

The repository root is auto-detected (``git rev-parse``, falling back to walking up
for a ``.git`` directory). Every artifact location comes from configuration and is
resolved relative to that root. There are no absolute paths in the framework.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .config import NDFConfig
from .exceptions import PathError


def detect_repo_root(start: Path | None = None) -> Path:
    """Detect the repository root.

    Tries ``git rev-parse --show-toplevel`` first; if git is unavailable, walks up
    from ``start`` looking for a ``.git`` directory.
    """
    start = (start or Path.cwd()).resolve()
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=start,
            capture_output=True,
            text=True,
            check=True,
        )
        root = out.stdout.strip()
        if root:
            return Path(root).resolve()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        pass

    current = start
    for candidate in [current, *current.parents]:
        if (candidate / ".git").exists():
            return candidate
    raise PathError(f"Could not detect a repository root from {start}")


class PathResolver:
    """Resolves configured, repository-relative artifact paths to absolute paths."""

    def __init__(self, repo_root: Path, config: NDFConfig) -> None:
        self._root = repo_root.resolve()
        self._paths = config.paths

    @property
    def root(self) -> Path:
        return self._root

    def relative(self, absolute: Path) -> str:
        """Return a forward-slash, repository-relative string for display/logging."""
        try:
            return absolute.resolve().relative_to(self._root).as_posix()
        except ValueError:
            return absolute.as_posix()

    def _configured(self, key: str, fallback: str) -> Path:
        rel = self._paths.get(key, fallback)
        return (self._root / rel).resolve()

    # ---- named artifact locations ---------------------------------------
    @property
    def docs_dir(self) -> Path:
        return self._configured("docs_dir", "docs")

    @property
    def adr_dir(self) -> Path:
        return self._configured("adr_dir", "docs/ADR")

    @property
    def business_rules_dir(self) -> Path:
        return self._configured("business_rules_dir", "docs/BUSINESS_RULES")

    @property
    def ai_archive_dir(self) -> Path:
        return self._configured("ai_archive_dir", "docs/AI_ARCHIVE")

    @property
    def reports_dir(self) -> Path:
        return self._configured("reports_dir", "docs/REPORTS")

    @property
    def version_file(self) -> Path:
        return self._configured("version_file", "VERSION.md")

    @property
    def changelog_file(self) -> Path:
        return self._configured("changelog_file", "CHANGELOG.md")

    @property
    def release_notes_file(self) -> Path:
        return self._configured("release_notes_file", "RELEASE_NOTES.md")

    @property
    def index_file(self) -> Path:
        return self._configured("index_file", "docs/INDEX.md")

    @property
    def status_file(self) -> Path:
        return self._configured("status_file", "PROJECT_STATUS.md")
