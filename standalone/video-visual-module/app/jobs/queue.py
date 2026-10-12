"""
Self-contained, persisted, in-process job system (ThreadPoolExecutor).

* Every job is a DB row (spec §25) -- status, progress, stage, timestamps,
  error_code/error_message, result_id -- so jobs are observable and survive
  restarts.
* A job is SUCCEEDED only when its handler returns normally; handlers verify
  their output (files exist, decode, timeline validates) before returning.
* Any exception -> FAILED with an explicit code; unexpected exceptions are
  logged with traceback server-side and stored as INTERNAL_ERROR with only
  the exception type (no stack traces in API responses).
* Cancellation: QUEUED jobs are cancelled immediately; RUNNING jobs get
  cancel_requested=True and stop at the next progress checkpoint -> CANCELLED.
* Restart recovery (`recover_jobs`, run at startup): RUNNING jobs from a dead
  process are marked FAILED/JOB_INTERRUPTED (never SUCCEEDED -- their output
  was never verified); QUEUED jobs are re-submitted.
"""
from __future__ import annotations

import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.base import SessionLocal
from app.db.models import GenerationRecord, Job, VisualPlan
from app.errors import VideoModuleError
from app.logging_conf import get_logger
from app.storage import write_json_atomic

logger = get_logger("jobs.queue")

TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED"}
JOB_STATUSES = ["QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"]

JobFn = Callable[[str, "JobContext"], str | None]
HANDLERS: dict[str, JobFn] = {}


def register_handler(job_type: str, fn: JobFn) -> None:
    HANDLERS[job_type] = fn


