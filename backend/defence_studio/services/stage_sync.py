"""Advance a synchronous (non-worker) workflow stage to COMPLETED on approval.

Script and scene-plan approvals happen over HTTP, not through a worker, so
nothing else moves their workflow stage. The caller holds the transaction.
"""

_PATH = {"PENDING": ("RUNNING", "AWAITING_REVIEW", "COMPLETED"),
         "RUNNING": ("AWAITING_REVIEW", "COMPLETED"),
         "AWAITING_REVIEW": ("COMPLETED",)}


def complete_stage_locked(connection, job_id: str, stage_key: str, now: str) -> bool:
    """Record the legal transitions up to COMPLETED; no-op if already terminal."""
    stage = connection.execute("SELECT status FROM workflow_stages WHERE job_id=? AND stage_key=?",
                               (job_id, stage_key)).fetchone()
    if not stage or stage[0] not in _PATH:
        return False
    before = stage[0]
    for after in _PATH[before]:
        connection.execute("UPDATE workflow_stages SET status=?,version=version+1,updated_at=? WHERE job_id=? AND stage_key=?",
                           (after, now, job_id, stage_key))
        connection.execute("INSERT INTO workflow_transitions(job_id,stage_key,from_status,to_status,stage_version,occurred_at) "
                           "SELECT job_id,stage_key,?,?,version,? FROM workflow_stages WHERE job_id=? AND stage_key=?",
                           (before, after, now, job_id, stage_key))
        before = after
    connection.execute("UPDATE workflow_jobs SET status='PAUSED',updated_at=? WHERE job_id=? AND status<>'COMPLETED'",
                       (now, job_id))
    return True
