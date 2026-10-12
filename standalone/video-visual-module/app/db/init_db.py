"""Schema management: Alembic migrations owned by this module (alembic/).

`init_db()` upgrades this module's own database to the latest revision. It is
idempotent and safe to run on every startup. There is no dependency on any
parent application's database or migration system.
"""
from __future__ import annotations

from alembic import command
from alembic.config import Config

from app.config import PROJECT_ROOT, get_settings
from app.db import models  # noqa: F401 -- register models on Base metadata


def alembic_config() -> Config:
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", get_settings().db_url)
    cfg.attributes["configure_logger"] = False
    return cfg


def init_db() -> None:
    get_settings().ensure_storage_dirs()
    command.upgrade(alembic_config(), "head")
