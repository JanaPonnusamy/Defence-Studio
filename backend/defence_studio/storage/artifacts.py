"""Path-safe, per-job artifact byte store for Defence Studio.

F1/F2 deliberately store only artifact *references* and a caller-supplied hash;
they never write or verify bytes. F3 is the first layer that acquires real
bytes, so it owns writing them under the mandated ``jobs/<job_id>/`` layout and
computing the SHA-256 over exactly what landed on disk.

Containment is the central guarantee: a relative URI may come from an approved
request or be derived from an external source (a redirected URL, a filename in a
page), so every path is resolved and checked to stay inside the configured
storage root. Writes are atomic (temp file + ``os.replace``) so an interrupted
acquisition never leaves a half-written file that a later SHA-256 would trust.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
# Leave headroom under Windows MAX_PATH (260). Long slugs from source URLs are
# the common offender (see QVault's acquisition storage note); we cap the
# visible filename and rely on the content-addressed source_id for uniqueness.
_MAX_FILENAME = 80


class StoragePathError(ValueError):
    """A URI would escape the job's storage directory or is malformed."""


@dataclass(frozen=True)
class StoredArtifact:
    uri: str
    path: Path
    sha256: str
    bytes: int
    media_type: str


def safe_filename(name: str, fallback_seed: str, extension: str) -> str:
    """Produce a short, filesystem-safe filename.

    `name` is untrusted (often a slug lifted from a source URL). We strip it to
    a safe stem, cap its length, and guarantee the extension. `fallback_seed`
    (e.g. the source URL) yields a deterministic stem when `name` is empty or
    entirely unsafe, so the result is never blank.
    """
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", (name or "").strip()).strip("-._")
    if not stem:
        stem = hashlib.sha256(fallback_seed.encode()).hexdigest()[:16]
    stem = stem[:_MAX_FILENAME]
    extension = extension if extension.startswith(".") else f".{extension}"
    return stem if stem.lower().endswith(extension.lower()) else stem + extension


MAX_WINDOWS_PATH = 240  # MAX_PATH 260 minus headroom for the .part temp suffix


class ArtifactStore:
    def __init__(self, storage_root: Path):
        self.root = Path(storage_root).resolve()

    def job_dir(self, job_id: str) -> Path:
        if not _SEGMENT.fullmatch(job_id):
            raise StoragePathError("job_id is not a safe path segment")
        return self.root / "jobs" / job_id

    def _resolve(self, job_id: str, relative_uri: str) -> tuple[str, Path]:
        """Validate ``jobs/<job_id>/...`` and resolve it inside the store.

        Rejects absolute paths, backslashes, empty/`.`/`..` segments, and
        anything whose resolved real path is not within this job's directory.
        """
        if "\\" in relative_uri:
            raise StoragePathError("Backslashes are not allowed in an artifact URI")
        pure = PurePosixPath(relative_uri)
        parts = pure.parts
        if pure.is_absolute() or len(parts) < 3:
            raise StoragePathError("Artifact URI must be a relative jobs/<job_id>/... path")
        if parts[0] != "jobs" or parts[1] != job_id:
            raise StoragePathError("Artifact URI must live under this job's folder")
        for segment in parts[2:]:
            if segment in ("", ".", "..") or not _SEGMENT.fullmatch(segment):
                raise StoragePathError(f"Unsafe artifact path segment: {segment!r}")
        job_dir = self.job_dir(job_id).resolve()
        target = (self.root / pure).resolve()
        # Defence in depth: even though every segment is validated above, confirm
        # the resolved real path is still contained (catches symlink surprises).
        if target != job_dir and job_dir not in target.parents:
            raise StoragePathError("Resolved artifact path escapes the job directory")
        return pure.as_posix(), target

    def write_bytes(
        self, job_id: str, relative_uri: str, data: bytes, media_type: str
    ) -> StoredArtifact:
        uri, target = self._resolve(job_id, relative_uri)
        if os.name == "nt" and len(str(target)) > MAX_WINDOWS_PATH:
            raise StoragePathError(
                f"Artifact path is {len(str(target))} characters, over the Windows limit of {MAX_WINDOWS_PATH}; "
                "use a shorter DEFENCE_STUDIO_STORAGE_ROOT")
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), suffix=".part")
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, target)  # atomic on same filesystem
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise
        digest = hashlib.sha256(data).hexdigest()
        return StoredArtifact(uri=uri, path=target, sha256=digest, bytes=len(data), media_type=media_type)

    def exists(self, job_id: str, relative_uri: str) -> bool:
        _, target = self._resolve(job_id, relative_uri)
        return target.is_file()

    def read_text(self, job_id: str, relative_uri: str, encoding: str = "utf-8") -> str:
        _, target = self._resolve(job_id, relative_uri)
        return target.read_text(encoding=encoding)

    def read_bytes(self, job_id: str, relative_uri: str, max_bytes: int | None = None) -> bytes:
        """Read artifact bytes, enforcing containment and an optional size cap.

        Rejects (without reading the whole file) any artifact larger than
        ``max_bytes`` so a hostile or oversized source cannot exhaust memory."""
        _, target = self._resolve(job_id, relative_uri)
        if max_bytes is not None and target.is_file() and target.stat().st_size > max_bytes:
            raise StoragePathError(
                f"Artifact exceeds the {max_bytes}-byte limit for this request"
            )
        return target.read_bytes()

    def sha256_of(self, job_id: str, relative_uri: str) -> str:
        _, target = self._resolve(job_id, relative_uri)
        digest = hashlib.sha256()
        with target.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
