import sqlite3
from contextlib import closing
from pathlib import Path


SCHEMA_VERSION = 10


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
        if version not in (1, 2, 3, 4, 5, 6, 7, 8, 9, SCHEMA_VERSION):
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
            version = 3
        if version == 3:
            _upgrade_v3_to_v4(connection)
            version = 4
        if version == 4:
            _upgrade_v4_to_v5(connection)
            version = 5
        if version == 5:
            _upgrade_v5_to_v6(connection)
            version = 6
        if version == 6:
            _upgrade_v6_to_v7(connection)
            version = 7
        if version == 7:
            _upgrade_v7_to_v8(connection)
            version = 8
        if version == 8:
            _upgrade_v8_to_v9(connection)
            version = 9
        if version == 9:
            _upgrade_v9_to_v10(connection)


def _upgrade_v3_to_v4(connection: sqlite3.Connection) -> None:
    """Append-only package approvals. DDL and version update commit together."""
    connection.execute("BEGIN EXCLUSIVE")
    try:
        connection.execute("""CREATE TABLE IF NOT EXISTS evidence_packages (
            job_id TEXT NOT NULL REFERENCES workflow_jobs(job_id) ON DELETE CASCADE,
            version INTEGER NOT NULL CHECK(version > 0),
            revision TEXT NOT NULL,
            uri TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            reviewer_id TEXT NOT NULL,
            note TEXT NOT NULL,
            created_at TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            PRIMARY KEY(job_id, version),
            UNIQUE(job_id, revision),
            UNIQUE(job_id, idempotency_key)
        )""")
        connection.execute("UPDATE schema_info SET version = 4 WHERE singleton = 1")
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def _upgrade_v4_to_v5(connection: sqlite3.Connection) -> None:
    """Phase 3: immutable, versioned script revisions + append-only script
    review/approval history. Additive only; no F1-F5/evidence table is touched.

    `(job_id, language, version)` is the revision identity -- Tamil and English
    are independent lineages per job, so approving one never approves the
    other. `revision_sha256` is unique per job+language so a duplicate
    generation request can be detected and is never silently re-created."""
    connection.execute("BEGIN EXCLUSIVE")
    try:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS script_revisions (
                job_id TEXT NOT NULL REFERENCES workflow_jobs(job_id) ON DELETE CASCADE,
                language TEXT NOT NULL,
                version INTEGER NOT NULL CHECK(version > 0),
                revision_sha256 TEXT NOT NULL,
                uri TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                package_sha256 TEXT NOT NULL,
                request_sha256 TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN
                    ('VALIDATION_FAILED','AWAITING_REVIEW','CHANGES_REQUESTED','APPROVED','SUPERSEDED')),
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                PRIMARY KEY(job_id, language, version),
                UNIQUE(job_id, language, revision_sha256),
                UNIQUE(job_id, language, idempotency_key)
            );
            CREATE INDEX IF NOT EXISTS ix_script_revisions_job_lang
                ON script_revisions(job_id, language, created_at);

            CREATE TABLE IF NOT EXISTS script_review_history (
                history_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES workflow_jobs(job_id) ON DELETE CASCADE,
                language TEXT NOT NULL,
                version INTEGER NOT NULL,
                action TEXT NOT NULL CHECK(action IN ('CHANGES_REQUESTED','APPROVED')),
                reviewer_id TEXT NOT NULL,
                note TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                UNIQUE(job_id, language, version, reviewer_id, idempotency_key)
            );
            CREATE INDEX IF NOT EXISTS ix_script_review_history_subject
                ON script_review_history(job_id, language, version, occurred_at);
        """)
        connection.execute("UPDATE schema_info SET version = 5 WHERE singleton = 1")
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def _upgrade_v5_to_v6(connection: sqlite3.Connection) -> None:
    """Phase 4 M1: immutable, versioned scene plans + append-only scene-plan
    review/approval history. Additive only; no F1-F5/evidence/script table is
    touched.

    `(job_id, language, version)` is the plan identity -- Tamil and English are
    independent lineages per job, exactly like `script_revisions`, so approving
    one language's plan never approves the other. `plan_sha256` is unique per
    job+language so a duplicate generation/edit landing the same content is
    detected and never silently re-created. Each plan row records the exact
    approved-script revision hash (`script_revision_sha256`) and its approved
    evidence package hash (`package_sha256`) it was built from, so a later
    script change can be detected and block a *new* approval (re-sync required)
    rather than being silently absorbed."""
    connection.execute("BEGIN EXCLUSIVE")
    try:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS scene_plans (
                job_id TEXT NOT NULL REFERENCES workflow_jobs(job_id) ON DELETE CASCADE,
                language TEXT NOT NULL,
                version INTEGER NOT NULL CHECK(version > 0),
                plan_sha256 TEXT NOT NULL,
                uri TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                script_revision_sha256 TEXT NOT NULL,
                script_version INTEGER NOT NULL CHECK(script_version > 0),
                package_sha256 TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN
                    ('DRAFT','AWAITING_REVIEW','CHANGES_REQUESTED','APPROVED','SUPERSEDED')),
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                PRIMARY KEY(job_id, language, version),
                UNIQUE(job_id, language, plan_sha256),
                UNIQUE(job_id, language, idempotency_key)
            );
            CREATE INDEX IF NOT EXISTS ix_scene_plans_job_lang
                ON scene_plans(job_id, language, created_at);

            CREATE TABLE IF NOT EXISTS scene_plan_review_history (
                history_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES workflow_jobs(job_id) ON DELETE CASCADE,
                language TEXT NOT NULL,
                version INTEGER NOT NULL,
                action TEXT NOT NULL CHECK(action IN ('CHANGES_REQUESTED','APPROVED')),
                reviewer_id TEXT NOT NULL,
                note TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                UNIQUE(job_id, language, version, reviewer_id, idempotency_key)
            );
            CREATE INDEX IF NOT EXISTS ix_scene_plan_review_history_subject
                ON scene_plan_review_history(job_id, language, version, occurred_at);
        """)
        connection.execute("UPDATE schema_info SET version = 6 WHERE singleton = 1")
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def _upgrade_v6_to_v7(connection: sqlite3.Connection) -> None:
    """Phase 5 M2.1: reference-only asset management & rights provenance.

    Additive only; no F1-M1/evidence/script/scene table is touched. Five tables,
    separating immutable content-addressed records (descriptor / rights-revision /
    assignment) from the append-only rights-approval ledger (current review +
    history), exactly as Phase 2 separates evidence artifacts from the
    `evidence_packages` approval ledger:

    * `assets` -- one row per registered asset; content-addressed by `asset_id`
      (the descriptor digest). `review_status`/`effective_rights` are the current
      derived state; the canonical descriptor bytes live in the artifact store.
    * `asset_rights` -- immutable rights revisions per asset (a new rights record
      never overwrites a prior one), identified by `rights_sha256`.
    * `asset_assignments` -- immutable, versioned bindings of one asset to one
      scene of an approved plan, pinned to the exact `scene_plan_sha256` +
      `scene_plan_version` + `scene_id` they were made against.
    * `asset_reviews` / `asset_review_history` -- the current human rights
      decision per asset plus the append-only audit trail, each decision pinned
      to the exact asset+rights digests the reviewer saw. Mirrors F5's
      current-decision + history split and M1's `scene_plan_review_history`.

    Ingestion is never approval: these tables record state, they do not imply it.
    DDL + the version bump commit together inside `BEGIN EXCLUSIVE` and roll back
    together on any failure, exactly like every prior migration.
    """
    connection.execute("BEGIN EXCLUSIVE")
    try:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS assets (
                job_id TEXT NOT NULL REFERENCES workflow_jobs(job_id) ON DELETE CASCADE,
                asset_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                uri TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                review_status TEXT NOT NULL CHECK(review_status IN
                    ('DRAFT','PENDING_REVIEW','APPROVED','REJECTED','RESTRICTED','EXPIRED')),
                effective_rights TEXT NOT NULL CHECK(effective_rights IN
                    ('CLEAR','RESTRICTED','UNVERIFIED','EXPIRED','BLOCKED')),
                rights_sha256 TEXT,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                PRIMARY KEY(job_id, asset_id),
                UNIQUE(job_id, idempotency_key)
            );
            CREATE INDEX IF NOT EXISTS ix_assets_job_status
                ON assets(job_id, review_status, created_at);

            CREATE TABLE IF NOT EXISTS asset_rights (
                job_id TEXT NOT NULL REFERENCES workflow_jobs(job_id) ON DELETE CASCADE,
                asset_id TEXT NOT NULL,
                rights_sha256 TEXT NOT NULL,
                uri TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(job_id, asset_id, rights_sha256)
            );

            CREATE TABLE IF NOT EXISTS asset_assignments (
                job_id TEXT NOT NULL REFERENCES workflow_jobs(job_id) ON DELETE CASCADE,
                language TEXT NOT NULL,
                scene_id TEXT NOT NULL,
                asset_id TEXT NOT NULL,
                version INTEGER NOT NULL CHECK(version > 0),
                assignment_sha256 TEXT NOT NULL,
                uri TEXT NOT NULL,
                scene_plan_sha256 TEXT NOT NULL,
                scene_plan_version INTEGER NOT NULL CHECK(scene_plan_version > 0),
                effective_rights TEXT NOT NULL CHECK(effective_rights IN
                    ('CLEAR','RESTRICTED','UNVERIFIED','EXPIRED','BLOCKED')),
                target_use TEXT,
                status TEXT NOT NULL CHECK(status IN ('ACTIVE','REMOVED')),
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                PRIMARY KEY(job_id, language, scene_id, asset_id, version),
                UNIQUE(job_id, language, idempotency_key)
            );
            CREATE INDEX IF NOT EXISTS ix_asset_assignments_scene
                ON asset_assignments(job_id, language, scene_id, status);

            CREATE TABLE IF NOT EXISTS asset_reviews (
                job_id TEXT NOT NULL REFERENCES workflow_jobs(job_id) ON DELETE CASCADE,
                asset_id TEXT NOT NULL,
                decision TEXT NOT NULL CHECK(decision IN
                    ('APPROVED','REJECTED','RESTRICTED','CHANGES_REQUESTED')),
                reviewed_asset_sha256 TEXT NOT NULL,
                reviewed_rights_sha256 TEXT NOT NULL,
                reviewer_id TEXT NOT NULL,
                note TEXT NOT NULL,
                revision INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(job_id, asset_id)
            );

            CREATE TABLE IF NOT EXISTS asset_review_history (
                history_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES workflow_jobs(job_id) ON DELETE CASCADE,
                asset_id TEXT NOT NULL,
                decision TEXT NOT NULL CHECK(decision IN
                    ('APPROVED','REJECTED','RESTRICTED','CHANGES_REQUESTED')),
                reviewed_asset_sha256 TEXT NOT NULL,
                reviewed_rights_sha256 TEXT NOT NULL,
                reviewer_id TEXT NOT NULL,
                note TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                UNIQUE(job_id, asset_id, reviewer_id, idempotency_key)
            );
            CREATE INDEX IF NOT EXISTS ix_asset_review_history_subject
                ON asset_review_history(job_id, asset_id, occurred_at);
        """)
        connection.execute("UPDATE schema_info SET version = 7 WHERE singleton = 1")
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def _upgrade_v7_to_v8(connection: sqlite3.Connection) -> None:
    """RM-3..RM-7: durable cross-module integration artifacts + audit events.

    Additive only. ``integration_artifacts`` holds one row per
    ``(job, language, kind, version)`` for outputs of the standalone Visual /
    Audio / Renderer modules and the QC / package stages. The row carries the
    exact upstream bindings (``input_binding``, canonical JSON of upstream
    hashes) so a changed upstream artifact makes it detectably stale; bytes
    live in the artifact store and are identified by ``sha256``. Rows are
    immutable except ``status`` and ``detail`` (state machine), and every
    transition is appended to ``integration_events``.
    """
    connection.execute("BEGIN EXCLUSIVE")
    try:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS integration_artifacts (
                job_id TEXT NOT NULL REFERENCES workflow_jobs(job_id) ON DELETE CASCADE,
                language TEXT NOT NULL,
                kind TEXT NOT NULL,
                version INTEGER NOT NULL CHECK(version > 0),
                status TEXT NOT NULL CHECK(status IN
                    ('QUEUED','RUNNING','SUCCEEDED','FAILED','INTERRUPTED','CANCELLED',
                     'AWAITING_REVIEW','APPROVED','REJECTED','SUPERSEDED')),
                uri TEXT,
                sha256 TEXT,
                input_binding TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT '',
                progress REAL NOT NULL DEFAULT 0,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                PRIMARY KEY(job_id, language, kind, version),
                UNIQUE(job_id, language, kind, idempotency_key)
            );
            CREATE INDEX IF NOT EXISTS ix_integration_artifacts_status
                ON integration_artifacts(status, updated_at);

            CREATE TABLE IF NOT EXISTS integration_events (
                event_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES workflow_jobs(job_id) ON DELETE CASCADE,
                language TEXT NOT NULL,
                kind TEXT NOT NULL,
                version INTEGER NOT NULL,
                event TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT '',
                actor_id TEXT NOT NULL,
                occurred_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_integration_events_subject
                ON integration_events(job_id, language, kind, version, occurred_at);
        """)
        connection.execute("UPDATE schema_info SET version = 8 WHERE singleton = 1")
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def _upgrade_v8_to_v9(connection: sqlite3.Connection) -> None:
    """RM-8: YouTube connection, publishing profiles, durable publishing jobs.

    Additive. Refresh tokens are stored only as Fernet ciphertext
    (``encrypted_refresh_token``); resumable-session URLs likewise
    (``session_ref_encrypted``). ``publishing_job_events`` is append-only.
    """
    connection.execute("BEGIN EXCLUSIVE")
    try:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS youtube_oauth_states (
                state TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                redirect_uri TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                used INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS youtube_connections (
                connection_id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                channel_title TEXT NOT NULL,
                channel_handle TEXT,
                thumbnail_url TEXT,
                encrypted_refresh_token TEXT,
                granted_scopes TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN
                    ('PENDING_CONFIRMATION','CONNECTED','REAUTH_REQUIRED','ERROR','DISCONNECTED')),
                confirmed_at TEXT,
                connected_at TEXT NOT NULL,
                last_checked_at TEXT,
                last_error_code TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_youtube_connections_owner
                ON youtube_connections(owner_id, status);
            CREATE TABLE IF NOT EXISTS publishing_profiles (
                profile_id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                connection_id TEXT NOT NULL REFERENCES youtube_connections(connection_id),
                name TEXT NOT NULL,
                title_prefix TEXT NOT NULL DEFAULT '',
                description_template TEXT NOT NULL DEFAULT '',
                tags_json TEXT NOT NULL DEFAULT '[]',
                category_id TEXT NOT NULL DEFAULT '25',
                language TEXT NOT NULL DEFAULT 'en',
                default_visibility TEXT NOT NULL DEFAULT 'private'
                    CHECK(default_visibility IN ('private','unlisted','public')),
                timezone TEXT NOT NULL DEFAULT 'Asia/Calcutta',
                caption_languages_json TEXT NOT NULL DEFAULT '[]',
                made_for_kids INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS publishing_jobs (
                publish_id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                job_id TEXT NOT NULL REFERENCES workflow_jobs(job_id) ON DELETE CASCADE,
                language TEXT NOT NULL,
                package_version INTEGER NOT NULL,
                package_sha256 TEXT NOT NULL,
                connection_id TEXT NOT NULL REFERENCES youtube_connections(connection_id),
                target_channel_id TEXT NOT NULL,
                youtube_video_id TEXT,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                tags_json TEXT NOT NULL,
                category_id TEXT NOT NULL,
                default_language TEXT NOT NULL,
                visibility TEXT NOT NULL CHECK(visibility IN ('private','unlisted','public')),
                scheduled_utc TEXT,
                timezone TEXT NOT NULL,
                made_for_kids INTEGER NOT NULL DEFAULT 0,
                state TEXT NOT NULL,
                progress REAL NOT NULL DEFAULT 0,
                bytes_total INTEGER NOT NULL DEFAULT 0,
                bytes_uploaded INTEGER NOT NULL DEFAULT 0,
                session_ref_encrypted TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 5,
                next_retry_at TEXT,
                error_code TEXT,
                error_message TEXT,
                approved_by TEXT,
                approved_at TEXT,
                approval_digest TEXT,
                approval_note TEXT,
                thumbnail_state TEXT NOT NULL DEFAULT 'NONE',
                verified_at TEXT,
                remote_status_json TEXT,
                quota_units_used INTEGER NOT NULL DEFAULT 0,
                idempotency_key TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(owner_id, idempotency_key)
            );
            CREATE INDEX IF NOT EXISTS ix_publishing_jobs_state ON publishing_jobs(state, next_retry_at);
            CREATE INDEX IF NOT EXISTS ix_publishing_jobs_package
                ON publishing_jobs(job_id, language, package_sha256);
            CREATE TABLE IF NOT EXISTS publishing_caption_tracks (
                track_id TEXT PRIMARY KEY,
                publish_id TEXT NOT NULL REFERENCES publishing_jobs(publish_id) ON DELETE CASCADE,
                language TEXT NOT NULL,
                name TEXT NOT NULL,
                source_sha256 TEXT NOT NULL,
                youtube_caption_id TEXT,
                state TEXT NOT NULL CHECK(state IN ('PENDING','UPLOADING','UPLOADED','FAILED')),
                error_code TEXT,
                updated_at TEXT NOT NULL,
                UNIQUE(publish_id, language)
            );
            CREATE TABLE IF NOT EXISTS publishing_job_events (
                event_id TEXT PRIMARY KEY,
                publish_id TEXT NOT NULL REFERENCES publishing_jobs(publish_id) ON DELETE CASCADE,
                event TEXT NOT NULL,
                prev_state TEXT,
                new_state TEXT,
                detail TEXT NOT NULL DEFAULT '',
                actor TEXT NOT NULL,
                occurred_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_publishing_events_job
                ON publishing_job_events(publish_id, occurred_at);
        """)
        connection.execute("UPDATE schema_info SET version = 9 WHERE singleton = 1")
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def _upgrade_v9_to_v10(connection: sqlite3.Connection) -> None:
    """Per-owner provider credentials (Fernet ciphertext only; additive)."""
    connection.execute("BEGIN EXCLUSIVE")
    try:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS provider_credentials (
                owner_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                encrypted_value TEXT NOT NULL,
                last4 TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(owner_id, provider)
            );
        """)
        connection.execute("UPDATE schema_info SET version = 10 WHERE singleton = 1")
        connection.commit()
    except Exception:
        connection.rollback()
        raise


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
