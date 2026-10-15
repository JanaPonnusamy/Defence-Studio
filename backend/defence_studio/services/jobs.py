import hashlib
import json
import re
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import PurePosixPath

from defence_studio.contracts.jobs import (
    JOB_REQUEST_SCHEMA,
    JOB_STAGES,
    STAGE_STATUSES,
)
from defence_studio.database import connect
from defence_studio.repositories.jobs import JobRepository

_TRANSITIONS = {
    "PENDING": {"RUNNING", "SKIPPED"},
    "RUNNING": {"AWAITING_REVIEW", "COMPLETED", "FAILED"},
    "AWAITING_REVIEW": {"RUNNING", "COMPLETED", "FAILED"},
    "FAILED": {"RUNNING"},
    "COMPLETED": set(),
    "SKIPPED": set(),
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,99}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class JobError(Exception):
    status_code = 400


class JobNotFound(JobError):
    status_code = 404


class IdempotencyConflict(JobError):
    status_code = 409


class InvalidTransition(JobError):
    status_code = 409


class JobValidationError(JobError):
    status_code = 422


class JobService:
    def __init__(self, database_file):
        self.database_file = database_file

    def create(self, owner_id: str, idempotency_key: str, topic: str, language: str):
        owner_id = self._required(owner_id, "owner_id")
        idempotency_key = self._required(idempotency_key, "idempotency_key")
        if len(idempotency_key) > 200:
            raise JobValidationError("idempotency_key must be at most 200 characters")
        topic = self._required(topic, "topic")
        language = self._required(language, "language")
        payload = {"schema": JOB_REQUEST_SCHEMA, "topic": topic, "language": language}
        request_hash = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        job_id = str(uuid.uuid4())
        now = _now()
        with closing(connect(self.database_file)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            repo = JobRepository(connection)
            existing = connection.execute(
                "SELECT * FROM workflow_jobs WHERE owner_id = ? AND idempotency_key = ?",
                (owner_id, idempotency_key),
            ).fetchone()
            if existing:
                if existing["request_hash"] != request_hash:
                    raise IdempotencyConflict("Idempotency key was already used with a different request")
                connection.commit()
                return self._serialize(repo, existing)
            connection.execute(
                "INSERT INTO workflow_jobs(job_id, owner_id, idempotency_key, request_schema, request_hash, "
                "topic, language, status, created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (job_id, owner_id, idempotency_key, JOB_REQUEST_SCHEMA, request_hash,
                 topic, language, "DRAFT", now, now),
            )
            connection.executemany(
                "INSERT INTO workflow_stages(job_id, stage_key, ordinal, status, version, created_at, updated_at) "
                "VALUES(?, ?, ?, 'PENDING', 1, ?, ?)",
                [(job_id, stage, ordinal, now, now) for ordinal, stage in enumerate(JOB_STAGES)],
            )
            connection.execute(
                "INSERT INTO workflow_transitions(job_id, stage_key, from_status, to_status, stage_version, occurred_at) "
                "VALUES(?, ?, '', 'PENDING', 1, ?)",
                (job_id, JOB_STAGES[0], now),
            )
            connection.commit()
            return self._serialize(repo, repo.get_job(job_id))

    def get(self, job_id: str, owner_id: str):
        with closing(connect(self.database_file)) as connection:
            repo = JobRepository(connection)
            job = repo.get_owned_job(job_id, owner_id)
            if job is None:
                raise JobNotFound("Workflow job not found")
            return self._serialize(repo, job)

    def list_for_owner(self, owner_id: str):
        """Return a lightweight, owner-scoped summary of each job for the
        dashboard: identity, topic/language, status, timestamps, and a small
        stage roll-up (completed / total / current). Full stage/artifact/claim
        detail stays behind ``get`` so the list stays cheap."""
        owner_id = self._required(owner_id, "owner_id")
        with closing(connect(self.database_file)) as connection:
            repo = JobRepository(connection)
            summaries = []
            for job in repo.list_owned_jobs(owner_id):
                stages = repo.stages(job["job_id"])
                done = sum(1 for s in stages if s["status"] in {"COMPLETED", "SKIPPED"})
                current = next(
                    (s["stage_key"] for s in stages
                     if s["status"] in {"RUNNING", "AWAITING_REVIEW", "FAILED"}),
                    None,
                )
                summaries.append({
                    "job_id": job["job_id"],
                    "owner_id": job["owner_id"],
                    "topic": job["topic"],
                    "language": job["language"],
                    "status": job["status"],
                    "created_at": job["created_at"],
                    "updated_at": job["updated_at"],
                    "stages_total": len(stages),
                    "stages_completed": done,
                    "current_stage": current,
                })
            return summaries

    def transition(self, job_id: str, owner_id: str, stage_key: str, to_status: str, error: str = ""):
        if to_status not in STAGE_STATUSES or to_status == "PENDING":
            raise JobValidationError("Unsupported stage status")
        error = (error or "").strip()
        if len(error) > 4000:
            raise JobValidationError("Error record exceeds 4000 characters")
        now = _now()
        with closing(connect(self.database_file)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            repo = JobRepository(connection)
            job = repo.get_owned_job(job_id, owner_id)
            if job is None:
                raise JobNotFound("Workflow job not found")
            if job["status"] == "CANCELLED":
                raise InvalidTransition("A cancelled job cannot accept stage transitions")
            if job["status"] == "QUEUED":
                raise InvalidTransition("Queued jobs are controlled by the worker")
            stage = connection.execute(
                "SELECT * FROM workflow_stages WHERE job_id = ? AND stage_key = ?",
                (job_id, stage_key),
            ).fetchone()
            if stage is None:
                raise JobValidationError("Unknown stage for workflow job")
            active_claim = connection.execute(
                "SELECT 1 FROM worker_claims WHERE job_id = ? AND stage_key = ? AND status = 'ACTIVE'",
                (job_id, stage_key),
            ).fetchone()
            if active_claim:
                raise InvalidTransition("An active worker claim owns this stage")
            before = stage["status"]
            if to_status == "FAILED" and not error:
                raise JobValidationError("A failed stage requires an error record")
            if to_status not in _TRANSITIONS[before]:
                raise InvalidTransition(f"Stage cannot transition from {before} to {to_status}")
            if to_status in {"RUNNING", "SKIPPED"} and before in {"PENDING", "FAILED"}:
                predecessor = connection.execute(
                    "SELECT status FROM workflow_stages WHERE job_id = ? AND ordinal = ?",
                    (job_id, stage["ordinal"] - 1),
                ).fetchone()
                if predecessor is not None and predecessor["status"] not in {"COMPLETED", "SKIPPED"}:
                    raise InvalidTransition("A stage can start only after its predecessor is completed or skipped")
            version = stage["version"] + 1
            connection.execute(
                "UPDATE workflow_stages SET status = ?, version = ?, updated_at = ?, error = ? "
                "WHERE job_id = ? AND stage_key = ?",
                (to_status, version, now, error, job_id, stage_key),
            )
            connection.execute(
                "INSERT INTO workflow_transitions(job_id, stage_key, from_status, to_status, stage_version, occurred_at, error) "
                "VALUES(?, ?, ?, ?, ?, ?, ?)",
                (job_id, stage_key, before, to_status, version, now, error),
            )
            statuses = [row[0] for row in connection.execute(
                "SELECT status FROM workflow_stages WHERE job_id = ?", (job_id,)
            )]
            if "AWAITING_REVIEW" in statuses:
                job_status = "AWAITING_REVIEW"
            elif "FAILED" in statuses:
                job_status = "FAILED"
            elif "RUNNING" in statuses:
                job_status = "RUNNING"
            elif all(status in {"COMPLETED", "SKIPPED"} for status in statuses):
                job_status = "COMPLETED"
            elif any(status in {"COMPLETED", "SKIPPED"} for status in statuses):
                job_status = "RUNNING"
            else:
                job_status = "DRAFT"
            connection.execute(
                "UPDATE workflow_jobs SET status = ?, updated_at = ? WHERE job_id = ?",
                (job_status, now, job_id),
            )
            connection.commit()
            return self._serialize(repo, repo.get_job(job_id))

    def add_pretransition_artifact(self, job_id: str, owner_id: str, stage_key: str, artifact_type: str,
                                   uri: str, sha256: str, schema_version: str):
        """Attach an owner-submitted input artifact to a stage that has not
        started yet, before the job is queued.

        `add_artifact` deliberately requires a stage to have started, because
        it is meant for worker-produced outputs. The very first real stage in
        a job has no predecessor of its own to carry its input, so an owner
        must be able to attach one to `application_foundation` (which has no
        handler/work of its own) while the job is still `DRAFT` -- before any
        worker claim exists, so there is no claim-fencing race to protect
        against. Restricting this to `DRAFT` jobs is what keeps it safe: once
        queued, only `add_artifact`'s stricter started/claim rules apply.
        """
        with closing(connect(self.database_file)) as connection:
            repo = JobRepository(connection)
            job = repo.get_owned_job(job_id, owner_id)
            if job is None:
                raise JobNotFound("Workflow job not found")
            if job["status"] != "DRAFT":
                raise InvalidTransition("Pre-queue artifacts can only be attached to a draft job")
        return self.add_artifact(job_id, owner_id, stage_key, artifact_type, uri, sha256,
                                 schema_version, _require_started=False)

    def add_artifact(self, job_id: str, owner_id: str, stage_key: str, artifact_type: str,
                     uri: str, sha256: str, schema_version: str, _require_started: bool = True):
        for value, name in ((artifact_type, "artifact_type"), (schema_version, "schema_version")):
            if not _IDENTIFIER.fullmatch(value or ""):
                raise JobValidationError(f"Invalid {name}")
        path = PurePosixPath(uri or "")
        if ("\\" in (uri or "") or path.is_absolute() or len(path.parts) < 3
                or path.parts[0] != "jobs" or path.parts[1] != job_id
                or any(part in {"", ".", ".."} for part in path.parts)):
            raise JobValidationError("Artifact URI must be a safe relative path under this job")
        if not _SHA256.fullmatch(sha256 or ""):
            raise JobValidationError("Artifact checksum must be a lowercase SHA-256 hex digest")
        now = _now()
        with closing(connect(self.database_file)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            repo = JobRepository(connection)
            if repo.get_owned_job(job_id, owner_id) is None:
                raise JobNotFound("Workflow job not found")
            stage = connection.execute(
                "SELECT status, version FROM workflow_stages WHERE job_id = ? AND stage_key = ?",
                (job_id, stage_key),
            ).fetchone()
            if stage is None:
                raise JobValidationError("Unknown stage for workflow job")
            if connection.execute(
                "SELECT 1 FROM worker_claims WHERE job_id = ? AND stage_key = ? AND status = 'ACTIVE'",
                (job_id, stage_key),
            ).fetchone():
                raise InvalidTransition("Artifact references for an active claim must commit with its result")
            if _require_started and stage["version"] == 1 and stage["status"] == "PENDING":
                raise JobValidationError("An artifact can be recorded only after its stage has started")
            version = connection.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM workflow_artifacts WHERE job_id = ? AND artifact_type = ?",
                (job_id, artifact_type),
            ).fetchone()[0]
            artifact_id = str(uuid.uuid4())
            connection.execute(
                "INSERT INTO workflow_artifacts VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (artifact_id, job_id, stage_key, artifact_type, version, path.as_posix(),
                 sha256, schema_version, now),
            )
            connection.execute("UPDATE workflow_jobs SET updated_at = ? WHERE job_id = ?", (now, job_id))
            connection.commit()
            return dict(connection.execute(
                "SELECT * FROM workflow_artifacts WHERE artifact_id = ?", (artifact_id,)
            ).fetchone())

    @staticmethod
    def _serialize(repo: JobRepository, row):
        result = dict(row)
        result["stages"] = [dict(stage) for stage in repo.stages(row["job_id"])]
        result["artifacts"] = [dict(artifact) for artifact in repo.artifacts(row["job_id"])]
        result["claims"] = [dict(claim) for claim in repo.claims(row["job_id"])]
        result["schema"] = "defence-studio.job-state.v1"
        return result

    @staticmethod
    def _required(value: str, name: str) -> str:
        normalized = (value or "").strip()
        if not normalized or len(normalized) > 1000:
            raise JobValidationError(f"{name} is required and must be at most 1000 characters")
        return normalized
