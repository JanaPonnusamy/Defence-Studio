from __future__ import annotations

import time

from app.db.base import SessionLocal
from app.db.init_db import init_db
from app.db.models import Job
from app.jobs.queue import JobContext, get_job_queue


def test_job_lifecycle_success():
    init_db()
    db = SessionLocal()
    job = Job(job_type="unit_test", status="QUEUED", payload={})
    db.add(job)
    db.commit()
    job_id = job.job_id
    db.close()

    def fn(_job_id: str, ctx: JobContext) -> str:
        ctx.update(progress=0.5, stage="HALFWAY")
        return "result-123"

    get_job_queue().submit(job_id, fn, {})

    deadline = time.time() + 10
    db = SessionLocal()
    final = None
    while time.time() < deadline:
        db.expire_all()
        j = db.get(Job, job_id)
        if j.status in ("SUCCEEDED", "FAILED"):
            final = j
            break
        time.sleep(0.1)
    db.close()
    assert final is not None
    assert final.status == "SUCCEEDED"
    assert final.result_id == "result-123"
    assert final.progress == 1.0


def test_job_lifecycle_failure_records_error():
    init_db()
    db = SessionLocal()
    job = Job(job_type="unit_test_fail", status="QUEUED", payload={})
    db.add(job)
    db.commit()
    job_id = job.job_id
    db.close()

    def fn(_job_id: str, ctx: JobContext) -> str:
        raise RuntimeError("deliberate failure")

    get_job_queue().submit(job_id, fn, {})

    deadline = time.time() + 10
    db = SessionLocal()
    final = None
    while time.time() < deadline:
        db.expire_all()
        j = db.get(Job, job_id)
        if j.status in ("SUCCEEDED", "FAILED"):
            final = j
            break
        time.sleep(0.1)
    db.close()
    assert final is not None
    assert final.status == "FAILED"
    assert final.error_code == "RuntimeError"
    assert "deliberate failure" in final.error_message
