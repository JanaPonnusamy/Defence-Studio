"""Module-owned storage helpers (spec §26).

All file references persisted in the DB are POSIX paths RELATIVE to
storage_root. `resolve_stored_path` refuses any reference that escapes the
storage root (path traversal guard, spec §32 "restrict filesystem access").
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from app.config import get_settings
from app.errors import VideoModuleError


def storage_root() -> Path:
    return Path(get_settings().storage_root).resolve()


def to_rel(path: Path) -> str:
    root = storage_root()
    p = Path(path).resolve()
    try:
        return p.relative_to(root).as_posix()
    except ValueError as exc:
        raise VideoModuleError("PATH_TRAVERSAL_REJECTED", "Path is outside module storage.") from exc


def resolve_stored_path(rel: str | None) -> Path | None:
    if not rel:
        return None
    root = storage_root()
    candidate = Path(rel)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise VideoModuleError("PATH_TRAVERSAL_REJECTED", "Stored path reference is not a safe relative path.")
    full = (root / candidate).resolve()
    if root != full and root not in full.parents:
        raise VideoModuleError("PATH_TRAVERSAL_REJECTED", "Stored path escapes module storage.")
    return full


def write_json_atomic(path: Path, data: dict | list) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False, default=str)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path
