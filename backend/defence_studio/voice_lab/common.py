"""Shared Voice Lab helpers: errors, ids, time, canonical JSON, environment-based paths."""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from defence_studio.config import PROJECT_ROOT


class VoiceLabError(Exception):
    """Raised with an HTTP status and a JSON-serialisable detail."""

    def __init__(self, status_code: int, detail):
        super().__init__(detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False))
        self.status_code = status_code
        self.detail = detail


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def loads(text, default=None):
    return default if text in (None, "") else json.loads(text)


def venv_python() -> Path:
    """Python of the isolated inference environment (never the application's own)."""
    root = Path(os.environ.get("DEFENCE_STUDIO_VOICE_LAB_VENV") or (PROJECT_ROOT / ".venv-voicelab"))
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def models_root() -> Path:
    return Path(os.environ.get("DEFENCE_STUDIO_VOICE_LAB_MODELS") or (PROJECT_ROOT / "models" / "voice_lab"))


def hf_home() -> Path:
    return models_root() / "hf"


def models_config_path() -> Path:
    return Path(os.environ.get("DEFENCE_STUDIO_VOICE_LAB_MODELS_CONFIG")
                or (PROJECT_ROOT / "backend" / "config" / "voice_lab_models.json"))


def reserve_bytes() -> int:
    """Free space that must remain after any Voice Lab write (default 2 GiB)."""
    return int(os.environ.get("DEFENCE_STUDIO_VOICE_LAB_RESERVE_BYTES", 2 * 1024 ** 3))


def cloud_training_enabled() -> bool:
    return os.environ.get("DEFENCE_STUDIO_VOICE_LAB_CLOUD_TRAINING", "") == "1"
