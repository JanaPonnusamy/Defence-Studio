"""Configuration from environment variables (prefix RENDERER_). No .env loader dependency."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

MODULE_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_WIDTH, DEFAULT_HEIGHT, DEFAULT_FPS = 1920, 1080, 25


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass
class Settings:
    storage_root: Path = field(default_factory=lambda: Path(os.environ.get("RENDERER_STORAGE_ROOT", MODULE_ROOT / "storage")))
    ffmpeg: str | None = field(default_factory=lambda: os.environ.get("RENDERER_FFMPEG") or None)
    ffprobe: str | None = field(default_factory=lambda: os.environ.get("RENDERER_FFPROBE") or None)
    font_path: str | None = field(default_factory=lambda: os.environ.get("RENDERER_FONT_PATH") or None)
    transition_seconds: float = field(default_factory=lambda: _f("RENDERER_TRANSITION_SECONDS", 0.5))
    x264_preset: str = field(default_factory=lambda: os.environ.get("RENDERER_X264_PRESET", "veryfast"))
    x264_crf: int = field(default_factory=lambda: int(_f("RENDERER_X264_CRF", 20)))
    audio_bitrate: str = field(default_factory=lambda: os.environ.get("RENDERER_AUDIO_BITRATE", "192k"))
    workers: int = field(default_factory=lambda: max(1, int(_f("RENDERER_WORKERS", 1))))
    # narration-vs-timeline end tolerance (seconds): warn above, reject above
    duration_warn_s: float = 0.5
    duration_reject_s: float = 5.0
    # output validation tolerance vs. narration duration (seconds)
    output_duration_tolerance_s: float = 0.1

    @property
    def db_path(self) -> Path:
        return self.storage_root / "renderer.db"

    @property
    def renders_dir(self) -> Path:
        return self.storage_root / "renders"

    @property
    def packages_dir(self) -> Path:
        return self.storage_root / "packages"

    @property
    def logs_dir(self) -> Path:
        return self.storage_root / "logs"

    def ensure_dirs(self) -> None:
        for d in (self.storage_root, self.renders_dir, self.packages_dir, self.logs_dir):
            d.mkdir(parents=True, exist_ok=True)
