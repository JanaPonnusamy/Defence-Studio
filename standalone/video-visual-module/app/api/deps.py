from __future__ import annotations

import hmac
from typing import Generator

from fastapi import Header

from app.config import get_settings
from app.db.base import SessionLocal
from app.errors import VideoModuleError


def get_db() -> Generator:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Optional shared-secret protection (spec §32 'protect job endpoints').
    Disabled when VIDEO_MODULE_API_KEY is empty (default: localhost-only bind)."""
    expected = get_settings().api_key
    if not expected:
        return
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise VideoModuleError("UNAUTHORIZED", "Missing or invalid X-API-Key header.")
