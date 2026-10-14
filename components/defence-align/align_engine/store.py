"""Path-safe artifact byte store for alignment outputs.

Independent of any Defence Studio store. Output artifacts live under
``<storage_root>/alignments/<job_id>/`` and are written atomically (temp file +
``os.replace``). A completed artifact is immutable: ``write_output`` refuses to
overwrite an existing file at the same path, so a later failed/retried run can
never clobber a previously completed artifact.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from align_engine.errors import AlignError

_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class ArtifactStore:
    def __init__(self, storage_root: Path):
        self.root = Path(storage_root).resolve()

    def _job_dir(self, job_id: str) -> Path:
        if not _SEGMENT.fullmatch(job_id):
            raise AlignError(f"Unsafe job id for storage: {job_id!r}")
        return self.root / "alignments" / job_id

    def output_uri(self, job_id: str) -> str:
        return f"alignments/{job_id}/alignment.json"

    def _resolve(self, uri: str) -> Path:
        target = (self.root / uri).resolve()
        if self.root not in target.parents and target != self.root:
            raise AlignError("Artifact path escapes the storage root")
        return target

    def write_output(self, job_id: str, body: dict) -> tuple[str, Path]:
        """Write the canonical output JSON immutably. Returns (uri, path).

        Raises if an artifact already exists for this job (immutability)."""
        job_dir = self._job_dir(job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        uri = self.output_uri(job_id)
        target = self._resolve(uri)
        if target.exists():
            raise AlignError(f"Refusing to overwrite an existing immutable artifact: {uri}")
        data = json.dumps(body, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
        fd, tmp_name = tempfile.mkstemp(dir=str(job_dir), suffix=".part")
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, target)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise
        return uri, target

    def read_output(self, job_id: str) -> dict:
        target = self._resolve(self.output_uri(job_id))
        if not target.is_file():
            raise AlignError(f"No stored artifact for job {job_id!r}")
        return json.loads(target.read_text(encoding="utf-8"))

    def exists(self, job_id: str) -> bool:
        return self._resolve(self.output_uri(job_id)).is_file()
