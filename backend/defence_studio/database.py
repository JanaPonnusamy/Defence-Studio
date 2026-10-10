import sqlite3
from contextlib import closing
from pathlib import Path


SCHEMA_VERSION = 3


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


def initialize_database(path: Path) -> None:
    with closing(connect(path)) as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_info (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                version INTEGER NOT NULL
            );
            INSERT INTO schema_info(singleton, version) VALUES(1, 1)
                ON CONFLICT(singleton) DO NOTHING;
            """
        )
        version = connection.execute(
            "SELECT version FROM schema_info WHERE singleton = 1"
        ).fetchone()[0]
        if version not in (1, 2, SCHEMA_VERSION):
            raise RuntimeError(f"Unsupported Defence Studio schema version: {version}")

        connection.executescript(
            """

            CREATE TABLE IF NOT EXISTS workflow_jobs (
                job_id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                request_schema TEXT NOT NULL,
                request_hash TEXT NOT NULL,
                topic TEXT NOT NULL,
                language TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                queued_at TEXT,
                UNIQUE(owner_id, idempotency_key)
            );
            CREATE INDEX IF NOT EXISTS ix_workflow_jobs_owner_created
                ON workflow_jobs(owner_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS workflow_stages (
                job_id TEXT NOT NULL REFERENCES workflow_jobs(job_id) ON DELETE CASCADE,
                stage_key TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                status TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                error TEXT NOT NULL DEFAULT '',
                attempt_count INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 3,
                retry_after TEXT,
                PRIMARY KEY(job_id, stage_key)
            );

            CREATE TABLE IF NOT EXISTS workflow_transitions (
                transition_id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL REFERENCES workflow_jobs(job_id) ON DELETE CASCADE,
                stage_key TEXT NOT NULL,
                from_status TEXT NOT NULL,
                to_status TEXT NOT NULL,
                stage_version INTEGER NOT NULL,
                occurred_at TEXT NOT NULL,
                error TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(job_id, stage_key)
                    REFERENCES workflow_stages(job_id, stage_key) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS ix_workflow_transitions_job_time
                ON workflow_transitions(job_id, occurred_at);

            CREATE TABLE IF NOT EXISTS workflow_artifacts (
                artifact_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES workflow_jobs(job_id) ON DELETE CASCADE,
                stage_key TEXT NOT NULL,
                artifact_type TEXT NOT NULL,
                version INTEGER NOT NULL CHECK(version > 0),
                uri TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(job_id, artifact_type, version),
                FOREIGN KEY(job_id, stage_key)
                    REFERENCES workflow_stages(job_id, stage_key) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS ix_workflow_artifacts_job_stage
                ON workflow_artifacts(job_id, stage_key, created_at);

            """
        )
        if version == 1:
            _upgrade_v1_to_v2(connection)
            version = 2
        if version == 2:
            _upgrade_v2_to_v3(connection)


def _upgrade_v1_to_v2(connection: sqlite3.Connection) -> None:
    """Add worker metadata without rewriting F1 jobs or their artifacts."""
    connection.execute("BEGIN EXCLUSIVE")
    try:
        job_columns = {row[1] for row in connection.execute("PRAGMA table_info(workflow_jobs)")}
        stage_columns = {row[1] for row in connection.execute("PRAGMA table_info(workflow_stages)")}
        if "queued_at" not in job_columns:
            connection.execute("ALTER TABLE workflow_jobs ADD COLUMN queued_at TEXT")
        for name, ddl in (
            ("attempt_count", "INTEGER NOT NULL DEFAULT 0"),
            ("max_attempts", "INTEGER NOT NULL DEFAULT 3"),
            ("retry_after", "TEXT"),
        ):
            if name not in stage_columns:
                connection.execute(f"ALTER TABLE workflow_stages ADD COLUMN {name} {ddl}")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS worker_claims ("
            "claim_id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES workflow_jobs(job_id) ON DELETE CASCADE, "
            "stage_key TEXT NOT NULL, worker_id TEXT NOT NULL, attempt INTEGER NOT NULL CHECK(attempt > 0), "
            "status TEXT NOT NULL CHECK(status IN ('ACTIVE', 'COMPLETED', 'FAILED', 'EXPIRED')), "
            "lease_expires_at TEXT NOT NULL, created_at TEXT NOT NULL, heartbeat_at TEXT NOT NULL, "
            "finished_at TEXT, failure_detail TEXT NOT NULL DEFAULT '', "
            "FOREIGN KEY(job_id, stage_key) REFERENCES workflow_stages(job_id, stage_key) ON DELETE CASCADE)"
        )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_worker_claim_active_stage "
            "ON worker_claims(job_id, stage_key) WHERE status = 'ACTIVE'"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS ix_worker_claim_expiry ON worker_claims(status, lease_expires_at)"
        )
        connection.execute("UPDATE schema_info SET version = 2 WHERE singleton = 1")
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def _upgrade_v2_to_v3(connection: sqlite3.Connection) -> None:
    """Add F5 evidence-review decisions and their append-only audit history.

    Additive only: no F1-F4 table, column, or row is touched. A decision row
    references `job_id` (FK) and an opaque `claim_id`/`source_id` string from
    the F3/F4 JSON artifacts -- those identities are not duplicated as DB rows
    here, consistent with how F3/F4 already store their output as artifacts
    rather than parallel tables."""
    connection.execute("BEGIN EXCLUSIVE")
    try:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS claim_reviews (
                job_id TEXT NOT NULL REFERENCES workflow_jobs(job_id) ON DELETE CASCADE,
                claim_id TEXT NOT NULL,
                decision TEXT NOT NULL CHECK(decision IN
                    ('APPROVED_FOR_USE', 'REJECTED', 'NEEDS_MORE_EVIDENCE', 'CONFLICT_UNRESOLVED')),
                reviewed_artifact_type TEXT NOT NULL,
                reviewed_artifact_sha256 TEXT NOT NULL,
                reviewer_id TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                revision INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(job_id, claim_id)
            );

            CREATE TABLE IF NOT EXISTS claim_review_history (
                history_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES workflow_jobs(job_id) ON DELETE CASCADE,
                claim_id TEXT NOT NULL,
                decision TEXT NOT NULL,
                reviewed_artifact_type TEXT NOT NULL,
                reviewed_artifact_sha256 TEXT NOT NULL,
                reviewer_id TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                revision INTEGER NOT NULL,
                previous_decision TEXT,
                occurred_at TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                UNIQUE(job_id, claim_id, reviewer_id, idempotency_key)
            );
            CREATE INDEX IF NOT EXISTS ix_claim_review_history_subject
                ON claim_review_history(job_id, claim_id, occurred_at);

            CREATE TABLE IF NOT EXISTS source_reviews (
                job_id TEXT NOT NULL REFERENCES workflow_jobs(job_id) ON DELETE CASCADE,
                source_id TEXT NOT NULL,
                decision TEXT NOT NULL CHECK(decision IN
                    ('APPROVED_FOR_USE', 'REJECTED', 'NEEDS_MORE_EVIDENCE', 'CONFLICT_UNRESOLVED')),
                reviewed_artifact_type TEXT NOT NULL,
                reviewed_artifact_sha256 TEXT NOT NULL,
                reviewer_id TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                revision INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(job_id, source_id)
            );

            CREATE TABLE IF NOT EXISTS source_review_history (
                history_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES workflow_jobs(job_id) ON DELETE CASCADE,
                source_id TEXT NOT NULL,
                decision TEXT NOT NULL,
                reviewed_artifact_type TEXT NOT NULL,
                reviewed_artifact_sha256 TEXT NOT NULL,
                reviewer_id TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                revision INTEGER NOT NULL,
                previous_decision TEXT,
                occurred_at TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                UNIQUE(job_id, source_id, reviewer_id, idempotency_key)
            );
            CREATE INDEX IF NOT EXISTS ix_source_review_history_subject
                ON source_review_history(job_id, source_id, occurred_at);
            """
        )
        connection.execute("UPDATE schema_info SET version = 3 WHERE singleton = 1")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
