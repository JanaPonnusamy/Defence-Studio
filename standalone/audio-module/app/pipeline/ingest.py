"""Ingestion: register an uploaded audio file as an AudioAsset.

Only direct audio upload is implemented with full support. URL/video
ingestion is a feature-flagged stub (disabled by default) because fetching
third-party video/audio requires a rights/licensing check that this
standalone module cannot perform automatically (see spec section 4.2 / 22).
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from app.config import get_settings
from app.errors import RightsMetadataRequiredError, UnsupportedMediaError
from app.logging_conf import get_logger

logger = get_logger("pipeline.ingest")

SUPPORTED_EXTENSIONS = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac"}


def sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def ingest_upload(reference_id: str, src_tmp_path: Path, original_filename: str) -> Path:
    """Copy an uploaded file into the module's own audio storage directory
    and return the stored path. Does not mutate the source temp file."""
    settings = get_settings()
    ext = Path(original_filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise UnsupportedMediaError(
            f"Unsupported audio file extension '{ext}'. Supported: {sorted(SUPPORTED_EXTENSIONS)}",
        )
    dest_dir = settings.audio_dir / reference_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / f"original{ext}"
    shutil.copyfile(src_tmp_path, dest_path)
    logger.info("Ingested upload for reference=%s -> %s", reference_id, dest_path)
    return dest_path


def ingest_url(reference_id: str, url: str, rights_metadata: dict | None) -> Path:
    """URL/video ingestion stub.

    Disabled by default via feature_video_url_ingest. Even when enabled,
    this function requires explicit rights_metadata to be supplied by the
    caller -- the module does not assume a technically-accessible URL is
    legally reusable (spec section 4.2 and 22).
    """
    settings = get_settings()
    if not settings.feature_video_url_ingest:
        raise UnsupportedMediaError(
            "URL/video ingestion is disabled in this standalone deployment "
            "(feature_video_url_ingest=False). Upload audio directly instead."
        )
    if not rights_metadata or not rights_metadata.get("authorized"):
        raise RightsMetadataRequiredError(
            "URL ingestion requires explicit rights_metadata with authorized=true."
        )
    raise NotImplementedError(
        "URL/video download is not implemented in this standalone build. "
        "Supply a direct audio upload instead."
    )