class JobCancelled(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


class JobContext:
    def __init__(self, job_id: str):
        self.job_id = job_id

    def check_cancel(self) -> None:
        db = SessionLocal()
        try:
            job = db.get(Job, self.job_id)
            if job is not None and job.cancel_requested:
                raise JobCancelled()
        finally:
            db.close()

    def update(self, *, progress: float | None = None, stage: str | None = None) -> None:
        db = SessionLocal()
        try:
            job = db.get(Job, self.job_id)
            if not job:
                return
            if job.cancel_requested:
                raise JobCancelled()
            if progress is not None:
                job.progress = round(max(0.0, min(1.0, progress)), 4)
            if stage is not None:
                job.stage = stage[:128]
            db.commit()
        finally:
            db.close()


def job_snapshot(job: Job) -> dict:
    return {
        "job_id": job.job_id, "job_type": job.job_type, "project_id": job.project_id, "status": job.status,
        "progress": job.progress, "stage": job.stage, "created_at": job.created_at, "started_at": job.started_at,
        "completed_at": job.completed_at, "error_code": job.error_code, "error_message": job.error_message,
        "result_id": job.result_id, "retry_of": job.retry_of,
    }


def _finish(job_id: str, status: str, *, result_id=None, code=None, message=None) -> None:
    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        if job is None:
            return
        job.status = status
        job.completed_at = _now()
        if status == "SUCCEEDED":
            job.progress = 1.0
            job.stage = "done"
            job.result_id = result_id
        else:
            job.error_code = code
            job.error_message = (message or "")[:2000]
        db.commit()
        try:
            write_json_atomic(get_settings().jobs_dir / f"{job_id}.json", job_snapshot(job))
        except OSError:
            logger.warning("Could not write job snapshot for %s", job_id)
    finally:
        db.close()


class JobQueue:
    def __init__(self):
        self._executor = ThreadPoolExecutor(max_workers=get_settings().job_worker_threads, thread_name_prefix="vvm-job")
        self._lock = threading.Lock()
        self._inflight: set[str] = set()

    def submit(self, job_id: str) -> None:
        with self._lock:
            if job_id in self._inflight:
                return
            self._inflight.add(job_id)
        self._executor.submit(self._run, job_id)

    def inflight(self) -> int:
        with self._lock:
            return len(self._inflight)

    def shutdown(self, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=not wait)

    def _run(self, job_id: str) -> None:
        try:
            self._run_inner(job_id)
        finally:
            with self._lock:
                self._inflight.discard(job_id)

    def _run_inner(self, job_id: str) -> None:
        db = SessionLocal()
        try:
            job = db.get(Job, job_id)
            if job is None:
                logger.error("Job %s vanished before start", job_id)
                return
            if job.status != "QUEUED":
                return  # cancelled (or already handled) before a worker picked it up
            job_type = job.job_type
            fn = HANDLERS.get(job_type)
            if fn is None:
                db.close()
                _finish(job_id, "FAILED", code="INTERNAL_ERROR", message=f"No handler registered for job_type {job_type}")
                return
            job.status = "RUNNING"
            job.started_at = _now()
            job.stage = "starting"
            db.commit()
        finally:
            db.close()

        ctx = JobContext(job_id)
        try:
            result_id = fn(job_id, ctx)
        except JobCancelled:
            _finish(job_id, "CANCELLED", code="JOB_CANCELLED", message="Cancelled by request.")
            logger.info("Job %s (%s) cancelled", job_id, job_type)
        except VideoModuleError as exc:
            _finish(job_id, "FAILED", code=exc.code, message=exc.message)
            logger.warning("Job %s (%s) failed: %s %s", job_id, job_type, exc.code, exc.message)
        except Exception as exc:  # noqa: BLE001
            logger.error("Job %s (%s) crashed: %s\n%s", job_id, job_type, exc, traceback.format_exc())
            _finish(job_id, "FAILED", code="INTERNAL_ERROR",
                    message=f"Internal error ({type(exc).__name__}); see server log for job {job_id}.")
        else:
            _finish(job_id, "SUCCEEDED", result_id=result_id)
            logger.info("Job %s (%s) succeeded result=%s", job_id, job_type, result_id)


_queue: JobQueue | None = None
_queue_lock = threading.Lock()


def get_job_queue() -> JobQueue:
    global _queue
    with _queue_lock:
        if _queue is None:
            _queue = JobQueue()
        return _queue


def reset_job_queue() -> None:
    """Shut down the worker pool (used on app shutdown and by tests)."""
    global _queue
    with _queue_lock:
        if _queue is not None:
            _queue.shutdown(wait=True)
        _queue = None


def create_job(db: Session, job_type: str, payload: dict, *, project_id: str | None,
               idempotency_key: str | None = None, retry_of: str | None = None) -> tuple[Job, bool]:
    """Create + enqueue a job. Returns (job, reused). Same idempotency key + job_type returns the existing job."""
    if job_type not in HANDLERS:
        raise VideoModuleError("VALIDATION_ERROR", f"Unknown job_type {job_type}")
    if idempotency_key:
        existing = db.query(Job).filter(Job.job_type == job_type, Job.idempotency_key == idempotency_key).first()
        if existing is not None:
            return existing, True
    job = Job(job_type=job_type, status="QUEUED", payload=payload, project_id=project_id,
              idempotency_key=idempotency_key, retry_of=retry_of, stage="queued")
    db.add(job)
    db.commit()
    db.refresh(job)
    get_job_queue().submit(job.job_id)
    return job, False


def cancel_job(db: Session, job_id: str) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise VideoModuleError("NOT_FOUND", f"Job {job_id} not found.")
    if job.status in TERMINAL:
        raise VideoModuleError("CONFLICT", f"Job is already {job.status}.")
    if job.status == "QUEUED":
        job.status = "CANCELLED"
        job.cancel_requested = True
        job.completed_at = _now()
        job.error_code = "JOB_CANCELLED"
        job.error_message = "Cancelled before start."
    else:
        job.cancel_requested = True
        job.stage = "cancel requested"
    db.commit()
    db.refresh(job)
    return job


def retry_job(db: Session, job_id: str) -> Job:
    old = db.get(Job, job_id)
    if old is None:
        raise VideoModuleError("NOT_FOUND", f"Job {job_id} not found.")
    if old.status not in ("FAILED", "CANCELLED"):
        raise VideoModuleError("CONFLICT", f"Only FAILED or CANCELLED jobs can be retried (job is {old.status}).")
    job, _ = create_job(db, old.job_type, dict(old.payload or {}), project_id=old.project_id, retry_of=old.job_id)
    return job


def recover_jobs() -> dict:
    """Startup recovery. RUNNING -> FAILED (JOB_INTERRUPTED); QUEUED -> re-submitted."""
    db = SessionLocal()
    counts = {"interrupted": 0, "requeued": 0}
    requeue: list[str] = []
    try:
        for job in db.query(Job).filter(Job.status == "RUNNING").all():
            job.status = "FAILED"
            job.completed_at = _now()
            job.error_code = "JOB_INTERRUPTED"
            job.error_message = ("The service stopped while this job was RUNNING; its output was never verified, "
                                 "so it is marked FAILED. Retry the job via POST /jobs/{id}/retry.")
            counts["interrupted"] += 1
            for plan in db.query(VisualPlan).filter(VisualPlan.job_id == job.job_id, VisualPlan.status == "BUILDING"):
                plan.status = "FAILED"
                plan.error_code = "JOB_INTERRUPTED"
                plan.error_message = "Build interrupted by service restart."
            for gen in db.query(GenerationRecord).filter(GenerationRecord.job_id == job.job_id,
                                                         GenerationRecord.status == "PENDING"):
                gen.status = "FAILED"
                gen.error_code = "JOB_INTERRUPTED"
        for job in db.query(Job).filter(Job.status == "QUEUED").all():
            if job.cancel_requested:
                job.status = "CANCELLED"
                job.completed_at = _now()
            else:
                requeue.append(job.job_id)
        db.commit()
    finally:
        db.close()
    for jid in requeue:
        get_job_queue().submit(jid)
    counts["requeued"] = len(requeue)
    if counts["interrupted"] or counts["requeued"]:
        logger.warning("Job recovery: %s", counts)
    return counts
