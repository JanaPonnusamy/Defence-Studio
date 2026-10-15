"""Simple, fully self-contained in-process job system.

Documented design choice: this module uses a ThreadPoolExecutor-backed
queue rather than an external broker (Redis/Celery). This keeps the module
standalone with zero external services, at the cost of jobs not surviving a
process restart mid-run (acceptable for this standalone build; a QUEUED job
is safe to resume since no work has started, but a RUNNING job interrupted
by process death will remain RUNNING -- documented limitation).
"""
from __future__ import annotations

import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Callable
from sqlalchemy import update

from app.config import get_settings
from app.db.base import SessionLocal
from app.db.models import Job
from app.logging_conf import get_logger

logger = get_logger("jobs.queue")

JobFn = Callable[[str, "JobContext"], str | None]


class JobContext:
    """Passed into a job function so it can report progress/stage."""

    def __init__(self, job_id: str):
        self.job_id = job_id

    def update(self, *, progress: float | None = None, stage: str | None = None) -> None:
        db = SessionLocal()
        try:
            values = {}
            if progress is not None:
                values['progress'] = progress
            if stage is not None:
                values['stage'] = stage
            if values:
                db.execute(update(Job).where(Job.job_id == self.job_id, Job.status == 'RUNNING').values(**values))
            db.commit()
        finally:
            db.close()


class JobQueue:
    def __init__(self):
        settings = get_settings()
        self._executor = ThreadPoolExecutor(max_workers=settings.job_worker_threads)

    def submit(self, job_id: str, fn: JobFn, payload: dict) -> None:
        self._executor.submit(self._run, job_id, fn, payload)

    def _run(self, job_id: str, fn: JobFn, payload: dict) -> None:
        # NOTE: `payload` here is the job's business-logic payload (e.g.
        # {"reference_id": ...}) -- it never carries a "job_type" key. The
        # real job_type lives on the Job ORM row itself, so it must be read
        # from the DB, not from `payload`. (A prior version of this method
        # logged `payload.get("job_type")`, which was always None -- found
        # and fixed during the admin self-test log review.)
        job_type = "unknown"
        db = SessionLocal()
        try:
            job = db.get(Job, job_id)
            if not job:
                logger.error("Job %s vanished before start", job_id)
                return
            job_type = job.job_type
            claimed = db.execute(update(Job).where(Job.job_id == job_id, Job.status == 'QUEUED').values(
                status='RUNNING', started_at=datetime.now(timezone.utc)))
            db.commit()
            if claimed.rowcount != 1:
                return
        finally:
            db.close()

        ctx = JobContext(job_id)
        try:
            result_id = fn(job_id, ctx)
            db = SessionLocal()
            try:
                db.execute(update(Job).where(Job.job_id == job_id, Job.status == 'RUNNING').values(
                    status='SUCCEEDED', progress=1.0, completed_at=datetime.now(timezone.utc), result_id=result_id))
                db.commit()
            finally:
                db.close()
            logger.info("Job %s (%s) succeeded", job_id, job_type)
        except Exception as exc:  # noqa: BLE001
            tb = traceback.format_exc()
            logger.error("Job %s (%s) failed: %s\n%s", job_id, job_type, exc, tb)
            db = SessionLocal()
            try:
                db.execute(update(Job).where(Job.job_id == job_id, Job.status == 'RUNNING').values(
                    status='FAILED', completed_at=datetime.now(timezone.utc),
                    error_code=type(exc).__name__, error_message=str(exc)))
                db.commit()
            finally:
                db.close()


_queue: JobQueue | None = None


def get_job_queue() -> JobQueue:
    global _queue
    if _queue is None:
        _queue = JobQueue()
    return _queue
