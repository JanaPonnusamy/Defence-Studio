"""This module's own migration entry point.

Migration strategy: SQLAlchemy `create_all` against this module's own
SQLite database (storage/audio_module.db), run idempotently. For a
standalone single-deployment module with a small, additive schema this is
sufficient and keeps the project free of an external migration runner.
If the schema needs versioned incremental migrations in the future, an
Alembic environment can be dropped into this same scripts/ directory
without touching any other module.

Run: python scripts/migrate.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.init_db import init_db  # noqa: E402


def main() -> None:
    init_db()
    print("Audio Module schema is up to date.")


if __name__ == "__main__":
    main()
