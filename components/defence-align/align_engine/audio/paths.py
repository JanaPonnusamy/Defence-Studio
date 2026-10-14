"""Confine a client-supplied audio reference to the configured input root.

A request's ``audio_path`` is untrusted. It is treated as a relative POSIX
path under ``input_root``; absolute paths, backslashes, drive letters, empty
or ``.``/``..`` segments, and anything whose resolved real path escapes the
root (including via symlinks) are rejected. The API therefore never exposes
arbitrary filesystem access.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath

from align_engine.errors import UnsafePathError


def resolve_input_path(input_root: Path, relative_uri: str) -> Path:
    if not relative_uri or "\\" in relative_uri:
        raise UnsafePathError("audio_path must be a relative POSIX path (no backslashes)")
    pure = PurePosixPath(relative_uri)
    if pure.is_absolute() or (pure.drive if hasattr(pure, "drive") else False):
        raise UnsafePathError("audio_path must not be absolute")
    parts = pure.parts
    if not parts:
        raise UnsafePathError("audio_path is empty")
    for segment in parts:
        if segment in ("", ".", "..") or segment.endswith(":"):
            raise UnsafePathError(f"Unsafe audio_path segment: {segment!r}")
    root = Path(input_root).resolve()
    target = (root / pure).resolve()
    if target != root and root not in target.parents:
        raise UnsafePathError("audio_path escapes the configured input root")
    return target
