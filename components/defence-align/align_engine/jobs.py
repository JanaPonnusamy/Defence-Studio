"""Small, focused persistent job store for alignment jobs.

Deliberately NOT a general workflow engine — it tracks exactly the lifecycle
this component needs:

  QUEUED -> PROCESSING -> COMPLETED
                       -> FAILED  (retryable)
  QUEUED/PROCESSING -> CANCELLED

Guarantees:
- Idempotent create: the same ``alignment_job_id`` with an identical request
  replays the existing job; a different request for the same id is a conflict.
- Immutable completed artifact: once COMPLETED, re-creating or re-processing
  returns the stored artifact and never recomputes or overwrites it.
- Safe retry: only a FAILED job can retry; it reuses the persisted, re-verified
  request (hashes are re-checked by the service) and never re-synthesizes
  audio (this component never synthesizes audio at all).
- Crash recovery: ``recover_interrupted`` flips any PROCESSING row left behind
  by a crash to FAILED (retryable) with a persisted error, so restart is safe.
- Persistent error details on every failure.

Execution is synchronous: ``process`` runs the alignment inline. A cooperative
cancel/engine-cancel seam exists for responsiveness, but the store never spawns
a second worker architecture.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from align_engine._hashing import digest
from align_engine.config import AlignConfig
from align_engine.contracts import AlignmentRequestV1
from align_engine.engines import EngineRegistry, default_registry
from align_engine.errors import (
    AlignError, AlignmentOutputInvalidError, JobConflictError, JobNotFoundError,
    JobStateError, RequestValidationError, UnsupportedCapabilityError,
)
from align_engine.service import run_alignment
from align_engine.store import ArtifactStore

SCHEMA_VERSION = 1
_TERMINAL = {"COMPLETED", "CANCELLED"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _connect(path: Path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def initialize_database(path: Path) -> None:
    with closing(_connect(path)) as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_info(
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1), version INTEGER NOT NULL);
            INSERT INTO schema_info(singleton, version) VALUES(1, 1)
                ON CONFLICT(singleton) DO NOTHING;
            CREATE TABLE IF NOT EXISTS alignment_jobs(
                alignment_job_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                request_sha256 TEXT NOT NULL,
                request_json TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN
                    ('QUEUED','PROCESSING','COMPLETED','FAILED','CANCELLED')),
                engine_name TEXT NOT NULL,
                audio_sha256 TEXT NOT NULL,
                transcript_sha256 TEXT NOT NULL,
                artifact_uri TEXT,
                artifact_sha256 TEXT,
                error_code TEXT NOT NULL DEFAULT '',
                error_detail TEXT NOT NULL DEFAULT '',
                attempt_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL);
            """
        )
        version = conn.execute("SELECT version FROM schema_info WHERE singleton=1").fetchone()[0]
        if version != SCHEMA_VERSION:
            raise AlignError(f"Unsupported alignment job schema version: {version}")


