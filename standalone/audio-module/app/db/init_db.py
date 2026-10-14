from __future__ import annotations

from app.db.base import Base, engine
from app.db import models  # noqa: F401  (ensure models are registered)


def init_db() -> None:
    """Create all tables if they do not exist.

    This project uses a lightweight create_all-based migration strategy for
    its SQLite database, plus an Alembic environment (see alembic/) for
    future incremental migrations. There is no dependency on any parent
    application's database or migration system.
    """
    Base.metadata.create_all(bind=engine)
