"""Filesystem provider — abstracts all file I/O behind one role.

Routing every read/write through this provider keeps services free of direct I/O,
centralizes UTF-8 handling and newline policy, and makes dry-run/test doubles trivial.
"""

from __future__ import annotations

from pathlib import Path

from ..core.logging import get_logger


class FileSystemProvider:
    """Reads and writes text files within the repository."""

    def __init__(self, repo_root: Path) -> None:
        self._root = repo_root.resolve()
        self._log = get_logger("providers.filesystem")

    def _rel(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self._root).as_posix()
        except ValueError:
            return path.as_posix()

    def exists(self, path: Path) -> bool:
        return path.exists()

    def read_text(self, path: Path, default: str | None = None) -> str:
        if not path.exists():
            if default is not None:
                return default
            raise FileNotFoundError(path)
        return path.read_text(encoding="utf-8")

    def write_text(self, path: Path, content: str) -> str:
        """Write UTF-8 text with a trailing newline; create parents as needed.

        Returns the repository-relative path that was written.
        """
        self.ensure_dir(path.parent)
        if content and not content.endswith("\n"):
            content += "\n"
        path.write_text(content, encoding="utf-8", newline="\n")
        rel = self._rel(path)
        self._log.info("wrote %s", rel)
        return rel

    def ensure_dir(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)

    def glob(self, base: Path, pattern: str) -> list[Path]:
        if not base.exists():
            return []
        return sorted(p for p in base.glob(pattern) if p.is_file())

    def rglob(self, base: Path, pattern: str) -> list[Path]:
        if not base.exists():
            return []
        return sorted(p for p in base.rglob(pattern) if p.is_file())

    def list_dirs(self, base: Path) -> list[Path]:
        if not base.exists():
            return []
        return sorted(p for p in base.iterdir() if p.is_dir())

    def size_bytes(self, path: Path) -> int:
        return path.stat().st_size if path.exists() else 0