class JobStore:
    def __init__(self, config: AlignConfig, registry: EngineRegistry | None = None):
        self.config = config
        self.registry = registry or default_registry()
        self.store = ArtifactStore(config.storage_root)
        initialize_database(config.database_path)

    # -- creation / idempotency -------------------------------------------

    def create(self, request: AlignmentRequestV1) -> dict:
        request_json = request.model_dump_json()
        request_sha = digest(json.loads(request_json))
        now = _now()
        with closing(_connect(self.config.database_path)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM alignment_jobs WHERE alignment_job_id=?",
                (request.alignment_job_id,),
            ).fetchone()
            if existing is not None:
                if existing["request_sha256"] != request_sha:
                    raise JobConflictError(
                        "alignment_job_id already exists with a different request payload"
                    )
                conn.commit()
                return dict(existing)
            conn.execute(
                "INSERT INTO alignment_jobs(alignment_job_id, project_id, request_sha256, "
                "request_json, status, engine_name, audio_sha256, transcript_sha256, "
                "created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (request.alignment_job_id, request.project_id, request_sha, request_json,
                 "QUEUED", request.config.engine, request.audio_sha256,
                 request.transcript_sha256, now, now),
            )
            conn.commit()
            return dict(conn.execute("SELECT * FROM alignment_jobs WHERE alignment_job_id=?",
                                     (request.alignment_job_id,)).fetchone())

    # -- processing --------------------------------------------------------

    def process(self, alignment_job_id: str, *, cancel: threading.Event | None = None) -> dict:
        row = self._row(alignment_job_id)
        if row["status"] == "COMPLETED":
            return row                      # immutable: never recompute
        if row["status"] == "CANCELLED":
            return row                      # do not run a cancelled job
        if row["status"] == "PROCESSING":
            raise JobStateError("Job is already processing")
        request = AlignmentRequestV1.model_validate_json(row["request_json"])
        engine = self.registry.get(request.config.engine)
        self._set_status(alignment_job_id, "PROCESSING", bump_attempt=True)
        try:
            body = run_alignment(request, engine, self.config, cancel=cancel)
        except (RequestValidationError, AlignmentOutputInvalidError, UnsupportedCapabilityError, AlignError) as exc:
            detail = json.dumps(getattr(exc, "issues", None)) if getattr(exc, "issues", None) else str(exc)
            self._fail(alignment_job_id, getattr(exc, "code", "align_error"), detail)
            return self._row(alignment_job_id)
        # Write the immutable artifact, then mark completed in one step.
        uri, _ = self.store.write_output(alignment_job_id, body)
        self._complete(alignment_job_id, uri, body["artifact_sha256"])
        return self._row(alignment_job_id)

    def create_and_process(self, request: AlignmentRequestV1,
                           *, cancel: threading.Event | None = None) -> dict:
        created = self.create(request)
        if created["status"] == "COMPLETED":
            return created                  # idempotent replay of a finished job
        if created["status"] in ("CANCELLED", "PROCESSING"):
            return created
        return self.process(request.alignment_job_id, cancel=cancel)

    # -- lifecycle actions -------------------------------------------------

    def cancel(self, alignment_job_id: str) -> dict:
        row = self._row(alignment_job_id)
        if row["status"] in _TERMINAL:
            if row["status"] == "CANCELLED":
                return row
            raise JobStateError(f"Cannot cancel a job in state {row['status']}")
        self._set_status(alignment_job_id, "CANCELLED")
        return self._row(alignment_job_id)

    def retry(self, alignment_job_id: str, *, cancel: threading.Event | None = None) -> dict:
        row = self._row(alignment_job_id)
        if row["status"] == "COMPLETED":
            return row                      # nothing to retry; artifact is immutable
        if row["status"] != "FAILED":
            raise JobStateError(f"Only FAILED jobs can be retried (state is {row['status']})")
        self._set_status(alignment_job_id, "QUEUED", clear_error=True)
        return self.process(alignment_job_id, cancel=cancel)

    def recover_interrupted(self) -> int:
        """Flip PROCESSING rows (left by a crash) to FAILED(retryable). Returns
        the number recovered. Safe to call at every startup."""
        now = _now()
        with closing(_connect(self.config.database_path)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute("SELECT alignment_job_id FROM alignment_jobs WHERE status='PROCESSING'").fetchall()
            for r in rows:
                conn.execute(
                    "UPDATE alignment_jobs SET status='FAILED', error_code='interrupted', "
                    "error_detail='Job was processing when the process stopped; retry is safe.', "
                    "updated_at=? WHERE alignment_job_id=?",
                    (now, r["alignment_job_id"]),
                )
            conn.commit()
            return len(rows)

    # -- reads -------------------------------------------------------------

    def get(self, alignment_job_id: str) -> dict:
        return self._row(alignment_job_id)

    def get_artifact(self, alignment_job_id: str) -> dict:
        row = self._row(alignment_job_id)
        if row["status"] != "COMPLETED":
            raise JobStateError(f"No alignment artifact: job is {row['status']}")
        return self.store.read_output(alignment_job_id)

    # -- internals ---------------------------------------------------------

    def _row(self, alignment_job_id: str) -> dict:
        with closing(_connect(self.config.database_path)) as conn:
            row = conn.execute("SELECT * FROM alignment_jobs WHERE alignment_job_id=?",
                               (alignment_job_id,)).fetchone()
        if row is None:
            raise JobNotFoundError(f"Unknown alignment_job_id: {alignment_job_id!r}")
        return dict(row)

    def _set_status(self, alignment_job_id: str, status: str, *, bump_attempt: bool = False,
                    clear_error: bool = False) -> None:
        with closing(_connect(self.config.database_path)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            sql = "UPDATE alignment_jobs SET status=?, updated_at=?"
            params: list = [status, _now()]
            if bump_attempt:
                sql += ", attempt_count=attempt_count+1"
            if clear_error:
                sql += ", error_code='', error_detail=''"
            sql += " WHERE alignment_job_id=?"
            params.append(alignment_job_id)
            conn.execute(sql, params)
            conn.commit()

    def _fail(self, alignment_job_id: str, code: str, detail: str) -> None:
        with closing(_connect(self.config.database_path)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE alignment_jobs SET status='FAILED', error_code=?, error_detail=?, "
                "updated_at=? WHERE alignment_job_id=?",
                (code, detail[:8000], _now(), alignment_job_id),
            )
            conn.commit()

    def _complete(self, alignment_job_id: str, uri: str, artifact_sha: str) -> None:
        with closing(_connect(self.config.database_path)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE alignment_jobs SET status='COMPLETED', artifact_uri=?, artifact_sha256=?, "
                "error_code='', error_detail='', updated_at=? WHERE alignment_job_id=?",
                (uri, artifact_sha, _now(), alignment_job_id),
            )
            conn.commit()
