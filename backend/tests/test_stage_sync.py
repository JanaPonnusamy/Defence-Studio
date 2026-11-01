"""Script/scene approvals happen over HTTP, so they must advance their own workflow stage."""
from contextlib import closing

from defence_studio.database import connect, initialize_database
from defence_studio.services.jobs import JobService
from defence_studio.services.stage_sync import complete_stage_locked

STAGE = "script_generation_and_approval"


def _setup(tmp_path):
    db = tmp_path / "db.sqlite3"
    initialize_database(db)
    job = JobService(db).create("owner", "k1", "topic", "en")
    return db, job["job_id"]


def _status(c, job_id, stage=STAGE):
    return c.execute("SELECT status FROM workflow_stages WHERE job_id=? AND stage_key=?", (job_id, stage)).fetchone()[0]


def test_pending_stage_completes_through_legal_transitions(tmp_path):
    db, job_id = _setup(tmp_path)
    with closing(connect(db)) as c:
        assert _status(c, job_id) == "PENDING"
        assert complete_stage_locked(c, job_id, STAGE, "2026-10-28T00:00:00+00:00") is True
        c.commit()
        assert _status(c, job_id) == "COMPLETED"
        path = [r[0] for r in c.execute("SELECT to_status FROM workflow_transitions WHERE job_id=? AND stage_key=? "
                                        "ORDER BY rowid", (job_id, STAGE))]
        assert path == ["RUNNING", "AWAITING_REVIEW", "COMPLETED"]
        assert c.execute("SELECT status FROM workflow_jobs WHERE job_id=?", (job_id,)).fetchone()[0] == "PAUSED"
        assert _status(c, job_id, "scene_creation_and_voice_direction") == "PENDING"   # only the named stage moves


def test_completed_stage_is_a_no_op(tmp_path):
    db, job_id = _setup(tmp_path)
    with closing(connect(db)) as c:
        complete_stage_locked(c, job_id, STAGE, "2026-10-28T00:00:00+00:00")
        n = c.execute("SELECT COUNT(*) FROM workflow_transitions WHERE job_id=?", (job_id,)).fetchone()[0]
        assert complete_stage_locked(c, job_id, STAGE, "2026-10-28T00:00:01+00:00") is False
        assert c.execute("SELECT COUNT(*) FROM workflow_transitions WHERE job_id=?", (job_id,)).fetchone()[0] == n
