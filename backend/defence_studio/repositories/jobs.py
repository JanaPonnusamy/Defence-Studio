import sqlite3


class JobRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def get_job(self, job_id: str):
        return self.connection.execute(
            "SELECT * FROM workflow_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()

    def get_owned_job(self, job_id: str, owner_id: str):
        return self.connection.execute(
            "SELECT * FROM workflow_jobs WHERE job_id = ? AND owner_id = ?",
            (job_id, owner_id),
        ).fetchone()

    def stages(self, job_id: str):
        return self.connection.execute(
            "SELECT * FROM workflow_stages WHERE job_id = ? ORDER BY ordinal", (job_id,)
        ).fetchall()

    def artifacts(self, job_id: str):
        return self.connection.execute(
            "SELECT * FROM workflow_artifacts WHERE job_id = ? ORDER BY created_at, artifact_type, version",
            (job_id,),
        ).fetchall()

    def claims(self, job_id: str):
        return self.connection.execute(
            "SELECT claim_id, stage_key, worker_id, attempt, status, lease_expires_at, created_at, "
            "heartbeat_at, finished_at, failure_detail FROM worker_claims "
            "WHERE job_id = ? ORDER BY created_at, claim_id",
            (job_id,),
        ).fetchall()
