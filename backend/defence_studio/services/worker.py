from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import Callable

from defence_studio.database import connect
from defence_studio.services.jobs import JobError, JobNotFound, JobValidationError

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,99}$")


def _utc(value: datetime | None = None) -> datetime:
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise ValueError("Worker timestamps must include a timezone")
    return value.astimezone(timezone.utc)


def _stamp(value: datetime | None = None) -> str:
    return _utc(value).isoformat(timespec="microseconds")


class WorkerError(Exception):
    pass


class ClaimLost(WorkerError):
    """The lease expired or another claim superseded this worker."""


class InvalidQueueTransition(JobError):
    status_code = 409

    pass


@dataclass(frozen=True)
class StageContext:
    job_id: str
    owner_id: str
    topic: str
    language: str
    stage_key: str
    claim_id: str
    worker_id: str
    attempt: int
    idempotency_key: str
    input_artifacts: tuple[dict, ...]


class WorkerService:
    """SQLite is authoritative; every mutating operation uses a write lock."""

    def __init__(self, database_file):
        self.database_file = database_file

    def queue(self, job_id: str, owner_id: str) -> dict:
        now = _stamp()
        with closing(connect(self.database_file)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            job = connection.execute(
                "SELECT * FROM workflow_jobs WHERE job_id = ? AND owner_id = ?", (job_id, owner_id)
            ).fetchone()
            if job is None:
                raise JobNotFound("Workflow job not found")
            if job["status"] == "QUEUED":
                connection.commit()
                return dict(job)
            if job["status"] == "RUNNING":
                active = connection.execute(
                    "SELECT 1 FROM worker_claims WHERE job_id = ? AND status = 'ACTIVE' LIMIT 1",
                    (job_id,),
                ).fetchone()
                if active:
                    connection.commit()
                    return dict(job)
                raise InvalidQueueTransition("A manually running F1 job cannot be dispatched")
            if job["status"] != "DRAFT":
                raise InvalidQueueTransition(f"Job in {job['status']} cannot be queued")
            unfinished = connection.execute(
                "SELECT COUNT(*) FROM workflow_stages WHERE job_id = ? AND status != 'PENDING'", (job_id,)
            ).fetchone()[0]
            if unfinished:
                raise InvalidQueueTransition("Only a new draft with pending stages can be queued")
            connection.execute(
                "UPDATE workflow_jobs SET status = 'QUEUED', queued_at = ?, updated_at = ? WHERE job_id = ?",
                (now, now, job_id),
            )
            connection.commit()
            return dict(connection.execute("SELECT * FROM workflow_jobs WHERE job_id = ?", (job_id,)).fetchone())

    def recover_expired(self, now: datetime | None = None) -> int:
        now = _utc(now)
        with closing(connect(self.database_file)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            count = self._recover_expired_tx(connection, now)
            connection.commit()
            return count

    def claim_next(
        self,
        worker_id: str,
        supported_stages: set[str] | tuple[str, ...],
        lease_seconds: float = 60,
        now: datetime | None = None,
    ) -> StageContext | None:
        if not worker_id or len(worker_id) > 200:
            raise ValueError("worker_id is required and must be at most 200 characters")
        if lease_seconds <= 0 or lease_seconds > 3600:
            raise ValueError("lease_seconds must be greater than zero and at most 3600")
        supported = set(supported_stages)
        instant = _utc(now)
        now_text = _stamp(instant)
        expiry = _stamp(instant + timedelta(seconds=lease_seconds))
        with closing(connect(self.database_file)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._recover_expired_tx(connection, instant)
            jobs = connection.execute(
                "SELECT * FROM workflow_jobs WHERE status = 'QUEUED' "
                "ORDER BY COALESCE(queued_at, created_at), created_at, job_id"
            ).fetchall()
            for job in jobs:
                stages = connection.execute(
                    "SELECT * FROM workflow_stages WHERE job_id = ? ORDER BY ordinal", (job["job_id"],)
                ).fetchall()
                candidate = self._next_stage(connection, job["job_id"], stages, supported, instant)
                if candidate is None:
                    continue
                attempt = candidate["attempt_count"] + 1
                claim_id = str(uuid.uuid4())
                stage_key = candidate["stage_key"]
                connection.execute(
                    "UPDATE workflow_stages SET status = 'RUNNING', attempt_count = ?, version = version + 1, "
                    "updated_at = ?, error = '', retry_after = NULL WHERE job_id = ? AND stage_key = ?",
                    (attempt, now_text, job["job_id"], stage_key),
                )
                connection.execute(
                    "INSERT INTO worker_claims(claim_id, job_id, stage_key, worker_id, attempt, status, "
                    "lease_expires_at, created_at, heartbeat_at) VALUES(?, ?, ?, ?, ?, 'ACTIVE', ?, ?, ?)",
                    (claim_id, job["job_id"], stage_key, worker_id, attempt, expiry, now_text, now_text),
                )
                connection.execute(
                    "INSERT INTO workflow_transitions(job_id, stage_key, from_status, to_status, stage_version, occurred_at) "
                    "SELECT job_id, stage_key, ?, 'RUNNING', version, ? FROM workflow_stages "
                    "WHERE job_id = ? AND stage_key = ?",
                    (candidate["status"], now_text, job["job_id"], stage_key),
                )
                connection.execute(
                    "UPDATE workflow_jobs SET status = 'RUNNING', updated_at = ? WHERE job_id = ?",
                    (now_text, job["job_id"]),
                )
                inputs = connection.execute(
                    "SELECT a.* FROM workflow_artifacts a JOIN workflow_stages s "
                    "ON s.job_id = a.job_id AND s.stage_key = a.stage_key "
                    "WHERE a.job_id = ? AND s.ordinal < ? AND a.version = ("
                    "SELECT MAX(latest.version) FROM workflow_artifacts latest "
                    "JOIN workflow_stages latest_stage ON latest_stage.job_id = latest.job_id "
                    "AND latest_stage.stage_key = latest.stage_key "
                    "WHERE latest.job_id = a.job_id AND latest.artifact_type = a.artifact_type "
                    "AND latest_stage.ordinal < ?) "
                    "ORDER BY s.ordinal, a.artifact_type",
                    (job["job_id"], candidate["ordinal"], candidate["ordinal"]),
                ).fetchall()
                artifacts = tuple(dict(row) for row in inputs)
                identity = [
                    {"artifact_id": row["artifact_id"], "version": row["version"], "sha256": row["sha256"]}
                    for row in artifacts
                ]
                idem = hashlib.sha256(
                    json.dumps([job["job_id"], stage_key, identity], sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest()
                connection.commit()
                return StageContext(
                    job_id=job["job_id"], owner_id=job["owner_id"], topic=job["topic"],
                    language=job["language"], stage_key=stage_key, claim_id=claim_id,
                    worker_id=worker_id, attempt=attempt, idempotency_key=idem,
                    input_artifacts=artifacts,
                )
            connection.commit()
            return None

    def heartbeat(
        self, context: StageContext, lease_seconds: float = 60, now: datetime | None = None
    ) -> None:
        instant = _utc(now)
        now_text = _stamp(instant)
        expiry = _stamp(instant + timedelta(seconds=lease_seconds))
        with closing(connect(self.database_file)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            claim = self._active_claim(connection, context, instant)
            if claim is None:
                raise ClaimLost("Worker claim is missing, expired, or no longer owned")
            connection.execute(
                "UPDATE worker_claims SET heartbeat_at = ?, lease_expires_at = ? WHERE claim_id = ?",
                (now_text, expiry, context.claim_id),
            )
            connection.commit()

    def complete(self, context: StageContext, artifacts: list[dict] | None = None,
                 now: datetime | None = None) -> None:
        instant = _utc(now)
        now_text = _stamp(instant)
        outputs = [self._validate_artifact(context, value) for value in (artifacts or [])]
        with closing(connect(self.database_file)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            if self._active_claim(connection, context, instant) is None:
                raise ClaimLost("Expired or superseded worker cannot commit stage results")
            stage = connection.execute(
                "SELECT * FROM workflow_stages WHERE job_id = ? AND stage_key = ?",
                (context.job_id, context.stage_key),
            ).fetchone()
            if stage is None or stage["status"] != "RUNNING":
                raise ClaimLost("Claim no longer owns a running stage")
            for output in outputs:
                version = connection.execute(
                    "SELECT COALESCE(MAX(version), 0) + 1 FROM workflow_artifacts "
                    "WHERE job_id = ? AND artifact_type = ?",
                    (context.job_id, output["artifact_type"]),
                ).fetchone()[0]
                connection.execute(
                    "INSERT INTO workflow_artifacts(artifact_id, job_id, stage_key, artifact_type, version, "
                    "uri, sha256, schema_version, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (str(uuid.uuid4()), context.job_id, context.stage_key, output["artifact_type"],
                     version, output["uri"], output["sha256"], output["schema_version"], now_text),
                )
            connection.execute(
                "UPDATE workflow_stages SET status = 'COMPLETED', version = version + 1, updated_at = ?, "
                "error = '', retry_after = NULL WHERE job_id = ? AND stage_key = ?",
                (now_text, context.job_id, context.stage_key),
            )
            connection.execute(
                "INSERT INTO workflow_transitions(job_id, stage_key, from_status, to_status, stage_version, occurred_at) "
                "SELECT job_id, stage_key, 'RUNNING', 'COMPLETED', version, ? FROM workflow_stages "
                "WHERE job_id = ? AND stage_key = ?",
                (now_text, context.job_id, context.stage_key),
            )
            connection.execute(
                "UPDATE worker_claims SET status = 'COMPLETED', finished_at = ? WHERE claim_id = ?",
                (now_text, context.claim_id),
            )
            remaining = connection.execute(
                "SELECT COUNT(*) FROM workflow_stages WHERE job_id = ? AND status NOT IN ('COMPLETED', 'SKIPPED')",
                (context.job_id,),
            ).fetchone()[0]
            job_status = "QUEUED" if remaining else "COMPLETED"
            if context.stage_key == "extraction_validation_normalization_deduplication" and any(
                output["artifact_type"] == "analysis_result" for output in outputs
            ):
                # Enter the existing human stage through valid transitions;
                # never let an unattended worker run through review.
                review_key = "evidence_analysis_and_review"
                review_stage = connection.execute(
                    "SELECT status FROM workflow_stages WHERE job_id=? AND stage_key=?", (context.job_id, review_key)
                ).fetchone()
                if review_stage and review_stage[0] == "PENDING":
                    for before, after in (("PENDING", "RUNNING"), ("RUNNING", "AWAITING_REVIEW")):
                        connection.execute("UPDATE workflow_stages SET status=?,version=version+1,updated_at=? WHERE job_id=? AND stage_key=?",
                                           (after, now_text, context.job_id, review_key))
                        connection.execute("INSERT INTO workflow_transitions(job_id,stage_key,from_status,to_status,stage_version,occurred_at) "
                                           "SELECT job_id,stage_key,?,?,version,? FROM workflow_stages WHERE job_id=? AND stage_key=?",
                                           (before, after, now_text, context.job_id, review_key))
                    job_status = "AWAITING_REVIEW"
            connection.execute(
                "UPDATE workflow_jobs SET status = ?, queued_at = CASE WHEN ? = 'QUEUED' THEN ? ELSE queued_at END, "
                "updated_at = ? WHERE job_id = ?",
                (job_status, job_status, now_text, now_text, context.job_id),
            )
            connection.commit()

    def fail(self, context: StageContext, error: str, now: datetime | None = None) -> None:
        instant = _utc(now)
        now_text = _stamp(instant)
        detail = (error or "Stage handler failed").strip()[:4000]
        with closing(connect(self.database_file)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            claim = self._active_claim(connection, context, instant)
            if claim is None:
                raise ClaimLost("Expired or superseded worker cannot record stage failure")
            self._fail_claim_tx(connection, claim, detail, instant)
            connection.commit()

    def _recover_expired_tx(self, connection, now: datetime) -> int:
        now_text = _stamp(now)
        expired = connection.execute(
            "SELECT * FROM worker_claims WHERE status = 'ACTIVE' AND lease_expires_at <= ? "
            "ORDER BY lease_expires_at", (now_text,)
        ).fetchall()
        for claim in expired:
            stage = connection.execute(
                "SELECT * FROM workflow_stages WHERE job_id = ? AND stage_key = ?",
                (claim["job_id"], claim["stage_key"]),
            ).fetchone()
            connection.execute(
                "UPDATE worker_claims SET status = 'EXPIRED', finished_at = ?, failure_detail = ? "
                "WHERE claim_id = ? AND status = 'ACTIVE'",
                (now_text, "Worker lease expired before result commit", claim["claim_id"]),
            )
            if stage is None or stage["status"] != "RUNNING":
                continue
            self._mark_stage_failed_tx(
                connection, claim, stage, "Worker lease expired before result commit", now
            )
        return len(expired)

    def _fail_claim_tx(self, connection, claim, detail: str, now: datetime) -> None:
        now_text = _stamp(now)
        stage = connection.execute(
            "SELECT * FROM workflow_stages WHERE job_id = ? AND stage_key = ?",
            (claim["job_id"], claim["stage_key"]),
        ).fetchone()
        if stage is None or stage["status"] != "RUNNING":
            raise ClaimLost("Claim no longer owns a running stage")
        connection.execute(
            "UPDATE worker_claims SET status = 'FAILED', finished_at = ?, failure_detail = ? WHERE claim_id = ?",
            (now_text, detail, claim["claim_id"]),
        )
        self._mark_stage_failed_tx(connection, claim, stage, detail, now)

    @staticmethod
    def _mark_stage_failed_tx(connection, claim, stage, detail: str, now: datetime) -> None:
        now_text = _stamp(now)
        attempts_left = stage["attempt_count"] < stage["max_attempts"]
        delay = min(2 ** max(claim["attempt"] - 1, 0), 60)
        retry_at = _stamp(now + timedelta(seconds=delay)) if attempts_left else None
        connection.execute(
            "UPDATE workflow_stages SET status = 'FAILED', version = version + 1, updated_at = ?, error = ?, "
            "retry_after = ? WHERE job_id = ? AND stage_key = ?",
            (now_text, detail, retry_at, claim["job_id"], claim["stage_key"]),
        )
        connection.execute(
            "INSERT INTO workflow_transitions(job_id, stage_key, from_status, to_status, stage_version, occurred_at, error) "
            "SELECT job_id, stage_key, 'RUNNING', 'FAILED', version, ?, ? FROM workflow_stages "
            "WHERE job_id = ? AND stage_key = ?",
            (now_text, detail, claim["job_id"], claim["stage_key"]),
        )
        connection.execute(
            "UPDATE workflow_jobs SET status = ?, updated_at = ? WHERE job_id = ?",
            ("QUEUED" if attempts_left else "FAILED", now_text, claim["job_id"]),
        )

    @staticmethod
    def _active_claim(connection, context: StageContext, now: datetime):
        claim = connection.execute(
            "SELECT * FROM worker_claims WHERE claim_id = ? AND job_id = ? AND stage_key = ? "
            "AND worker_id = ? AND status = 'ACTIVE'",
            (context.claim_id, context.job_id, context.stage_key, context.worker_id),
        ).fetchone()
        if claim is None or claim["lease_expires_at"] <= _stamp(now):
            return None
        return claim

    @staticmethod
    def _next_stage(connection, job_id: str, stages, supported: set[str], now: datetime):
        previous_done = True
        for stage in stages:
            if stage["status"] in {"COMPLETED", "SKIPPED"}:
                previous_done = True
                continue
            if stage["status"] == "AWAITING_REVIEW":
                return None
            if stage["status"] == "RUNNING":
                return None
            if stage["status"] not in {"PENDING", "FAILED"}:
                return None
            if not previous_done or stage["attempt_count"] >= stage["max_attempts"]:
                return None
            if stage["retry_after"] and stage["retry_after"] > _stamp(now):
                return None
            # Topic search acquires the discovery result itself. An explicit
            # acquisition_request, when one already exists, is the candidate
            # subset; otherwise the acquisition handler builds targets from
            # the discovered URLs. Evidence review stays the human gate.
            return stage if stage["stage_key"] in supported else None
        return None

    @staticmethod
    def _validate_artifact(context: StageContext, value: dict) -> dict:
        try:
            artifact_type = value["artifact_type"]
            uri = value["uri"]
            digest = value["sha256"]
            schema_version = value["schema_version"]
        except (KeyError, TypeError):
            raise JobValidationError("Worker artifact needs artifact_type, uri, sha256 and schema_version") from None
        if not _NAME.fullmatch(artifact_type or "") or not _NAME.fullmatch(schema_version or ""):
            raise JobValidationError("Invalid worker artifact type or schema version")
        path = PurePosixPath(uri or "")
        if ("\\" in (uri or "") or path.is_absolute() or len(path.parts) < 3
                or path.parts[:2] != ("jobs", context.job_id)
                or any(part in {"", ".", ".."} for part in path.parts)):
            raise JobValidationError("Artifact URI must be under this job's artifact folder")
        if not _DIGEST.fullmatch(digest or ""):
            raise JobValidationError("Artifact checksum must be a lowercase SHA-256 digest")
        return {"artifact_type": artifact_type, "uri": path.as_posix(),
                "sha256": digest, "schema_version": schema_version}


StageHandler = Callable[[StageContext, threading.Event], list[dict] | None]


class Worker:
    """A synchronous worker loop with a separate lease-heartbeat thread."""

    def __init__(self, database_file, worker_id: str, handlers: dict[str, StageHandler],
                 lease_seconds: float = 60, retry_safe_stages: set[str] | None = None, after_stage=None):
        if not handlers:
            raise ValueError("At least one explicitly supported stage handler is required")
        if set(handlers) != set(retry_safe_stages or ()):
            raise ValueError(
                "Every registered handler must be explicitly declared retry-safe; "
                "unsafe or unclassified stages fail closed"
            )
        self.after_stage = after_stage
        self.store = WorkerService(database_file)
        self.worker_id = worker_id
        self.handlers = dict(handlers)
        self.lease_seconds = lease_seconds
        self._shutdown = threading.Event()

    def run_once(self) -> bool:
        if self._shutdown.is_set():
            return False
        context = self.store.claim_next(self.worker_id, set(self.handlers), self.lease_seconds)
        if context is None:
            return False
        heartbeat_stop = threading.Event()
        claim_lost = threading.Event()
        handler_cancel = threading.Event()
        interval = max(min(self.lease_seconds / 3, 20), 0.05)

        def keep_lease():
            while not heartbeat_stop.wait(interval):
                try:
                    self.store.heartbeat(context, self.lease_seconds)
                except ClaimLost:
                    claim_lost.set()
                    handler_cancel.set()
                    return
                except Exception:
                    claim_lost.set()
                    handler_cancel.set()
                    return

        heartbeat_thread = threading.Thread(target=keep_lease, name=f"lease-{context.claim_id[:8]}", daemon=True)
        heartbeat_thread.start()
        try:
            try:
                outputs = self.handlers[context.stage_key](context, handler_cancel) or []
            except Exception as exc:
                if not claim_lost.is_set():
                    try:
                        self.store.fail(context, f"{type(exc).__name__}: {exc}")
                    except ClaimLost:
                        pass
                return True
            if not claim_lost.is_set():
                try:
                    self.store.complete(context, outputs)
                    if self.after_stage is not None:
                        self.after_stage(context)
                except ClaimLost:
                    pass
            return True
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join()

    def run_forever(self, idle_wait: float = 0.5) -> None:
        while not self._shutdown.is_set():
            if not self.run_once():
                self._shutdown.wait(idle_wait)

    def shutdown(self) -> None:
        """Stop new claims; let the current stage finish and commit safely."""
        self._shutdown.set()
