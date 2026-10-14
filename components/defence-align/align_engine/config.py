"""Environment-based configuration with explicitly bounded, validated roots.

No client ever supplies a filesystem root; the operator configures them. All
request-supplied paths are resolved *against* ``input_root`` and confined to it
(see ``align_engine.audio.paths``), so the API never exposes arbitrary
filesystem access.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_MAX_AUDIO_BYTES = 512 * 1024 * 1024  # 512 MiB hard ceiling


@dataclass(frozen=True)
class AlignConfig:
    input_root: Path       # registered root holding caller-provided audio artifacts
    storage_root: Path     # where this component writes its own output artifacts
    database_path: Path    # SQLite job store
    max_audio_bytes: int = _DEFAULT_MAX_AUDIO_BYTES

    @staticmethod
    def from_env(base: Path | None = None) -> "AlignConfig":
        root = Path(base) if base else _package_data_root()
        input_root = _resolve_env("DEFENCE_ALIGN_INPUT_ROOT", root / "input")
        storage_root = _resolve_env("DEFENCE_ALIGN_STORAGE_ROOT", root / "storage")
        database_path = _resolve_env("DEFENCE_ALIGN_DB_PATH", root / "align.sqlite3")
        try:
            max_bytes = int(os.environ.get("DEFENCE_ALIGN_MAX_AUDIO_BYTES", _DEFAULT_MAX_AUDIO_BYTES))
        except ValueError:
            max_bytes = _DEFAULT_MAX_AUDIO_BYTES
        return AlignConfig(input_root=input_root, storage_root=storage_root,
                           database_path=database_path, max_audio_bytes=max(1, max_bytes))


def _resolve_env(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value).resolve() if value else Path(default).resolve()


def _package_data_root() -> Path:
    return Path(__file__).resolve().parents[1] / "data"
