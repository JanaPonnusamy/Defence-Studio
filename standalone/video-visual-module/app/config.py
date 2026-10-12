"""
Standalone configuration for the Video Visual / Scene Composition Module.

All configuration is self-contained inside this project. No values are read
from, or written to, any location outside this project's own root (unless an
operator explicitly points VIDEO_MODULE_STORAGE_ROOT somewhere else).

Precedence: environment variables > <module root>/.env file > defaults below.
Secrets (the optional API key) are read only from the environment/.env, are
never logged and are never returned by any API response.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODULE_NAME = "video-visual-module"
MODULE_VERSION = "1.1.0"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="VIDEO_MODULE_", env_file=str(PROJECT_ROOT / ".env"), extra="ignore"
    )

    storage_root: Path = PROJECT_ROOT / "storage"
    # If empty, derived from storage_root (see `db_url`).
    database_url: str = ""

    host: str = "127.0.0.1"
    port: int = 8765

    job_worker_threads: int = 2

    # Default is the PLACEHOLDER provider (not AI) so the module works with no credentials/network.
    default_image_provider: str = "placeholder-procedural"
    enabled_image_providers: list[str] = ["placeholder-procedural", "mock-local", "openai-images"]
    generation_max_retries: int = 2          # NON-billable providers: retries for transient errors (timeout / 429 / 5xx)
    # BILLABLE providers: off by default. Even when raised, only errors where the provider demonstrably
    # did not process the request (connection never established, explicit 429) are retried; timeouts and
    # other ambiguous outcomes are never resent.
    billable_generation_max_retries: int = 0
    generation_retry_backoff_s: float = 0.2
    # When a REAL AI provider fails or is not configured, fall back to the placeholder provider
    # only if the caller asks for it or this is set. Fallback output is always labelled as fallback.
    allow_placeholder_fallback: bool = False

    # ---- optional OpenAI Images provider (openai-images). Never required.
    # Key read from OPENAI_API_KEY (or VIDEO_MODULE_OPENAI_API_KEY), env or module .env only.
    openai_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("VIDEO_MODULE_OPENAI_API_KEY", "OPENAI_API_KEY"))
    openai_image_model: str = "gpt-image-2"
    openai_base_url: str = "https://api.openai.com/v1"
    openai_timeout_s: float = 180.0

    # ---- previews (Phase 2): optional local decoders; never a network dependency
    video_previews_enabled: bool = True
    pdf_previews_enabled: bool = True

    # ---- optional base-map tiles for MAP assets. OFF by default (no network); the schematic
    # map is always the fallback. Template like https://tile.example.org/{z}/{x}/{y}.png
    basemap_tile_url: str = ""
    basemap_attribution: str = ""
    basemap_network_enabled: bool = False
    basemap_user_agent: str = "video-visual-module/1.1 (standalone; contact operator)"

    max_upload_bytes: int = 50 * 1024 * 1024  # 50 MB per uploaded file
    max_image_pixels: int = 60_000_000        # decompression-bomb guard
    min_image_side: int = 16
    max_image_side: int = 12_000
    max_metadata_json_bytes: int = 64 * 1024

    # AI image budget (spec §10/§24): soft per-project cap enforced before generation.
    default_ai_image_budget_usd: float = 5.00
    # Spec §10: 3-4 primary AI hook/hero images recommended per episode.
    default_max_ai_images_per_plan: int = 4
    hard_max_ai_images_per_plan: int = 12

    # Planner tunables (spec §12: narration-driven; fixed duration is only a fallback)
    max_shot_seconds: float = 6.5
    min_shot_seconds: float = 1.8
    words_per_minute: float = 150.0
    allow_unknown_rights_in_plan: bool = False

    # Optional shared-secret API key. When set, every endpoint except /health,
    # /docs, /openapi.json and the static UI requires header X-API-Key.
    api_key: str = ""

    log_level: str = "INFO"

    @field_validator("storage_root")
    @classmethod
    def _storage_root(cls, v: Path) -> Path:
        p = Path(v).expanduser()
        return p if p.is_absolute() else (PROJECT_ROOT / p).resolve()

    @field_validator("job_worker_threads")
    @classmethod
    def _workers(cls, v: int) -> int:
        return max(1, min(16, v))

    @property
    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(Path(self.storage_root) / 'video_visual_module.db').as_posix()}"

    def _d(self, name: str) -> Path:
        return Path(self.storage_root) / name

    @property
    def assets_dir(self) -> Path:
        return self._d("assets")

    @property
    def source_metadata_dir(self) -> Path:
        return self._d("source_metadata")

    @property
    def generated_dir(self) -> Path:
        return self._d("generated")

    @property
    def maps_dir(self) -> Path:
        return self._d("maps")

    @property
    def graphics_dir(self) -> Path:
        return self._d("graphics")

    @property
    def documents_dir(self) -> Path:
        return self._d("documents")

    @property
    def clips_dir(self) -> Path:
        return self._d("clips")

    @property
    def scenes_dir(self) -> Path:
        return self._d("scenes")

    @property
    def plans_dir(self) -> Path:
        return self._d("plans")

    @property
    def jobs_dir(self) -> Path:
        return self._d("jobs")

    @property
    def attribution_dir(self) -> Path:
        return self._d("attribution")

    @property
    def logs_dir(self) -> Path:
        return self._d("logs")

    def all_storage_dirs(self) -> list[Path]:
        return [
            self.assets_dir, self.source_metadata_dir, self.generated_dir,
            self.maps_dir, self.graphics_dir, self.documents_dir, self.clips_dir,
            self.scenes_dir, self.plans_dir, self.jobs_dir, self.attribution_dir,
            self.logs_dir,
        ]

    def ensure_storage_dirs(self) -> None:
        for d in self.all_storage_dirs():
            d.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_storage_dirs()
    return settings
