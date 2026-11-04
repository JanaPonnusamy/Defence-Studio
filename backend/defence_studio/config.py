import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _contained(path: Path) -> Path:
    """Project-owned database and storage paths cannot leave the repository."""
    from defence_studio.project_paths import configure_project_logging, require_inside_root

    if os.environ.get("DEFENCE_STUDIO_STARTUP_DIAGNOSTICS") == "1":
        configure_project_logging()
    return require_inside_root(path)


def database_path() -> Path:
    configured = os.environ.get("DEFENCE_STUDIO_DB_PATH")
    path = Path(configured) if configured else PROJECT_ROOT / "data" / "defence_studio.sqlite3"
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return _contained(path)


def auth_secret() -> str:
    return os.environ.get("DEFENCE_STUDIO_AUTH_SECRET", "")


def storage_root() -> Path:
    """Filesystem root that physically holds per-job artifact bytes.

    Artifact URIs recorded in SQLite are relative references of the form
    ``jobs/<job_id>/...`` (see the F1 artifact contract); this root is the base
    those relative URIs resolve against, so a stored file lives at
    ``<storage_root>/jobs/<job_id>/...``. Kept separate from the SQLite path so
    bytes and the authoritative metadata DB can live on different volumes.
    """
    configured = os.environ.get("DEFENCE_STUDIO_STORAGE_PATH")
    path = Path(configured) if configured else PROJECT_ROOT / "data" / "storage"
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return _contained(path)
