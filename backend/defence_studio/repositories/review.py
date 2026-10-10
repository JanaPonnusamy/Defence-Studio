import sqlite3


class ReviewRepository:
    """Thin SQL access over the F5 review tables (see database.py schema v3).

    Mirrors the `JobRepository` convention: takes an open connection, returns
    sqlite3.Row objects, applies no business rules (those live in
    `services/review.py`)."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    # -- claims ---------------------------------------------------------

    def get_claim_review(self, job_id: str, claim_id: str):
        return self.connection.execute(
            "SELECT * FROM claim_reviews WHERE job_id = ? AND claim_id = ?",
            (job_id, claim_id),
        ).fetchone()

    def list_claim_reviews(self, job_id: str):
        return self.connection.execute(
            "SELECT * FROM claim_reviews WHERE job_id = ?", (job_id,)
        ).fetchall()

    def upsert_claim_review(self, job_id: str, claim_id: str, decision: str,
                            artifact_type: str, artifact_sha256: str, reviewer_id: str,
                            note: str, revision: int, now: str):
        self.connection.execute(
            "INSERT INTO claim_reviews(job_id, claim_id, decision, reviewed_artifact_type, "
            "reviewed_artifact_sha256, reviewer_id, note, revision, created_at, updated_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(job_id, claim_id) DO UPDATE SET "
            "decision = excluded.decision, reviewed_artifact_type = excluded.reviewed_artifact_type, "
            "reviewed_artifact_sha256 = excluded.reviewed_artifact_sha256, "
            "reviewer_id = excluded.reviewer_id, note = excluded.note, "
            "revision = excluded.revision, updated_at = excluded.updated_at",
            (job_id, claim_id, decision, artifact_type, artifact_sha256, reviewer_id,
             note, revision, now, now),
        )

    def insert_claim_history(self, history_id: str, job_id: str, claim_id: str, decision: str,
                             artifact_type: str, artifact_sha256: str, reviewer_id: str,
                             note: str, revision: int, previous_decision: str | None,
                             occurred_at: str, idempotency_key: str):
        self.connection.execute(
            "INSERT INTO claim_review_history(history_id, job_id, claim_id, decision, "
            "reviewed_artifact_type, reviewed_artifact_sha256, reviewer_id, note, revision, "
            "previous_decision, occurred_at, idempotency_key) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (history_id, job_id, claim_id, decision, artifact_type, artifact_sha256,
             reviewer_id, note, revision, previous_decision, occurred_at, idempotency_key),
        )

    def find_claim_history_by_idempotency_key(self, job_id: str, claim_id: str,
                                               reviewer_id: str, idempotency_key: str):
        return self.connection.execute(
            "SELECT * FROM claim_review_history WHERE job_id = ? AND claim_id = ? "
            "AND reviewer_id = ? AND idempotency_key = ?",
            (job_id, claim_id, reviewer_id, idempotency_key),
        ).fetchone()

    def claim_history(self, job_id: str, claim_id: str):
        return self.connection.execute(
            "SELECT * FROM claim_review_history WHERE job_id = ? AND claim_id = ? "
            "ORDER BY revision",
            (job_id, claim_id),
        ).fetchall()

    # -- sources ----------------------------------------------------------

    def get_source_review(self, job_id: str, source_id: str):
        return self.connection.execute(
            "SELECT * FROM source_reviews WHERE job_id = ? AND source_id = ?",
            (job_id, source_id),
        ).fetchone()

    def list_source_reviews(self, job_id: str):
        return self.connection.execute(
            "SELECT * FROM source_reviews WHERE job_id = ?", (job_id,)
        ).fetchall()

    def upsert_source_review(self, job_id: str, source_id: str, decision: str,
                             artifact_type: str, artifact_sha256: str, reviewer_id: str,
                             note: str, revision: int, now: str):
        self.connection.execute(
            "INSERT INTO source_reviews(job_id, source_id, decision, reviewed_artifact_type, "
            "reviewed_artifact_sha256, reviewer_id, note, revision, created_at, updated_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(job_id, source_id) DO UPDATE SET "
            "decision = excluded.decision, reviewed_artifact_type = excluded.reviewed_artifact_type, "
            "reviewed_artifact_sha256 = excluded.reviewed_artifact_sha256, "
            "reviewer_id = excluded.reviewer_id, note = excluded.note, "
            "revision = excluded.revision, updated_at = excluded.updated_at",
            (job_id, source_id, decision, artifact_type, artifact_sha256, reviewer_id,
             note, revision, now, now),
        )

    def insert_source_history(self, history_id: str, job_id: str, source_id: str, decision: str,
                              artifact_type: str, artifact_sha256: str, reviewer_id: str,
                              note: str, revision: int, previous_decision: str | None,
                              occurred_at: str, idempotency_key: str):
        self.connection.execute(
            "INSERT INTO source_review_history(history_id, job_id, source_id, decision, "
            "reviewed_artifact_type, reviewed_artifact_sha256, reviewer_id, note, revision, "
            "previous_decision, occurred_at, idempotency_key) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (history_id, job_id, source_id, decision, artifact_type, artifact_sha256,
             reviewer_id, note, revision, previous_decision, occurred_at, idempotency_key),
        )

    def find_source_history_by_idempotency_key(self, job_id: str, source_id: str,
                                                reviewer_id: str, idempotency_key: str):
        return self.connection.execute(
            "SELECT * FROM source_review_history WHERE job_id = ? AND source_id = ? "
            "AND reviewer_id = ? AND idempotency_key = ?",
            (job_id, source_id, reviewer_id, idempotency_key),
        ).fetchone()

    def source_history(self, job_id: str, source_id: str):
        return self.connection.execute(
            "SELECT * FROM source_review_history WHERE job_id = ? AND source_id = ? "
            "ORDER BY revision",
            (job_id, source_id),
        ).fetchall()
