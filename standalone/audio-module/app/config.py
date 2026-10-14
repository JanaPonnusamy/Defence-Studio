"""
Standalone configuration for the Voice Audio Lab / Audio Module.

All configuration is self-contained inside this project. No values are read
from, or written to, any file outside this project's own root.

Configuration precedence: environment variables > .env file > defaults below.
Secrets (e.g. cloud TTS API keys) are read ONLY from environment variables
and are never logged or exposed via any API response.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUDIO_MODULE_", env_file=".env", extra="ignore")

    # --- Storage ---
    storage_root: Path = PROJECT_ROOT / "storage"

    # --- Database ---
    database_url: str = f"sqlite:///{(PROJECT_ROOT / 'storage' / 'audio_module.db').as_posix()}"

    # --- Job worker ---
    job_worker_threads: int = 2
    job_poll_interval_seconds: float = 0.25

    # --- Alignment ---
    # "vad_proportional" is the honest fallback aligner shipped by default on
    # machines without a heavy forced-alignment model installed. See
    # app/pipeline/align.py for the full explanation of its limitations.
    alignment_provider: str = "vad_proportional"

    # --- TTS ---
    default_tts_provider: str = "mock-tts"
    enabled_tts_providers: list[str] = ["local-pyttsx3", "mock-tts"]

    # --- Limits ---
    max_upload_bytes: int = 200 * 1024 * 1024  # 200 MB
    max_tts_text_chars: int = 20000

    # --- Logging ---
    log_level: str = "INFO"

    # --- Feature flags ---
    feature_video_url_ingest: bool = False  # disabled by default: requires explicit rights review

    @property
    def references_dir(self) -> Path:
        return self.storage_root / "references"

    @property
    def audio_dir(self) -> Path:
        return self.storage_root / "audio"

    @property
    def transcripts_dir(self) -> Path:
        return self.storage_root / "transcripts"

    @property
    def alignment_dir(self) -> Path:
        return self.storage_root / "alignment"

    @property
    def analysis_dir(self) -> Path:
        return self.storage_root / "analysis"

    @property
    def profiles_dir(self) -> Path:
        return self.storage_root / "profiles"

    @property
    def generated_dir(self) -> Path:
        return self.storage_root / "generated"

    @property
    def waveforms_dir(self) -> Path:
        return self.storage_root / "waveforms"

    @property
    def jobs_dir(self) -> Path:
        return self.storage_root / "jobs"

    @property
    def logs_dir(self) -> Path:
        return self.storage_root / "logs"

    def ensure_storage_dirs(self) -> None:
        for d in (
            self.references_dir, self.audio_dir, self.transcripts_dir,
            self.alignment_dir, self.analysis_dir, self.profiles_dir,
            self.generated_dir, self.waveforms_dir, self.jobs_dir, self.logs_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_storage_dirs()
    return settings
